"""Cambrian Supervisor — host-side HTTP server managing containers and generation history."""
import asyncio
import contextlib
import json
import os
import shutil
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import aiodocker
import structlog
from aiohttp import web

from . import generations, git_ops

log = structlog.get_logger(component="supervisor")


# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

PORT = int(os.environ.get("CAMBRIAN_SUPERVISOR_PORT", "8400"))
DOCKER_IMAGE = os.environ.get("CAMBRIAN_DOCKER_IMAGE", "cambrian-base")
SUPERVISOR_URL = os.environ.get(
    "CAMBRIAN_SUPERVISOR_URL", "http://host.docker.internal:8400"
)
CONTAINER_TIMEOUT = int(os.environ.get("CAMBRIAN_CONTAINER_TIMEOUT", "600"))

_start_time: float = 0.0
# Supervisor operational status — NOT the same as generation record outcome.
# idle | spawning | testing | promoting | rolling-back
_status: str = "idle"
_current_generation: int | None = None


def _require_env(name: str) -> str:
    value = os.environ.get(name)
    if not value:
        raise SystemExit(f"Fatal: {name} is required but not set.")
    return value


def _set_status(status: str, generation: int | None = None) -> None:
    global _status, _current_generation
    _status = status
    if generation is not None:
        _current_generation = generation


# ---------------------------------------------------------------------------
# Handlers
# ---------------------------------------------------------------------------

async def handle_root(request: web.Request) -> web.Response:
    records = generations.load_all()
    rows = "".join(
        f"<tr><td>{r.get('generation')}</td><td>{r.get('outcome','in_progress')}</td>"
        f"<td>{r.get('created','')}</td></tr>"
        for r in records
    )
    html = (
        "<html><body><h1>Cambrian Supervisor</h1>"
        f"<table border=1><tr><th>Gen</th><th>Outcome</th><th>Created</th></tr>{rows}</table>"
        "</body></html>"
    )
    return web.Response(text=html, content_type="text/html")


async def handle_stats(request: web.Request) -> web.Response:
    """Return Supervisor operational state.

    Note: `status` here is the Supervisor's own state (idle/spawning/testing/…),
    NOT the latest generation record's `outcome`. Do not derive one from the other.
    """
    records = generations.load_all()
    latest = records[-1] if records else None
    latest_gen = int(latest.get("generation", 0)) if latest else 0
    uptime = int(time.time() - _start_time)
    return web.json_response({
        "generation": latest_gen,
        "status": _status,
        "uptime": uptime,
    })


async def handle_versions(request: web.Request) -> web.Response:
    return web.json_response(generations.load_all())


async def handle_debug_state(request: web.Request) -> web.Response:
    """Dump internal Supervisor state as JSON — for development debugging only."""
    return web.json_response({
        "status": _status,
        "current_generation": _current_generation,
        "uptime": int(time.time() - _start_time),
        "records": generations.load_all(),
        "config": {
            "port": PORT,
            "docker_image": DOCKER_IMAGE,
            "container_timeout": CONTAINER_TIMEOUT,
            "artifacts_root": git_ops.artifacts_root(),
        },
    })


async def handle_spawn(request: web.Request) -> web.Response:
    body: dict[str, Any] = await request.json()
    generation = int(body["generation"])
    artifact_rel = body["artifact-path"]  # relative path inside artifacts repo

    artifacts_root = git_ops.artifacts_root()
    artifact_path = Path(artifacts_root) / artifact_rel

    if not artifact_path.exists():
        return web.json_response(
            {"ok": False, "error": f"Artifact path does not exist: {artifact_path}"},
            status=400,
        )

    # Verify Docker image exists
    _set_status("spawning", generation)
    docker = aiodocker.Docker()
    try:
        await docker.images.inspect(DOCKER_IMAGE)
    except Exception:
        await docker.close()
        _set_status("idle")
        return web.json_response(
            {"ok": False, "error": f"Docker image {DOCKER_IMAGE} not found. Run docker/build.sh"},
            status=400,
        )
    await docker.close()

    container_id = f"lab-gen-{generation}"

    # Create branch and commit artifact in artifacts repo before starting container
    try:
        await git_ops.ensure_on_main()
        await git_ops.git("checkout", "-b", f"gen-{generation}")
        await git_ops.git("add", "-A")
        await git_ops.git(
            "commit", "--allow-empty",
            "-m", f"Generation {generation} artifact",
        )
    except git_ops.GitError as e:
        _set_status("idle")
        return web.json_response({"ok": False, "error": f"Git error: {e}"}, status=500)

    # Read campaign-id from body if present (MAY field for M2)
    campaign_id: str | None = body.get("campaign-id")

    # Record in-progress state
    record: dict[str, Any] = {
        "generation": generation,
        "parent": generation - 1,
        "spec-hash": body.get("spec-hash", ""),
        "artifact-hash": "",
        "outcome": "in_progress",
        "artifact_ref": f"gen-{generation}",
        "created": datetime.now(UTC).isoformat(),
        "completed": None,
        "container-id": container_id,
        "viability": None,
    }
    if campaign_id:
        record["campaign-id"] = campaign_id
    generations.append(record)

    # Spawn Test Rig as background task — return immediately
    asyncio.create_task(
        run_test_rig(generation, artifact_path, container_id),
        name=f"test-rig-gen-{generation}",
    )

    log.info("spawn_accepted", generation=generation, container_id=container_id)
    return web.json_response({"ok": True, "container-id": container_id, "generation": generation})


async def handle_promote(request: web.Request) -> web.Response:
    body: dict[str, Any] = await request.json()
    generation = int(body["generation"])

    _set_status("promoting", generation)
    record = generations.get(generation)
    if not record:
        _set_status("idle")
        return web.json_response(
            {"ok": False, "error": f"Generation {generation} not found"}, status=404
        )

    artifact_rel = record.get("artifact_ref", f"gen-{generation}")
    artifact_path = Path(git_ops.artifacts_root()) / artifact_rel

    try:
        tag = await git_ops.promote(generation, artifact_path)
    except git_ops.GitError as e:
        _set_status("idle")
        return web.json_response({"ok": False, "error": str(e)}, status=500)

    generations.update(generation, outcome="promoted", artifact_ref=tag)
    _set_status("idle")
    log.info("generation_promoted", generation=generation, tag=tag)
    return web.json_response({"ok": True, "generation": generation})


async def handle_rollback(request: web.Request) -> web.Response:
    body: dict[str, Any] = await request.json()
    generation = int(body["generation"])

    _set_status("rolling-back", generation)
    try:
        tag = await git_ops.rollback(generation)
    except git_ops.GitError as e:
        _set_status("idle")
        return web.json_response({"ok": False, "error": str(e)}, status=500)

    generations.update(generation, outcome="failed", artifact_ref=tag)
    _set_status("idle")
    log.info("generation_rolled_back", generation=generation, tag=tag)
    return web.json_response({"ok": True, "generation": generation})


# ---------------------------------------------------------------------------
# Test Rig background task
# ---------------------------------------------------------------------------

async def run_test_rig(generation: int, artifact_path: Path, container_id: str) -> None:
    """Run the Test Rig container and update the generation record with results.

    Sets outcome to "tested" — Prime is responsible for calling /promote or /rollback
    to set the final terminal outcome.
    """
    api_key = os.environ.get("ANTHROPIC_API_KEY", "")
    docker = aiodocker.Docker()
    container: Any = None
    try:
        # Build env list: required vars always set; optional vars threaded through
        # from the supervisor's own environment only if explicitly set there.
        _optional_vars = [
            "CAMBRIAN_MODEL",
            "CAMBRIAN_ESCALATION_MODEL",
            "CAMBRIAN_MAX_PARSE_RETRIES",
            "CAMBRIAN_MAX_RETRIES",
            "CAMBRIAN_MAX_GENS",
            "CAMBRIAN_TOKEN_BUDGET",
            "CAMBRIAN_SPEC_PATH",
        ]
        env_list = [
            f"ANTHROPIC_API_KEY={api_key}",
            f"CAMBRIAN_SUPERVISOR_URL={SUPERVISOR_URL}",
            f"CAMBRIAN_GENERATION={generation}",
        ]
        for var in _optional_vars:
            val = os.environ.get(var)
            if val is not None:
                env_list.append(f"{var}={val}")

        config: dict[str, Any] = {
            "Image": DOCKER_IMAGE,
            "Env": env_list,
            "HostConfig": {
                "Binds": [f"{artifact_path.resolve()}:/workspace:rw"],
            },
        }
        container = await docker.containers.create_or_replace(
            name=container_id, config=config
        )
        await container.start()
        _set_status("testing", generation)
        log.info("test_rig_started", generation=generation, container_id=container_id)

        # Non-blocking wait with configurable timeout to prevent hung containers
        try:
            await asyncio.wait_for(container.wait(), timeout=float(CONTAINER_TIMEOUT))
        except TimeoutError:
            log.warning("container_timeout", generation=generation, timeout=CONTAINER_TIMEOUT)
            with contextlib.suppress(Exception):
                await container.kill()
            generations.update(generation, outcome="timeout")
            _set_status("idle")
            return

        # Read viability report written by Test Rig into the mounted volume
        report_path = artifact_path / "viability-report.json"
        if report_path.exists():
            with report_path.open() as f:
                viability = json.load(f)
        else:
            viability = {
                "generation": generation,
                "status": "non-viable",
                "failure_stage": "health",
                "checks": {
                    "manifest": {"passed": False},
                    "build": {"passed": False, "duration_ms": 0},
                    "test": {"passed": False, "duration_ms": 0},
                    "start": {"passed": False, "duration_ms": 0},
                    "health": {"passed": False, "duration_ms": 0},
                },
                "completed_at": datetime.now(UTC).isoformat(),
                "diagnostics": {
                    "stage": "health",
                    "summary": (
                        "Viability report not written — "
                        "container crashed or exited without reporting"
                    ),
                    "exit_code": None,
                    "failures": [],
                    "stdout_tail": "",
                    "stderr_tail": "",
                },
            }
            log.warning("viability_report_missing", generation=generation)

        # Set outcome to "tested" — Prime will call /promote or /rollback
        generations.update(generation, outcome="tested", viability=viability)
        viable = viability.get("status") == "viable"
        log.info("test_rig_complete", generation=generation, viable=viable)

    except Exception as e:
        log.error("test_rig_error", generation=generation, error=str(e))
        generations.update(generation, outcome="tested", viability={
            "generation": generation,
            "status": "non-viable",
            "failure_stage": "health",
            "checks": {
                "manifest": {"passed": False},
                "build": {"passed": False, "duration_ms": 0},
                "test": {"passed": False, "duration_ms": 0},
                "start": {"passed": False, "duration_ms": 0},
                "health": {"passed": False, "duration_ms": 0},
            },
            "completed_at": datetime.now(UTC).isoformat(),
            "diagnostics": {
                "stage": "health",
                "summary": f"Test rig infrastructure error: {e}",
                "exit_code": None,
                "failures": [],
                "stdout_tail": "",
                "stderr_tail": "",
            },
        })
    finally:
        if container is not None:
            with contextlib.suppress(Exception):
                await container.delete()
        await docker.close()
        # Remove __pycache__ and .pytest_cache left by the container in the bind-mounted
        # workspace. The Dockerfile sets PYTHONDONTWRITEBYTECODE=1 as the primary guard;
        # this is a safety net for any subprocesses that bypass that env var.
        for cache_dir in (*artifact_path.rglob("__pycache__"), *artifact_path.rglob(".pytest_cache")):
            shutil.rmtree(cache_dir, ignore_errors=True)
        _set_status("idle")


# ---------------------------------------------------------------------------
# App factory + startup validation
# ---------------------------------------------------------------------------

def make_app() -> web.Application:
    app = web.Application()
    app.router.add_get("/", handle_root)
    app.router.add_get("/stats", handle_stats)
    app.router.add_get("/versions", handle_versions)
    app.router.add_get("/debug/state", handle_debug_state)
    app.router.add_post("/spawn", handle_spawn)
    app.router.add_post("/promote", handle_promote)
    app.router.add_post("/rollback", handle_rollback)
    return app


def main() -> None:
    global _start_time
    _require_env("ANTHROPIC_API_KEY")
    _start_time = time.time()

    structlog.configure(
        processors=[
            structlog.stdlib.add_log_level,
            structlog.processors.TimeStamper(fmt="iso"),
            structlog.dev.ConsoleRenderer(),
        ]
    )

    log.info("supervisor_ready", port=PORT, artifacts_root=git_ops.artifacts_root())
    web.run_app(make_app(), host="0.0.0.0", port=PORT)


if __name__ == "__main__":
    main()
