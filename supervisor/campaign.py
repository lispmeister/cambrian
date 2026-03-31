"""Campaign runner — run N generations against one spec variant and summarise results.

A campaign is the unit of spec selection in M2: a sequence of N generation attempts against
a single spec variant, producing a CampaignSummary that selection policies can compare.

This module has two layers:
  compute_campaign_summary(records) — pure function, no I/O, fully testable
  run_campaign(spec_path, n, supervisor_url) — async orchestration via Supervisor HTTP API
"""

import asyncio
import hashlib
import os
import uuid
from pathlib import Path
from typing import Any

import structlog
from aiohttp import ClientSession

log = structlog.get_logger(component="campaign")

CAMPAIGN_POLL_INTERVAL = float(os.environ.get("CAMBRIAN_CAMPAIGN_POLL_INTERVAL", "2.0"))
CAMPAIGN_GENERATION_TIMEOUT = int(os.environ.get("CAMBRIAN_CONTAINER_TIMEOUT", "600"))

# Stage order used for max-stage computation.
_STAGE_ORDER = ["manifest", "build", "test", "start", "health"]


# ---------------------------------------------------------------------------
# Pure summary computation
# ---------------------------------------------------------------------------


def compute_campaign_summary(records: list[dict[str, Any]]) -> dict[str, Any]:
    """Compute a CampaignSummary from a list of generation records.

    Fields produced (per CAMBRIAN-SPEC-005 §Campaign):
      viability_rate              float  viable / total
      fitness_mean                dict   mean of each numeric fitness key
      fitness_trend               float  linear regression slope of [0,1] viability outcomes
      failure_distribution        dict   {failure_stage: count}
      stages_completed_distribution dict  {max_stage_reached: count}
      generation_count            int    total generations in campaign
    """
    if not records:
        return {
            "viability_rate": 0.0,
            "fitness_mean": {},
            "fitness_trend": 0.0,
            "failure_distribution": {},
            "stages_completed_distribution": {},
            "generation_count": 0,
        }

    viable_outcomes = []
    fitness_accum: dict[str, list[float]] = {}
    failure_distribution: dict[str, int] = {}
    stages_completed_distribution: dict[str, int] = {}

    for record in records:
        viability = record.get("viability") or {}
        status = viability.get("status", "non-viable")
        is_viable = status == "viable"
        viable_outcomes.append(1 if is_viable else 0)

        # Accumulate fitness dimensions (numeric values only; skip lists/dicts).
        fitness = viability.get("fitness") or {}
        for key, val in fitness.items():
            if isinstance(val, (int, float)) and not isinstance(val, bool):
                fitness_accum.setdefault(key, []).append(float(val))

        # failure_distribution
        failure_stage = viability.get("failure_stage", "none")
        failure_distribution[failure_stage] = failure_distribution.get(failure_stage, 0) + 1

        # stages_completed_distribution — use furthest stage in stages_completed list.
        stages_completed = fitness.get("stages_completed") or []
        max_stage = _max_stage(stages_completed)
        stages_completed_distribution[max_stage] = (
            stages_completed_distribution.get(max_stage, 0) + 1
        )

    n = len(records)
    viability_rate = round(sum(viable_outcomes) / n, 4)
    fitness_mean = {k: round(sum(v) / len(v), 4) for k, v in fitness_accum.items()}
    fitness_trend = _linear_slope(viable_outcomes)

    return {
        "viability_rate": viability_rate,
        "fitness_mean": fitness_mean,
        "fitness_trend": round(fitness_trend, 6),
        "failure_distribution": failure_distribution,
        "stages_completed_distribution": stages_completed_distribution,
        "generation_count": n,
    }


def _max_stage(stages_completed: list[str]) -> str:
    """Return the furthest stage reached, or 'none' if the list is empty."""
    best_idx = -1
    best = "none"
    for s in stages_completed:
        try:
            idx = _STAGE_ORDER.index(s)
        except ValueError:
            continue
        if idx > best_idx:
            best_idx = idx
            best = s
    return best


def _linear_slope(values: list[int | float]) -> float:
    """Return the slope of a least-squares linear regression on the values.

    Uses the closed-form formula for slope = (n*Σxy - Σx*Σy) / (n*Σx² - (Σx)²).
    Returns 0.0 for fewer than 2 points.
    """
    n = len(values)
    if n < 2:
        return 0.0
    xs = list(range(n))
    sum_x = sum(xs)
    sum_y = sum(values)
    sum_xy = sum(x * y for x, y in zip(xs, values))
    sum_x2 = sum(x * x for x in xs)
    denom = n * sum_x2 - sum_x * sum_x
    if denom == 0:
        return 0.0
    return (n * sum_xy - sum_x * sum_y) / denom


# ---------------------------------------------------------------------------
# Async campaign orchestration
# ---------------------------------------------------------------------------


async def run_campaign(
    spec_path: Path,
    n: int | None = None,
    supervisor_url: str | None = None,
    start_generation: int = 1,
) -> dict[str, Any]:
    """Run N generations against one spec variant and return a CampaignSummary.

    Args:
        spec_path: Path to the spec file to use for this campaign.
        n: Number of generations to run (default: CAMBRIAN_CAMPAIGN_LENGTH env var, else 5).
        supervisor_url: Supervisor base URL (default: CAMBRIAN_SUPERVISOR_URL env var).
        start_generation: Generation number to start from (default: 1).

    Returns:
        CampaignSummary dict as produced by compute_campaign_summary().
    """
    if n is None:
        n = int(os.environ.get("CAMBRIAN_CAMPAIGN_LENGTH", "5"))
    if supervisor_url is None:
        supervisor_url = os.environ.get("CAMBRIAN_SUPERVISOR_URL", "http://localhost:8400")

    campaign_id = f"campaign-{uuid.uuid4().hex[:8]}"
    spec_hash = _hash_file(spec_path)

    log.info("campaign_start", campaign_id=campaign_id, spec=str(spec_path), n=n)

    records: list[dict[str, Any]] = []
    async with ClientSession() as session:
        for i in range(n):
            generation = start_generation + i
            record = await _run_one_generation(
                session=session,
                supervisor_url=supervisor_url,
                generation=generation,
                spec_hash=spec_hash,
                artifact_path=f"campaign-{campaign_id}-gen-{generation}",
                campaign_id=campaign_id,
            )
            records.append(record)
            log.info(
                "campaign_generation_done",
                campaign_id=campaign_id,
                generation=generation,
                viable=record.get("viability", {}).get("status") == "viable",
            )

    summary = compute_campaign_summary(records)
    summary["campaign_id"] = campaign_id
    summary["spec_hash"] = spec_hash
    log.info(
        "campaign_complete",
        campaign_id=campaign_id,
        viability_rate=summary["viability_rate"],
        fitness_trend=summary["fitness_trend"],
    )
    return summary


async def _run_one_generation(
    *,
    session: ClientSession,
    supervisor_url: str,
    generation: int,
    spec_hash: str,
    artifact_path: str,
    campaign_id: str,
) -> dict[str, Any]:
    """Spawn one generation, poll until done, return the completed generation record."""
    body = {
        "generation": generation,
        "spec-hash": spec_hash,
        "artifact-path": artifact_path,
        "campaign-id": campaign_id,
    }

    async with session.post(f"{supervisor_url}/spawn", json=body) as resp:
        if resp.status != 200:
            text = await resp.text()
            log.error("spawn_failed", generation=generation, status=resp.status, body=text)
            return _error_record(generation, f"spawn HTTP {resp.status}: {text[:200]}")

    # Poll until the record transitions out of in_progress.
    deadline = asyncio.get_event_loop().time() + CAMPAIGN_GENERATION_TIMEOUT
    while asyncio.get_event_loop().time() < deadline:
        await asyncio.sleep(CAMPAIGN_POLL_INTERVAL)
        async with session.get(f"{supervisor_url}/versions") as resp:
            if resp.status != 200:
                continue
            records: list[dict[str, Any]] = await resp.json()
        for record in records:
            if record.get("generation") == generation:
                if record.get("outcome") != "in_progress":
                    return record
                break

    log.warning("campaign_generation_timeout", generation=generation)
    return _error_record(generation, "timeout waiting for generation to complete")


def _error_record(generation: int, reason: str) -> dict[str, Any]:
    """Synthesise a failed generation record for error cases."""
    return {
        "generation": generation,
        "outcome": "failed",
        "viability": {
            "status": "non-viable",
            "failure_stage": "none",
            "summary": reason,
            "fitness": {},
        },
    }


def _hash_file(path: Path) -> str:
    """Return sha256:<hex> hash of a file."""
    h = hashlib.sha256(path.read_bytes())
    return f"sha256:{h.hexdigest()}"


# ---------------------------------------------------------------------------
# Mini-campaign screening (cambrian-3sb)
# ---------------------------------------------------------------------------

# Default mini-campaign length (2 generations).
_MINI_CAMPAIGN_N = int(os.environ.get("CAMBRIAN_MINI_CAMPAIGN_N", "2"))


async def screen_mutation(
    spec_path: Path,
    supervisor_url: str | None = None,
    n: int = _MINI_CAMPAIGN_N,
    start_generation: int = 1,
) -> tuple[bool, dict[str, Any]]:
    """Run a mini-campaign (n=2 by default) to screen a spec mutation.

    Replaces the gameable dual-model LLM screener (adversarial review §5).
    The Test Rig is the judge: if at least one of the n short generations
    achieves Tier 0 viability, the mutation passes screening and is eligible
    for a full campaign.

    Args:
        spec_path: Path to the candidate mutated spec file.
        supervisor_url: Supervisor base URL.
        n: Number of generations for the mini-campaign (default: 2).
        start_generation: Generation number to start from.

    Returns:
        (passes, mini_summary) where passes=True if viability_rate > 0.
    """
    mini_summary = await run_campaign(
        spec_path=spec_path,
        n=n,
        supervisor_url=supervisor_url,
        start_generation=start_generation,
    )
    passes = mini_summary.get("viability_rate", 0.0) > 0.0
    log.info(
        "screen_mutation_result",
        spec=str(spec_path),
        viability_rate=mini_summary.get("viability_rate"),
        passes=passes,
    )
    return passes, mini_summary
