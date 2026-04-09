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

import hashlib
import json
import os
import tempfile
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import structlog
from aiohttp import ClientSession
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
        start_generation: int | None = None,
        artifacts_root: Path | None = None,
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
        self.artifacts_root = artifacts_root or Path(
            os.environ.get("CAMBRIAN_ARTIFACTS_ROOT", "../cambrian-artifacts")
        )

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
        # Accumulated SpecDiff objects — one per successful mutation — for entanglement monitoring.
        self._spec_diffs: list[SpecDiff] = []

        # Reload persisted observations (crash recovery). This must happen before
        # setting _generation_counter so we can derive the counter from saved state.
        self._obs_path = self.artifacts_root / "bo-observations.jsonl"
        self._reload_observations()

        # Determine starting generation number.
        # If start_generation is explicitly provided, use it.
        # Otherwise, derive from reloaded observations (crash recovery path).
        # The async auto-detection from /versions is done in run() before the loop starts.
        if start_generation is not None:
            self._generation_counter = start_generation
            self.start_generation = start_generation
        elif self.observations:
            # Derive from the last persisted observation's generation counter.
            # The observations don't directly store the counter, so we fall back to
            # counting total campaigns run (mini + full per observation).
            total_used = sum(
                self.mini_n + (self.full_n if o.full_viability is not None else 0)
                for o in self.observations
            )
            self._generation_counter = 1 + total_used
            self.start_generation = self._generation_counter
        else:
            # Defer to auto-detection in run()
            self._generation_counter = 1
            self.start_generation = 1

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
        # Auto-detect start generation from /versions if not set from persisted state.
        if not self.observations and self._generation_counter == 1:
            detected = await self._detect_start_generation()
            if detected > 1:
                self._generation_counter = detected
                self.start_generation = detected
                log.info("bo_loop_generation_auto_detected", start_generation=detected)

        log.info(
            "bo_loop_start",
            budget=self.budget,
            sections=len(self.section_names),
            mini_n=self.mini_n,
            full_n=self.full_n,
            start_generation=self._generation_counter,
            resuming=len(self.observations) > 0,
        )

        # Evaluate the base spec first (establishes the baseline) only on fresh
        # runs. On resumed runs this observation is already persisted.
        if not self.observations:
            base_obs = await self._evaluate_spec(
                self.base_spec_text,
                features=extract_features(
                    self.base_spec_text, self.section_names, self.base_spec_text
                ),
                target_section=None,
            )
            if base_obs:
                self.observations.append(base_obs)
                self._record_observation(base_obs)
        else:
            log.info("bo_loop_resume_skip_base_eval", observations=len(self.observations))

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

    async def _detect_start_generation(self) -> int:
        """Query GET /versions and return max(generation) + 1, or 1 if empty."""
        try:
            async with ClientSession() as s:
                async with s.get(f"{self.supervisor_url}/versions") as resp:
                    if resp.status == 200:
                        records: list[dict[str, Any]] = await resp.json()
                        if records:
                            return max(r.get("generation", 0) for r in records) + 1
        except Exception:
            pass
        return 1

    def _reload_observations(self) -> None:
        """Reload persisted observations from bo-observations.jsonl (crash recovery)."""
        if not self._obs_path.exists():
            return
        loaded = 0
        try:
            for line in self._obs_path.read_text().splitlines():
                line = line.strip()
                if not line:
                    continue
                data = json.loads(line)
                obs = BOObservation(
                    spec_hash=data["spec_hash"],
                    spec_text=data["spec_text"],
                    features=data["features"],
                    mini_viability=data["mini_viability"],
                    full_viability=data.get("full_viability"),
                    mini_summary=data.get("mini_summary", {}),
                    full_summary=data.get("full_summary"),
                    target_section=data.get("target_section"),
                )
                self.observations.append(obs)
                # Re-tell the optimizer so the GP is reconstructed from saved data.
                score = obs.full_viability if obs.full_viability is not None else obs.mini_viability
                self.optimizer.tell(obs.features, -score)
                loaded += 1
        except Exception as exc:
            log.warning("bo_loop_reload_failed", path=str(self._obs_path), error=str(exc))
            # Don't crash — start fresh if the file is corrupt.
            self.observations.clear()
            return
        if loaded:
            log.info("bo_loop_resumed", observations_loaded=loaded, path=str(self._obs_path))

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
        spec_hash = f"sha256:{hashlib.sha256(spec_text.encode()).hexdigest()}"

        # Write spec to a temp file for the campaign runner
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
        """Update the BO optimizer with the observed viability score and persist to JSONL."""
        score = obs.full_viability if obs.full_viability is not None else obs.mini_viability
        # skopt minimizes; we maximize viability_rate, so negate it.
        self.optimizer.tell(obs.features, -score)

        # Persist to bo-observations.jsonl for crash recovery.
        try:
            self._obs_path.parent.mkdir(parents=True, exist_ok=True)
            record = asdict(obs)
            record["timestamp"] = datetime.now(UTC).isoformat()
            with self._obs_path.open("a") as f:
                f.write(json.dumps(record) + "\n")
        except Exception as exc:
            log.warning("bo_loop_persist_failed", path=str(self._obs_path), error=str(exc))

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
