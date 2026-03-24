"""Append-only generation record store backed by generations.json in the artifacts repo."""
import json
import os
from datetime import datetime, timezone
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


def update(generation: int, **fields: Any) -> None:
    """Update fields on an existing generation record in place."""
    path = _generations_path()
    records = load_all()
    for record in records:
        if record.get("generation") == generation:
            record.update(fields)
            if "completed" not in fields:
                record["completed"] = datetime.now(timezone.utc).isoformat()
            break
    path.write_text(json.dumps(records, indent=2))
    log.info("generation_record_updated", generation=generation, fields=list(fields.keys()))


def get(generation: int) -> dict[str, Any] | None:
    for record in load_all():
        if record.get("generation") == generation:
            return record
    return None
