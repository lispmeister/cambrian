"""Append-only generation record store backed by generations.json in the artifacts repo."""

import json
import os
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import structlog

log = structlog.get_logger()


def _generations_path() -> Path:
    root = os.environ.get("CAMBRIAN_ARTIFACTS_ROOT", "../cambrian-artifacts")
    return Path(root) / "generations.json"


def load_all() -> list[dict[str, Any]]:
    path = _generations_path()
    if not path.exists():
        return []
    with path.open() as f:
        data = json.load(f)
    return data if isinstance(data, list) else []


def append(record: dict[str, Any]) -> None:
    path = _generations_path()
    records = load_all()
    records.append(record)
    path.write_text(json.dumps(records, indent=2))
    log.info("generation_record_appended", generation=record.get("generation"))


_TERMINAL_OUTCOMES = {"promoted", "failed", "timeout"}


def update(generation: int, fields: dict[str, Any]) -> None:
    """Update fields on an existing generation record in place.

    Rejects updates to records that already have a terminal outcome
    (promoted, failed, timeout) to prevent state corruption.
    Uses a dict parameter instead of **kwargs so callers can use kebab-case
    field names (e.g. "artifact-ref") which are invalid Python identifiers.
    """
    path = _generations_path()
    records = load_all()
    found = False
    for record in records:
        if record.get("generation") == generation:
            current_outcome = record.get("outcome")
            if current_outcome in _TERMINAL_OUTCOMES:
                log.warning(
                    "generation_record_update_rejected",
                    generation=generation,
                    current_outcome=current_outcome,
                    attempted_fields=list(fields.keys()),
                )
                return
            record.update(fields)
            if "completed" not in fields:
                record["completed"] = datetime.now(UTC).isoformat()
            found = True
            break
    if not found:
        log.warning("generation_record_not_found", generation=generation)
        return
    path.write_text(json.dumps(records, indent=2))
    log.info("generation_record_updated", generation=generation, fields=list(fields.keys()))


def get(generation: int) -> dict[str, Any] | None:
    for record in load_all():
        if record.get("generation") == generation:
            return record
    return None
