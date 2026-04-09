"""Baseline battery management for counterfactual regression testing (M3).

After each promoted generation, extract its contracts and metadata into a
battery file under {artifacts_root}/baselines/gen-{N}/battery.json. Subsequent
generations are tested against this battery to detect spec regression.

Battery schema (kebab-case wire format):
{
  "generation": 42,
  "created-at": "...",
  "spec-hash": "sha256:...",
  "artifact-hash": "sha256:...",
  "artifact-ref": "gen-42",
  "contracts": [...]
}
"""

import json
import os
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import structlog

log = structlog.get_logger(component="baseline")


def _baselines_root() -> Path:
    root = os.environ.get("CAMBRIAN_ARTIFACTS_ROOT", "../cambrian-artifacts")
    return Path(root) / "baselines"


def extract(generation: int, artifact_path: Path, record: dict[str, Any]) -> Path | None:
    """Extract a battery from a just-promoted generation's artifact.

    Reads manifest.json from artifact_path, pulls out contracts and metadata,
    and writes {baselines_root}/gen-{generation}/battery.json.

    Returns the battery path on success, None if extraction fails (logged).
    """
    manifest_path = artifact_path / "manifest.json"
    if not manifest_path.exists():
        log.warning("baseline_extract_no_manifest", generation=generation)
        return None

    try:
        manifest = json.loads(manifest_path.read_text())
    except Exception as e:
        log.warning("baseline_extract_bad_manifest", generation=generation, error=str(e))
        return None

    battery: dict[str, Any] = {
        "generation": generation,
        "created-at": datetime.now(UTC).isoformat(),
        "spec-hash": record.get("spec-hash", manifest.get("spec-hash", "")),
        "artifact-hash": record.get("artifact-hash", manifest.get("artifact-hash", "")),
        "artifact-ref": record.get("artifact-ref", f"gen-{generation}"),
        "contracts": manifest.get("contracts", []),
    }

    battery_dir = _baselines_root() / f"gen-{generation}"
    try:
        battery_dir.mkdir(parents=True, exist_ok=True)
        battery_path = battery_dir / "battery.json"
        battery_path.write_text(json.dumps(battery, indent=2))
        log.info(
            "baseline_extracted",
            generation=generation,
            contracts=len(battery["contracts"]),
            path=str(battery_path),
        )
        return battery_path
    except Exception as e:
        log.warning("baseline_extract_write_failed", generation=generation, error=str(e))
        return None


def latest() -> dict[str, Any] | None:
    """Return the battery from the most recently promoted generation, or None.

    Scans baselines_root for gen-N directories, picks the highest N that has
    a valid battery.json.
    """
    root = _baselines_root()
    if not root.exists():
        return None

    candidates: list[tuple[int, Path]] = []
    for entry in root.iterdir():
        if entry.is_dir() and entry.name.startswith("gen-"):
            try:
                n = int(entry.name[4:])
                battery_path = entry / "battery.json"
                if battery_path.exists():
                    candidates.append((n, battery_path))
            except ValueError:
                pass

    if not candidates:
        return None

    _, best_path = max(candidates, key=lambda t: t[0])
    try:
        return json.loads(best_path.read_text())
    except Exception as e:
        log.warning("baseline_load_failed", path=str(best_path), error=str(e))
        return None


def battery_path_for(generation: int) -> Path:
    """Return the battery path for a given generation (may not exist)."""
    return _baselines_root() / f"gen-{generation}" / "battery.json"
