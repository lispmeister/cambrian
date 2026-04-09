"""Cambrian Supervisor — host-side HTTP server managing containers and generation history."""

import asyncio
import contextlib
import hashlib
import json
import os
import shutil
import tempfile
import time
from collections.abc import Callable, Coroutine
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import aiodocker
import structlog
from aiohttp import web

from . import generations, git_ops

log = structlog.get_logger(component="supervisor")


# ---------------------------------------------------------------------------
# Artifact hash
# ---------------------------------------------------------------------------


def compute_artifact_hash(artifact_root: Path, files: list[str]) -> str:
    """Compute SHA-256 hash of artifact files per CAMBRIAN-SPEC-005 algorithm.

    Files are processed in lexicographic order. manifest.json is excluded.
    Each file contributes: path_bytes + null_byte + file_bytes.
    """
    hasher = hashlib.sha256()
    for rel_path in sorted(files):
        if rel_path == "manifest.json":
            continue
        hasher.update(rel_path.encode())
        hasher.update(b"\0")
        hasher.update((artifact_root / rel_path).read_bytes())
    return f"sha256:{hasher.hexdigest()}"


# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

PORT = int(os.environ.get("CAMBRIAN_SUPERVISOR_PORT", "8400"))
DOCKER_IMAGE = os.environ.get("CAMBRIAN_DOCKER_IMAGE", "cambrian-base")
SUPERVISOR_URL = os.environ.get("CAMBRIAN_SUPERVISOR_URL", "http://host.docker.internal:8400")
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


def _schedule_test_rig(
    factory: Callable[[], Coroutine[Any, Any, None]],
    *,
    name: str | None = None,
) -> None:
    """Schedule test rig without instantiating coroutine in tests."""
    asyncio.create_task(factory(), name=name)


# ---------------------------------------------------------------------------
# Handlers
# ---------------------------------------------------------------------------


async def handle_root(request: web.Request) -> web.Response:
    records = generations.load_all()
    rows = "".join(
        f"<tr><td>{r.get('generation')}</td><td>{r.get('outcome', 'in_progress')}</td>"
        f"<td>{r.get('created', '')}</td></tr>"
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
    `generation` is the highest completed generation number (0 if none).
    """
    _TERMINAL = {"promoted", "failed", "timeout"}
    records = generations.load_all()
    completed = [r for r in records if r.get("outcome") in _TERMINAL]
    latest_gen = max((int(r.get("generation", 0)) for r in completed), default=0)
    uptime = int(time.time() - _start_time)
    return web.json_response(
        {
            "generation": latest_gen,
            "status": _status,
            "uptime": uptime,
        }
    )


async def handle_versions(request: web.Request) -> web.Response:
    return web.json_response(generations.load_all())


async def handle_debug_state(request: web.Request) -> web.Response:
    """Dump internal Supervisor state as JSON — for development debugging only."""
    return web.json_response(
        {
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
        }
    )


async def handle_spawn(request: web.Request) -> web.Response:
    body: dict[str, Any] = await request.json()
    generation = int(body["generation"])
    artifact_rel = body["artifact-path"]  # relative path inside artifacts repo
    spec_hash = body.get("spec-hash", "")
    if not spec_hash:
        return web.json_response({"ok": False, "error": "spec-hash is required"}, status=400)

    # BOOTSTRAP-SPEC-002 §1.3: generation MUST be >= 1
    if generation < 1:
        return web.json_response({"ok": False, "error": "generation must be >= 1"}, status=400)

    # Guard against duplicate spawns — two requests for the same generation would
    # race on git branch creation and append two records.
    if generations.get(generation) is not None:
        return web.json_response(
            {"ok": False, "error": f"generation {generation} already exists"}, status=409
        )

    artifacts_root = git_ops.artifacts_root()
    artifacts_root_path = Path(artifacts_root).resolve()
    artifact_path = (artifacts_root_path / artifact_rel).resolve()

    # Path traversal guard — reject paths that escape the artifacts root
    try:
        artifact_path.relative_to(artifacts_root_path)
    except ValueError:
        return web.json_response(
            {"ok": False, "error": "artifact-path escapes artifacts root"},
            status=400,
        )

    if not artifact_path.exists():
        return web.json_response(
            {"ok": False, "error": f"Artifact path does not exist: {artifact_path}"},
            status=400,
        )

    manifest_file = artifact_path / "manifest.json"
    if not manifest_file.exists():
        return web.json_response(
            {"ok": False, "error": "manifest.json missing in artifact"},
            status=400,
        )

    try:
        manifest = json.loads(manifest_file.read_text())
    except json.JSONDecodeError as e:
        return web.json_response(
            {"ok": False, "error": f"manifest.json is invalid JSON: {e}"},
            status=400,
        )

    files = manifest.get("files")
    if not isinstance(files, list) or not all(isinstance(f, str) for f in files):
        return web.json_response(
            {"ok": False, "error": "manifest.json files must be an array of strings"},
            status=400,
        )

    try:
        artifact_hash = compute_artifact_hash(artifact_path, files)
    except Exception as e:
        return web.json_response(
            {"ok": False, "error": f"artifact-hash computation failed: {e}"},
            status=400,
        )

    # Verify Docker image exists.
    # Use list() instead of inspect() — on Docker Desktop, inspect() returns 404
    # for images that exist (list() sees them correctly).
    _set_status("spawning", generation)
    docker = aiodocker.Docker()
    try:
        images = await docker.images.list()
        image_tag = DOCKER_IMAGE if ":" in DOCKER_IMAGE else f"{DOCKER_IMAGE}:latest"
        found = any(image_tag in (img.get("RepoTags") or []) for img in images)
        if not found:
            await docker.close()
            _set_status("idle")
            err = f"Docker image {DOCKER_IMAGE!r} not found. Run docker/build.sh"
            return web.json_response({"ok": False, "error": err}, status=400)
    except Exception as e:
        await docker.close()
        _set_status("idle")
        return web.json_response(
            {"ok": False, "error": f"Docker error checking image: {e}"},
            status=500,
        )
    await docker.close()

    container_id = f"lab-gen-{generation}"

    # Create branch and commit artifact in artifacts repo before starting container.
    # create_generation_branch holds _git_lock for the entire sequence.
    try:
        await git_ops.create_generation_branch(generation, artifact_rel)
    except git_ops.GitError as e:
        _set_status("idle")
        return web.json_response({"ok": False, "error": f"Git error: {e}"}, status=500)

    # Read campaign-id from body if present (MAY field for M2)
    campaign_id: str | None = body.get("campaign-id")

    # Compute artifact-hash from manifest files list (CAMBRIAN-SPEC-005 algorithm)

    # Record in-progress state — MAY fields (artifact-ref, completed, viability)
    # are absent while in_progress, per spec.
    record: dict[str, Any] = {
        "generation": generation,
        "parent": generation - 1,
        "spec-hash": spec_hash,
        "artifact-hash": artifact_hash,
        "outcome": "in_progress",
        "created": datetime.now(UTC).isoformat(),
        "container-id": container_id,
    }
    if campaign_id:
        record["campaign-id"] = campaign_id
    generations.append(record)

    # Spawn Test Rig as background task — return immediately
    _schedule_test_rig(
        lambda: run_test_rig(generation, artifact_path, container_id),
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
    if record.get("outcome") != "tested":
        _set_status("idle")
        return web.json_response(
            {"ok": False, "error": f"Generation {generation} is not in tested state"},
            status=409,
        )

    artifact_rel = record.get("artifact-ref", f"gen-{generation}")
    artifact_path = Path(git_ops.artifacts_root()) / artifact_rel

    try:
        tag = await git_ops.promote(generation, artifact_path)
    except git_ops.GitError as e:
        _set_status("idle")
        return web.json_response({"ok": False, "error": str(e)}, status=500)

    generations.update(generation, {"outcome": "promoted", "artifact-ref": tag})
    _set_status("idle")
    log.info("generation_promoted", generation=generation, tag=tag)
    return web.json_response({"ok": True, "generation": generation})


async def handle_rollback(request: web.Request) -> web.Response:
    body: dict[str, Any] = await request.json()
    generation = int(body["generation"])

    _set_status("rolling-back", generation)
    record = generations.get(generation)
    if not record:
        _set_status("idle")
        return web.json_response(
            {"ok": False, "error": f"Generation {generation} not found"}, status=404
        )
    if record.get("outcome") != "tested":
        _set_status("idle")
        return web.json_response(
            {"ok": False, "error": f"Generation {generation} is not in tested state"},
            status=409,
        )

    try:
        tag = await git_ops.rollback(generation)
    except git_ops.GitError as e:
        _set_status("idle")
        return web.json_response({"ok": False, "error": str(e)}, status=500)

    generations.update(generation, {"outcome": "failed", "artifact-ref": tag})
    _set_status("idle")
    log.info("generation_rolled_back", generation=generation, tag=tag)
    return web.json_response({"ok": True, "generation": generation})


# ---------------------------------------------------------------------------
# Test Rig background task
# ---------------------------------------------------------------------------


def _make_error_viability(generation: int, summary: str) -> dict[str, Any]:
    """Return a non-viable viability report for infrastructure failures."""
    return {
        "generation": generation,
        "status": "non-viable",
        "failure_stage": "health",
        "checks": {
            "manifest": {"passed": False},
            "build": {"passed": False, "duration_ms": 0},
            "test": {"passed": False, "duration_ms": 0, "tests_run": 0, "tests_passed": 0},
            "start": {"passed": False, "duration_ms": 0},
            "health": {"passed": False, "duration_ms": 0},
        },
        "completed_at": datetime.now(UTC).isoformat(),
        "diagnostics": {
            "stage": "health",
            "summary": summary,
            "exit_code": None,
            "failures": [],
            "stdout_tail": "",
            "stderr_tail": "",
        },
    }


async def run_test_rig(generation: int, artifact_path: Path, container_id: str) -> None:
    """Run the Test Rig container and update the generation record with results.

    Sets outcome to "tested" — Prime is responsible for calling /promote or /rollback
    to set the final terminal outcome.
    """
    api_key = os.environ.get("ANTHROPIC_API_KEY", "")
    docker = aiodocker.Docker()
    container: Any = None
    # Isolated output directory for the viability report. Separate from the artifact
    # workspace so the organism's code cannot predict or overwrite the report path.
    output_dir = Path(tempfile.mkdtemp(prefix="cambrian-output-"))
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
                "Binds": [
                    f"{artifact_path.resolve()}:/workspace:rw",
                    # Separate output mount: Test Rig writes report here.
                    # Organism code runs in /workspace and cannot predict this path.
                    f"{output_dir.resolve()}:/output:rw",
                ],
            },
        }
        container = await docker.containers.create_or_replace(name=container_id, config=config)
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
            generations.update(generation, {"outcome": "timeout"})
            _set_status("idle")
            return

        # Read viability report from the isolated output directory
        report_path = output_dir / "viability-report.json"
        if report_path.exists():
            with report_path.open() as f:
                viability = json.load(f)
        else:
            viability = _make_error_viability(
                generation,
                "Viability report not written — container crashed or exited without reporting",
            )
            log.warning("viability_report_missing", generation=generation)

        # Set outcome to "tested" — Prime will call /promote or /rollback
        generations.update(generation, {"outcome": "tested", "viability": viability})
        viable = viability.get("status") == "viable"
        log.info("test_rig_complete", generation=generation, viable=viable)

    except Exception as e:
        log.error("test_rig_error", generation=generation, error=str(e))
        generations.update(
            generation,
            {
                "outcome": "tested",
                "viability": _make_error_viability(
                    generation, f"Test rig infrastructure error: {e}"
                ),
            },
        )
    finally:
        await docker.close()
        # Remove __pycache__ and .pytest_cache left by the container in the bind-mounted
        # workspace. The Dockerfile sets PYTHONDONTWRITEBYTECODE=1 as the primary guard;
        # this is a safety net for any subprocesses that bypass that env var.
        cache_dirs = (*artifact_path.rglob("__pycache__"), *artifact_path.rglob(".pytest_cache"))
        for cache_dir in cache_dirs:
            shutil.rmtree(cache_dir, ignore_errors=True)
        # Clean up the isolated output directory.
        shutil.rmtree(output_dir, ignore_errors=True)
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


async def _startup() -> None:
    """Initialize artifacts repo and generation store before the server starts."""
    await git_ops.ensure_repo()
    generations.load_all()  # creates generations.json if absent (handled lazily)
    log.info("supervisor_ready", port=PORT, artifacts_root=git_ops.artifacts_root())


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

    app = make_app()
    app.on_startup.append(lambda _: _startup())
    web.run_app(app, host="0.0.0.0", port=PORT)


if __name__ == "__main__":
    main()
