"""Single-objective Bayesian Optimization loop over spec mutations.

Implements M2 Stage 1: maximize viability_rate across spec variants using a
Gaussian Process surrogate and Expected Improvement acquisition function.

Design (adversarial review §4):
  - Stage 1 (~20-30 campaigns): single-objective BO, maximize viability rate.
  - Each spec variant is represented as a feature vector of per-section diff
    magnitudes relative to the base spec.
  - skopt.Optimizer wraps the GP-EI loop; we translate suggested feature
    vectors into LLM mutation targets.
  - Mini-campaigns (n=2) screen candidates before committing to full campaigns.

Reference: cambrian-9ic (campaign runner), cambrian-evw (spec diff),
           cambrian-7cc (type1 mutator), cambrian-3sb (mini-campaign screening).
"""

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import structlog
from skopt import Optimizer
from skopt.space import Real

from .adaptive_tests import expire_old_tests, generate_adaptive_tests
from .campaign import run_campaign
from .entanglement import EntanglementReport, compute_entanglement_report, entanglement_alert
from .spec_diff import SpecDiff, diff_spec, parse_sections
from .spec_grammar import evolvable_sections
from .spec_mutator import propose_target_section, type1_mutate

log = structlog.get_logger(component="bo_loop")

# BO hyperparameters (all overridable via env vars for experiment tuning).
_BO_BUDGET = int(os.environ.get("CAMBRIAN_BO_BUDGET", "20"))
_MINI_N = int(os.environ.get("CAMBRIAN_MINI_CAMPAIGN_N", "2"))
_FULL_N = int(os.environ.get("CAMBRIAN_CAMPAIGN_LENGTH", "5"))
# Minimum viability in the mini-campaign to proceed to a full campaign.
_SCREEN_THRESHOLD = int(os.environ.get("CAMBRIAN_SCREEN_THRESHOLD", "1"))
# Number of random initial points before the GP kicks in.
_N_INITIAL_POINTS = int(os.environ.get("CAMBRIAN_BO_INITIAL_POINTS", "5"))
# Lines-changed range per section dimension (symmetric: [-MAX, MAX]).
_DIM_RANGE = float(os.environ.get("CAMBRIAN_BO_DIM_RANGE", "100.0"))


# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------


@dataclass
class BOObservation:
    """One evaluated point in spec space."""

    spec_hash: str
    spec_text: str
    features: list[float]  # per-section diff magnitude from base spec
    mini_viability: float  # viability_rate from mini-campaign
    full_viability: float | None  # viability_rate from full campaign (None if screened out)
    mini_summary: dict[str, Any]
    full_summary: dict[str, Any] | None
    target_section: str | None


@dataclass
class BOResult:
    """Summary of a completed BO run."""

    best_spec_hash: str
    best_viability: float
    best_spec_text: str
    observations: list[BOObservation]
    iterations: int
    budget_used: int


# ---------------------------------------------------------------------------
# Feature extraction
# ---------------------------------------------------------------------------


def extract_features(spec_text: str, section_names: list[str], base_spec: str) -> list[float]:
    """Compute a feature vector for spec_text relative to base_spec.

    Each dimension = lines_in_section(spec_text) - lines_in_section(base_spec).
    Positive = section expanded, negative = section contracted.
    Sections absent in spec_text get delta = -(lines in base).
    """
    current_sections = parse_sections(spec_text)
    base_sections = parse_sections(base_spec)
    features = []
    for name in section_names:
        current_lines = len(current_sections.get(name, "").splitlines())
        base_lines = len(base_sections.get(name, "").splitlines())
        features.append(float(current_lines - base_lines))
    return features


def decode_suggestion(
    suggested_x: list[float],
    section_names: list[str],
    campaign_summary: dict[str, Any],
) -> str | None:
    """Translate a BO-suggested feature vector into a target section name.

    The dimension with the largest magnitude is the section the BO wants to
    change most. We propose that section as the mutation target.

    Returns the section name, or falls back to propose_target_section.
    """
    if not suggested_x or not section_names:
        return None

    # Find the section with the largest magnitude (BO wants to change it most)
    idx = max(range(len(suggested_x)), key=lambda i: abs(suggested_x[i]))
    bo_candidate = section_names[idx]

    # If BO wants to change a section by essentially nothing, fall back
    if abs(suggested_x[idx]) < 1.0:
        return propose_target_section(campaign_summary, section_names)

    return bo_candidate


# ---------------------------------------------------------------------------
# BO loop
# ---------------------------------------------------------------------------


class SpecBOLoop:
    """Bayesian Optimization loop over spec mutations.

    Usage:
        loop = SpecBOLoop(base_spec_path, supervisor_url="http://localhost:8400")
        result = await loop.run()
    """

    def __init__(
        self,
        base_spec_path: Path,
        supervisor_url: str | None = None,
        budget: int = _BO_BUDGET,
        mini_n: int = _MINI_N,
        full_n: int = _FULL_N,
        screen_threshold: int = _SCREEN_THRESHOLD,
        n_initial_points: int = _N_INITIAL_POINTS,
        start_generation: int = 1,
    ) -> None:
        self.base_spec_text = base_spec_path.read_text()
        self.base_spec_path = base_spec_path
        self.supervisor_url = supervisor_url or os.environ.get(
            "CAMBRIAN_SUPERVISOR_URL", "http://localhost:8400"
        )
        self.budget = budget
        self.mini_n = mini_n
        self.full_n = full_n
        self.screen_threshold = screen_threshold
        self.start_generation = start_generation

        # Section names that may be mutated (excludes FROZEN sections)
        self.section_names: list[str] = evolvable_sections(self.base_spec_text)

        # skopt Optimizer: one Real dimension per evolvable section
        dimensions = [Real(-_DIM_RANGE, _DIM_RANGE, name=s) for s in self.section_names]
        self.optimizer = Optimizer(
            dimensions=dimensions,
            base_estimator="GP",
            acq_func="EI",
            n_initial_points=n_initial_points,
            random_state=42,
        )

        self.observations: list[BOObservation] = []
        self._generation_counter = start_generation
        # Accumulated SpecDiff objects — one per successful mutation — for entanglement monitoring.
        self._spec_diffs: list[SpecDiff] = []

    async def run(self) -> BOResult:
        """Execute the BO loop up to self.budget iterations.

        Each iteration:
          1. BO suggests a feature vector (= target section direction)
          2. Decode to a section name
          3. Call type1_mutate to generate the mutated spec
          4. Run mini-campaign (n=2) for fast screening
          5. If mini passes, run full campaign (n=5) to score the spec
          6. Record observation, update BO
        """
        log.info(
            "bo_loop_start",
            budget=self.budget,
            sections=len(self.section_names),
            mini_n=self.mini_n,
            full_n=self.full_n,
        )

        # Evaluate the base spec first (establishes the baseline)
        base_obs = await self._evaluate_spec(
            self.base_spec_text,
            features=extract_features(self.base_spec_text, self.section_names, self.base_spec_text),
            target_section=None,
        )
        if base_obs:
            self.observations.append(base_obs)
            self._record_observation(base_obs)

        for iteration in range(1, self.budget + 1):
            campaign_index = iteration - 1
            log.info("bo_loop_iteration", iteration=iteration, budget=self.budget)

            # Expire adaptive tests that have aged out
            expire_old_tests(campaign_index)

            # Ask BO for the next point
            suggested_x: list[float] = self.optimizer.ask()

            # Use most recent full campaign summary for context (or empty if none yet)
            last_full = next(
                (o.full_summary for o in reversed(self.observations) if o.full_summary),
                {},
            )

            # Decode suggestion to a mutation target
            target_section = decode_suggestion(suggested_x, self.section_names, last_full)

            # Generate mutation
            mutated = await type1_mutate(
                self.base_spec_text,
                last_full or {},
                target_section=target_section,
            )
            if mutated is None:
                log.warning("bo_loop_mutation_failed", iteration=iteration)
                # Tell BO a poor score so it explores elsewhere.
                # skopt minimizes, so 1.0 = worst (negated viability).
                self.optimizer.tell(suggested_x, 1.0)
                continue

            features = extract_features(mutated, self.section_names, self.base_spec_text)

            # Accumulate diff for entanglement monitoring
            self._spec_diffs.append(diff_spec(self.base_spec_text, mutated))

            obs = await self._evaluate_spec(
                mutated,
                features=features,
                target_section=target_section,
                campaign_index=campaign_index,
            )
            if obs:
                self.observations.append(obs)
                self._record_observation(obs)
            else:
                # Mutation was screened out — tell BO it scored 0
                self.optimizer.tell(features, 1.0)

            # Check entanglement every 5 iterations once we have enough data
            if len(self._spec_diffs) >= 2 and iteration % 5 == 0:
                self._check_entanglement(mutated)

        return self._make_result()

    def _check_entanglement(self, current_spec_text: str) -> None:
        """Compute and log the entanglement report; alert if is_entangling."""
        report: EntanglementReport = compute_entanglement_report(
            self._spec_diffs, spec_text=current_spec_text
        )
        log.info(
            "entanglement_report",
            mutation_count=report.mutation_count,
            mean_sections_per_mutation=report.mean_sections_per_mutation,
            entanglement_trend=report.entanglement_trend,
            is_entangling=report.is_entangling,
        )
        alert = entanglement_alert(report)
        if alert:
            log.warning("entanglement_alert", message=alert)

    async def _evaluate_spec(
        self,
        spec_text: str,
        features: list[float],
        target_section: str | None,
        campaign_index: int = 0,
    ) -> BOObservation | None:
        """Run mini-campaign (+ optionally full campaign) for one spec variant."""
        import hashlib

        spec_hash = f"sha256:{hashlib.sha256(spec_text.encode()).hexdigest()}"

        # Write spec to a temp file for the campaign runner
        import tempfile

        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".md", delete=False, prefix="spec-candidate-"
        ) as f:
            f.write(spec_text)
            tmp_path = Path(f.name)

        try:
            # Mini-campaign
            mini_summary = await run_campaign(
                spec_path=tmp_path,
                n=self.mini_n,
                supervisor_url=self.supervisor_url,
                start_generation=self._generation_counter,
            )
            self._generation_counter += self.mini_n

            mini_viability = mini_summary.get("viability_rate", 0.0)
            viable_count = round(mini_viability * self.mini_n)

            if viable_count < self.screen_threshold:
                log.info(
                    "bo_loop_screened_out",
                    spec_hash=spec_hash[:12],
                    mini_viability=mini_viability,
                )
                return BOObservation(
                    spec_hash=spec_hash,
                    spec_text=spec_text,
                    features=features,
                    mini_viability=mini_viability,
                    full_viability=None,
                    mini_summary=mini_summary,
                    full_summary=None,
                    target_section=target_section,
                )

            # Full campaign
            full_summary = await run_campaign(
                spec_path=tmp_path,
                n=self.full_n,
                supervisor_url=self.supervisor_url,
                start_generation=self._generation_counter,
            )
            self._generation_counter += self.full_n
            full_viability = full_summary.get("viability_rate", 0.0)

            # After a failed campaign, generate adaptive tests probing the failure mode
            if full_viability < 1.0:
                await generate_adaptive_tests(
                    campaign_summary=full_summary,
                    spec_text=spec_text,
                    campaign_index=campaign_index,
                )

            log.info(
                "bo_loop_full_campaign_done",
                spec_hash=spec_hash[:12],
                full_viability=full_viability,
                target_section=target_section,
            )
            return BOObservation(
                spec_hash=spec_hash,
                spec_text=spec_text,
                features=features,
                mini_viability=mini_viability,
                full_viability=full_viability,
                mini_summary=mini_summary,
                full_summary=full_summary,
                target_section=target_section,
            )
        finally:
            tmp_path.unlink(missing_ok=True)

    def _record_observation(self, obs: BOObservation) -> None:
        """Update the BO optimizer with the observed viability score."""
        score = obs.full_viability if obs.full_viability is not None else obs.mini_viability
        # skopt minimizes; we maximize viability_rate, so negate it.
        self.optimizer.tell(obs.features, -score)

    def _make_result(self) -> BOResult:
        """Assemble the BOResult from all observations."""
        best = max(
            self.observations,
            key=lambda o: o.full_viability or o.mini_viability,
            default=None,
        )
        if best is None:
            return BOResult(
                best_spec_hash="",
                best_viability=0.0,
                best_spec_text=self.base_spec_text,
                observations=[],
                iterations=0,
                budget_used=0,
            )
        return BOResult(
            best_spec_hash=best.spec_hash,
            best_viability=best.full_viability or best.mini_viability,
            best_spec_text=best.spec_text,
            observations=self.observations,
            iterations=len(self.observations),
            budget_used=self._generation_counter - self.start_generation,
        )
