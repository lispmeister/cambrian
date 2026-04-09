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
import shutil
import uuid
from pathlib import Path
from typing import Any

import structlog
from aiohttp import ClientSession

from . import prime_runner
from .supervisor import _make_error_viability

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
    artifacts_root: Path | None = None,
) -> dict[str, Any]:
    """Run N generations against one spec variant and return a CampaignSummary.

    For each generation:
      1. Call prime_runner.generate_artifact() — invoke LLM to produce the artifact.
      2. POST /spawn — hand the artifact to the Supervisor's Test Rig.
      3. Poll until tested, then call /promote or /rollback.

    Args:
        spec_path: Path to the spec file to use for this campaign.
        n: Number of generations to run (default: CAMBRIAN_CAMPAIGN_LENGTH env var, else 5).
        supervisor_url: Supervisor base URL (default: CAMBRIAN_SUPERVISOR_URL env var).
        start_generation: Generation number to start from (default: 1).
        artifacts_root: Path to the artifacts repo root (default: CAMBRIAN_ARTIFACTS_ROOT).

    Returns:
        CampaignSummary dict as produced by compute_campaign_summary().
    """
    if n is None:
        n = int(os.environ.get("CAMBRIAN_CAMPAIGN_LENGTH", "5"))
    if supervisor_url is None:
        supervisor_url = os.environ.get("CAMBRIAN_SUPERVISOR_URL", "http://localhost:8400")
    if artifacts_root is None:
        artifacts_root = Path(os.environ.get("CAMBRIAN_ARTIFACTS_ROOT", "../cambrian-artifacts"))

    campaign_id = f"campaign-{uuid.uuid4().hex[:8]}"
    spec_text = spec_path.read_text()
    spec_hash = _hash_file(spec_path)

    log.info("campaign_start", campaign_id=campaign_id, spec=str(spec_path), n=n)

    # Fetch generation history once for context
    history: list[dict[str, Any]] = []
    try:
        async with ClientSession() as s:
            async with s.get(f"{supervisor_url}/versions") as resp:
                if resp.status == 200:
                    history = await resp.json()
    except Exception:
        pass

    records: list[dict[str, Any]] = []
    failed_context: dict[str, Any] | None = None
    prev_artifact_dir: Path | None = None  # kept alive for failed_context prompt

    async with ClientSession() as session:
        for i in range(n):
            generation = start_generation + i
            # campaigns/{campaign_id}/gen-{N}/ — no double-prefix, isolated from M1 artifacts
            artifact_rel = f"campaigns/{campaign_id}/gen-{generation}"
            parent = generation - 1

            # Step 1: Generate artifact via LLM (with diagnostics from previous failure)
            artifact_dir: Path | None = None
            try:
                artifact_dir, _, _ = await prime_runner.generate_artifact(
                    spec_text=spec_text,
                    generation=generation,
                    parent=parent,
                    artifacts_root=artifacts_root,
                    history=history,
                    artifact_rel=artifact_rel,
                    failed_context=failed_context,
                )
            except Exception as e:
                log.error("generate_artifact_failed", generation=generation, error=str(e))
                records.append(_error_record(generation, f"generate_artifact failed: {e}"))
                continue
            finally:
                # Now safe to clean up the PREVIOUS failed artifact (the prompt has been built)
                if prev_artifact_dir is not None and prev_artifact_dir.exists():
                    shutil.rmtree(prev_artifact_dir, ignore_errors=True)
                    log.info(
                        "artifact_dir_cleaned",
                        generation=generation - 1,
                        path=str(prev_artifact_dir),
                    )
                prev_artifact_dir = None

            # Step 2: Spawn the test rig on the generated artifact
            record = await _run_one_generation(
                session=session,
                supervisor_url=supervisor_url,
                generation=generation,
                spec_hash=spec_hash,
                artifact_path=artifact_rel,
                campaign_id=campaign_id,
            )

            # Step 3: Build failed_context for the next generation if this one failed
            viable = record.get("viability", {}).get("status") == "viable"
            if not viable:
                viability = record.get("viability", {})
                failed_context = {
                    "diagnostics": viability.get("diagnostics", {}),
                    "artifact_dir": artifact_dir,
                }
                # Keep artifact_dir alive until the next gen's prompt is built
                prev_artifact_dir = artifact_dir
            else:
                failed_context = None
                prev_artifact_dir = None

            records.append(record)
            # Update history for next generation's context
            history.append(record)
            log.info(
                "campaign_generation_done",
                campaign_id=campaign_id,
                generation=generation,
                viable=viable,
            )

    # Clean up any remaining failed artifact from the last generation
    if prev_artifact_dir is not None and prev_artifact_dir.exists():
        shutil.rmtree(prev_artifact_dir, ignore_errors=True)
        log.info(
            "artifact_dir_cleaned", generation=start_generation + n - 1, path=str(prev_artifact_dir)
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
    # Use campaign-id filter to avoid scanning unrelated records.
    versions_url = f"{supervisor_url}/versions?campaign-id={campaign_id}"
    deadline = asyncio.get_event_loop().time() + CAMPAIGN_GENERATION_TIMEOUT
    while asyncio.get_event_loop().time() < deadline:
        await asyncio.sleep(CAMPAIGN_POLL_INTERVAL)
        async with session.get(versions_url) as resp:
            if resp.status != 200:
                continue
            records: list[dict[str, Any]] = await resp.json()
        for record in records:
            if record.get("generation") == generation:
                if record.get("outcome") != "in_progress":
                    record = await _promote_or_rollback(session, supervisor_url, generation, record)
                    return record
                break

    log.warning("campaign_generation_timeout", generation=generation)
    return _error_record(generation, "timeout waiting for generation to complete")


async def _promote_or_rollback(
    session: ClientSession,
    supervisor_url: str,
    generation: int,
    record: dict[str, Any],
) -> dict[str, Any]:
    """Call /promote or /rollback based on viability, return the updated record.

    If the record is already in a terminal state (promoted/failed/timeout), skip.
    """
    outcome = record.get("outcome")
    if outcome in ("promoted", "failed", "timeout", "in_progress"):
        return record

    viable = record.get("viability", {}).get("status") == "viable"
    endpoint = "/promote" if viable else "/rollback"
    try:
        async with session.post(
            f"{supervisor_url}{endpoint}", json={"generation": generation}
        ) as resp:
            if resp.status != 200:
                text = await resp.text()
                log.warning(
                    "promote_rollback_failed",
                    generation=generation,
                    endpoint=endpoint,
                    status=resp.status,
                    body=text[:200],
                )
                return record
    except Exception as e:
        log.warning(
            "promote_rollback_error",
            generation=generation,
            endpoint=endpoint,
            error=str(e),
        )
        return record

    log.info(
        "generation_finalized",
        generation=generation,
        endpoint=endpoint,
        viable=viable,
    )
    # Return updated record reflecting the terminal outcome
    record = dict(record)
    record["outcome"] = "promoted" if viable else "failed"
    return record


def _error_record(generation: int, reason: str) -> dict[str, Any]:
    """Synthesise a failed generation record for error cases."""
    return {
        "generation": generation,
        "outcome": "failed",
        "viability": _make_error_viability(generation, reason),
    }


def _hash_file(path: Path) -> str:
    """Return sha256:<hex> hash of a file."""
    h = hashlib.sha256(path.read_bytes())
    return f"sha256:{h.hexdigest()}"


# ---------------------------------------------------------------------------
