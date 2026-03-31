"""Append-only generation record store backed by generations.json in the artifacts repo."""

import fcntl
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


def _read_locked(path: Path) -> list[dict[str, Any]]:
    """Read records with a shared lock."""
    if not path.exists():
        return []
    with path.open() as f:
        fcntl.flock(f, fcntl.LOCK_SH)
        try:
            data = json.load(f)
        finally:
            fcntl.flock(f, fcntl.LOCK_UN)
    return data if isinstance(data, list) else []


def _atomic_modify(path: Path, modifier: Any) -> None:
    """Read-modify-write with exclusive lock — prevents lost updates.

    The modifier receives the current record list and returns either a modified
    list (which is written back) or None to signal "no change needed" (skips
    the write so the file's mtime is not touched).

    Opens the file in r+ mode (creating if absent), holds an exclusive lock
    for the entire read-modify-write cycle, then seeks to 0 and truncates
    before writing to avoid stale tail bytes.
    """
    if not path.exists():
        path.touch()
    with path.open("r+") as f:
        fcntl.flock(f, fcntl.LOCK_EX)
        try:
            content = f.read()
            records: list[dict[str, Any]] = json.loads(content) if content.strip() else []
            if not isinstance(records, list):
                records = []
            result = modifier(records)
            if result is None:
                return  # modifier signalled no change — skip write
            f.seek(0)
            f.truncate()
            f.write(json.dumps(result, indent=2))
        finally:
            fcntl.flock(f, fcntl.LOCK_UN)


def load_all() -> list[dict[str, Any]]:
    return _read_locked(_generations_path())


def append(record: dict[str, Any]) -> None:
    def _append(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
        records.append(record)
        return records

    _atomic_modify(_generations_path(), _append)
    log.info("generation_record_appended", generation=record.get("generation"))


_TERMINAL_OUTCOMES = {"promoted", "failed", "timeout"}


def update(generation: int, fields: dict[str, Any]) -> None:
    """Update fields on an existing generation record in place.

    Rejects updates to records that already have a terminal outcome
    (promoted, failed, timeout) to prevent state corruption.

    Sets `completed` only when the outcome transitions to a terminal state —
    not on every update (e.g. attaching a viability report leaves completed unset).

    Uses a dict parameter instead of **kwargs so callers can use kebab-case
    field names (e.g. "artifact-ref") which are invalid Python identifiers.
    """
    updated = False
    rejected = False

    def _update(records: list[dict[str, Any]]) -> list[dict[str, Any]] | None:
        nonlocal updated, rejected
        for record in records:
            if record.get("generation") == generation:
                current_outcome = record.get("outcome")
                if current_outcome in _TERMINAL_OUTCOMES:
                    rejected = True
                    return None  # no write needed
                record.update(fields)
                # Only stamp completed when reaching a terminal state
                new_outcome = record.get("outcome")
                if new_outcome in _TERMINAL_OUTCOMES and not record.get("completed"):
                    record["completed"] = datetime.now(UTC).isoformat()
                updated = True
                return records
        return None  # generation not found — no write needed

    _atomic_modify(_generations_path(), _update)

    if rejected:
        log.warning(
            "generation_record_update_rejected",
            generation=generation,
            attempted_fields=list(fields.keys()),
        )
    elif not updated:
        log.warning("generation_record_not_found", generation=generation)
    else:
        log.info("generation_record_updated", generation=generation, fields=list(fields.keys()))


def get(generation: int) -> dict[str, Any] | None:
    for record in load_all():
        if record.get("generation") == generation:
            return record
    return None
