"""Run a short self-replication campaign against the base spec.

Uses prime_runner to generate artifacts and Supervisor/Test Rig to verify them.
Artifacts are written under cambrian-artifacts/gen-0-campaigns/.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import shutil
import sys
import time
from pathlib import Path
from typing import Any

import aiohttp
import structlog

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from supervisor import prime_runner

log = structlog.get_logger(component="gen0_campaign")


def _hash_file(path: Path) -> str:
    import hashlib

    h = hashlib.sha256(path.read_bytes())
    return f"sha256:{h.hexdigest()}"


def _next_generation(artifacts_root: Path) -> int:
    history_file = artifacts_root / "generations.json"
    if not history_file.exists():
        return 1
    data = json.loads(history_file.read_text())
    if not data:
        return 1
    last_gen = max(int(d.get("generation", 0)) for d in data)
    return last_gen + 1


async def _fetch_versions(
    session: aiohttp.ClientSession, supervisor_url: str
) -> list[dict[str, Any]]:
    async with session.get(f"{supervisor_url}/versions") as resp:
        if resp.status != 200:
            raise RuntimeError(f"/versions returned {resp.status}")
        return await resp.json()


async def _wait_for_tested(
    session: aiohttp.ClientSession,
    supervisor_url: str,
    generation: int,
    timeout_s: int = 900,
    poll_s: float = 2.0,
) -> dict[str, Any]:
    start = time.time()
    while time.time() - start < timeout_s:
        records = await _fetch_versions(session, supervisor_url)
        record = next((r for r in records if r.get("generation") == generation), None)
        if record and record.get("outcome") == "tested":
            return record
        await asyncio.sleep(poll_s)
    raise TimeoutError(f"generation {generation} did not reach tested state")


async def _run_one_generation(
    session: aiohttp.ClientSession,
    supervisor_url: str,
    artifacts_root: Path,
    spec_text: str,
    spec_hash: str,
    generation: int,
    parent: int,
    campaign_id: str,
    model: str,
    failed_context: dict[str, Any] | None = None,
) -> tuple[dict[str, Any], Path | None]:
    """Run one generation. Returns (record, artifact_dir).

    artifact_dir is returned so the caller can keep it alive for failed_context
    prompt building in the next generation, then clean it up.
    """
    artifact_rel = f"gen-0-campaigns/{campaign_id}/gen-{generation}"
    artifact_dir, _, _ = await prime_runner.generate_artifact(
        spec_text=spec_text,
        generation=generation,
        parent=parent,
        artifacts_root=artifacts_root,
        history=await _fetch_versions(session, supervisor_url),
        artifact_rel=artifact_rel,
        model=model,
        failed_context=failed_context,
    )

    log.info("artifact_generated", generation=generation, path=str(artifact_dir))

    spawn_body = {
        "generation": generation,
        "artifact-path": artifact_rel,
        "spec-hash": spec_hash,
        "campaign-id": campaign_id,
    }
    async with session.post(f"{supervisor_url}/spawn", json=spawn_body) as resp:
        if resp.status != 200:
            error = await resp.text()
            raise RuntimeError(f"/spawn failed ({resp.status}): {error}")

    record = await _wait_for_tested(session, supervisor_url, generation)
    viable = record.get("viability", {}).get("status") == "viable"

    if not viable:
        # Preserve failed artifact for analysis before rollback
        failed_dir = (
            artifacts_root / "gen-0-campaigns" / campaign_id / "failed" / f"gen-{generation}"
        )
        if artifact_dir.exists() and not failed_dir.exists():
            failed_dir.parent.mkdir(parents=True, exist_ok=True)
            shutil.copytree(artifact_dir, failed_dir)

    endpoint = "promote" if viable else "rollback"
    async with session.post(
        f"{supervisor_url}/{endpoint}", json={"generation": generation}
    ) as resp:
        if resp.status != 200:
            error = await resp.text()
            raise RuntimeError(f"/{endpoint} failed ({resp.status}): {error}")

    log.info("generation_finalized", generation=generation, viable=viable)
    record = dict(record)
    record["outcome"] = "promoted" if viable else "failed"
    # Return artifact_dir for failed_context lifecycle management
    return record, artifact_dir


async def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--generations", type=int, default=1)
    parser.add_argument(
        "--spec",
        type=Path,
        default=Path("spec/CAMBRIAN-SPEC-005.md"),
        help="Path to base spec",
    )
    parser.add_argument(
        "--model",
        default=os.environ.get("CAMBRIAN_ESCALATION_MODEL", "claude-sonnet-4-6"),
        help="LLM model to use",
    )
    parser.add_argument(
        "--supervisor-url",
        default=os.environ.get("CAMBRIAN_SUPERVISOR_URL", "http://localhost:8400"),
    )
    args = parser.parse_args()

    artifacts_root = Path(os.environ.get("CAMBRIAN_ARTIFACTS_ROOT", "../cambrian-artifacts"))
    artifacts_root.mkdir(parents=True, exist_ok=True)
    (artifacts_root / "gen-0-campaigns").mkdir(parents=True, exist_ok=True)

    spec_path = args.spec
    spec_text = spec_path.read_text()
    spec_hash = _hash_file(spec_path)
    start_generation = _next_generation(artifacts_root)
    campaign_id = f"gen0-{int(time.time())}"
    campaign_dir = artifacts_root / "gen-0-campaigns" / campaign_id
    campaign_dir.mkdir(parents=True, exist_ok=True)

    log.info(
        "gen0_campaign_start",
        generations=args.generations,
        start_generation=start_generation,
        campaign_id=campaign_id,
        model=args.model,
    )

    async with aiohttp.ClientSession() as session:
        # sanity check supervisor is up
        await _fetch_versions(session, args.supervisor_url)

        records: list[dict[str, Any]] = []
        failed_context: dict[str, Any] | None = None
        prev_artifact_dir: Path | None = None  # kept alive for failed_context prompt

        for i in range(args.generations):
            generation = start_generation + i
            parent = generation - 1

            try:
                record, artifact_dir = await _run_one_generation(
                    session=session,
                    supervisor_url=args.supervisor_url,
                    artifacts_root=artifacts_root,
                    spec_text=spec_text,
                    spec_hash=spec_hash,
                    generation=generation,
                    parent=parent,
                    campaign_id=campaign_id,
                    model=args.model,
                    failed_context=failed_context,
                )
            finally:
                # Safe to clean up previous failed artifact now that the prompt was built
                if prev_artifact_dir is not None and prev_artifact_dir.exists():
                    shutil.rmtree(prev_artifact_dir, ignore_errors=True)
                    log.info(
                        "artifact_dir_cleaned",
                        generation=generation - 1,
                        path=str(prev_artifact_dir),
                    )
                prev_artifact_dir = None

            viable = record.get("outcome") == "promoted"
            if not viable:
                viability = record.get("viability", {})
                failed_context = {
                    "diagnostics": viability.get("diagnostics", {}),
                    "artifact_dir": artifact_dir,
                }
                prev_artifact_dir = artifact_dir  # keep alive for next gen's prompt
            else:
                failed_context = None
                prev_artifact_dir = None

            records.append(record)

        # Clean up any remaining failed artifact from the last generation
        if prev_artifact_dir is not None and prev_artifact_dir.exists():
            shutil.rmtree(prev_artifact_dir, ignore_errors=True)

    viable = sum(1 for r in records if r.get("outcome") == "promoted")
    summary = {
        "campaign_id": campaign_id,
        "spec_path": str(spec_path),
        "spec_hash": spec_hash,
        "model": args.model,
        "supervisor_url": args.supervisor_url,
        "start_generation": start_generation,
        "generations": args.generations,
        "viable": viable,
        "records": records,
    }
    (campaign_dir / "summary.json").write_text(json.dumps(summary, indent=2))
    log.info(
        "gen0_campaign_complete",
        campaign_id=campaign_id,
        total=len(records),
        viable=viable,
        summary_path=str(campaign_dir / "summary.json"),
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
