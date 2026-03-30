---
date: 2026-03-23
author: Markus Fix <lispmeister@gmail.com>
title: "Cambrian Bootstrap: Supervisor, Test Rig, and First Prime"
version: 0.9.0
tags: [cambrian, bootstrap, supervisor, test-rig, docker, M1, M2, contracts, diagnostics]
ancestor: BOOTSTRAP-SPEC-001
---

# BOOTSTRAP-SPEC-002

## Overview

This spec defines the one-time bootstrap process that takes the Cambrian project from zero to a running Gen-1 Prime. It is consumed by a human + agent pair working interactively in Claude Code. It is NOT consumed by Prime — Prime reads CAMBRIAN-SPEC-005 (the genome).

The bootstrap builds three things:

1. **Supervisor** — a Python HTTP server that manages container lifecycle, generation history, and promote/rollback operations
2. **Test Rig** — a Python script that mechanically verifies artifacts (build, test, start, health-check)
3. **Gen-1 Prime** — the first organism, generated interactively from the genome spec

The bootstrap is complete when Gen-1 Prime is promoted and can reproduce.

Execution path:

- Build the Supervisor and Test Rig (Phase 0)
- Verify infrastructure with a hand-crafted test artifact
- Generate Gen-1 Prime interactively using Claude Code (Phase 1)
- Run Gen-1 through the Test Rig
- Promote Gen-1 and confirm it can generate offspring (Phase 2 — covered by CAMBRIAN-SPEC-005 acceptance criteria)

## Problem Statement

The Cambrian system has no running code yet. CAMBRIAN-SPEC-005 (the genome) defines what Prime is, but there is no Supervisor to manage containers, no Test Rig to verify artifacts, and no Prime to generate code. All three must be built from scratch before the self-reproducing loop can begin.

The bootstrap is a chicken-and-egg problem: Prime needs the Supervisor and Test Rig to verify its offspring, but nobody exists yet to build them. The solution is human + agent collaboration for the initial build, then hand-off to the autonomous loop.

## Goals

- Build a working Supervisor that implements the full HTTP API defined in this document.
- Build a working Test Rig that executes the verification pipeline defined in this document.
- Create Docker infrastructure (base images, networking, credential injection).
- Verify the infrastructure end-to-end with a hand-crafted test artifact before generating Prime.
- Generate Gen-1 Prime interactively and promote it through the standard pipeline.
- Leave a clean, documented state for CAMBRIAN-SPEC-005 to drive autonomous reproduction.

## Non-Goals

- **Building Prime autonomously.** Gen-1 is built interactively by human + Claude Code. Autonomous generation is CAMBRIAN-SPEC-005's concern.
- **Spec mutation.** The bootstrap does not modify any spec.
- **Fitness ranking.** Bootstrap only needs binary viability (pass/fail).
- **Multi-language support.** M1 is Python throughout.
- **Production hardening.** The bootstrap infrastructure is functional, not production-grade. No TLS, no auth on the Supervisor API, no resource limits on containers.
- **Dashboard polish.** The Supervisor dashboard is minimal — enough to observe state, not a product.

## Design Principles

### Infrastructure before organisms

Build and verify the Supervisor and Test Rig completely before attempting to generate Prime. If the environment is broken, every organism will fail and the failures will be misleading.

### Verify with a fake before testing with the real thing

Use a hand-crafted test artifact (a trivial Python HTTP server) to validate the entire infrastructure pipeline — container creation, Test Rig execution, viability report, promote/rollback — before generating Prime. This isolates infrastructure bugs from generation bugs.

### Minimal dependencies

The Supervisor and Test Rig use a focused set of external packages, managed by `uv` with a lockfile for reproducible builds. No Flask, no FastAPI.

Runtime dependencies:
- `aiohttp` — async HTTP server (Supervisor) and client (health checks, Supervisor API calls from Prime)
- `aiodocker` — async Docker container lifecycle management
- `pydantic` (v2) — I/O boundary validation, schema definitions, JSON serialization
- `structlog` — structured logging (JSON in production, key-value in dev)
- `rich` — pretty-printing, `__rich_repr__`, formatted debug output
- `devtools` — `debug()` helper for development introspection
- `typing-inspect` — runtime type introspection

Dev/CI dependencies:
- `pyright` — strict mode type checker (migration path to `ty` when Pydantic support lands)
- `pytest` — test runner
- `pytest-asyncio` — native async test support
- `ruff` — linter and formatter (unused imports, dead code, style, security patterns)

Project tooling:
- `uv` — package management, venv creation, lockfile (`uv.lock`), dependency resolution. Replaces raw `pip` and `python -m venv`.

### Always use virtual environments

All Python code — Supervisor, Test Rig, and generated artifacts — MUST run inside a virtual environment. Both host and container use `uv` exclusively — never raw `pip` or `python -m venv`. The host-side venv is created with `uv venv` and managed with `uv sync`. Inside containers, the Dockerfile creates a venv at `/venv` via `uv venv` and activates it. The `entry.build` command in artifacts uses `uv pip install -r requirements.txt`, which installs into `/venv`. This prevents dependency conflicts, keeps the system Python clean, and gives 10-100x faster installs compared to pip.

### Type safety

All Python code MUST be fully type-annotated. Type checking is enforced in CI — type errors fail the build.

Rules:
- **Annotate aggressively.** Every function signature, every return type, every class attribute. Start strict from day one — retrofitting annotations is expensive.
- **Type checker:** Pyright in strict mode. Configured via `pyrightconfig.json` at project root. Zero errors tolerated in CI. Migration path: switch to `ty` (Astral, Rust-based, 10-60x faster) when its Pydantic plugin ships ([astral-sh/ty#2403](https://github.com/astral-sh/ty/issues/2403)). No annotation changes required — both consume the same type syntax.
- **I/O boundary validation and JSON handling:** Pydantic v2 SHOULD be used for all data crossing process or network boundaries — manifest parsing, HTTP request/response bodies, viability reports, generation records. Raw `json.loads()`/`json.dumps()` is acceptable in M1 where schema compliance is enforced by the Test Rig. In M2, Pydantic becomes MUST — spec mutation introduces schema drift risk and the Test Rig cannot catch mismatches before they propagate. When Pydantic is used: `model_validate_json()` for deserialization, `model_dump_json()` for serialization.
- **Precise constructs:** Prefer `Protocol` over abstract base classes, `TypedDict` for JSON-shaped data, `Literal` for enums with few values (e.g., `Literal["viable", "non-viable"]`), `Self` for fluent APIs. Avoid `Any` — if a type is truly unknown, use `object` and narrow with `isinstance`.
- **No runtime cost:** Type annotations are erased at runtime. Pydantic validation happens at I/O boundaries only, not on internal function calls.

### Runtime introspection

This is experimental code. Components SHOULD be built for introspection and live debugging. Use `inspect` (stdlib) and `typing-inspect` for runtime type and call-stack introspection. Use `devtools` and `rich` for pretty-printing complex objects (generation records, viability reports) to the console during development.

Concrete recommendations:
- Major objects (Supervisor state, generation records) SHOULD implement `__rich_repr__` for readable console output.
- Debug endpoints on the Supervisor (e.g., `GET /debug/state`) SHOULD dump internal state as formatted JSON in development mode.
- Generation failures SHOULD log the full call stack using `inspect.stack()` and `devtools.debug()`, not just an error message.
- When using Pydantic: models can be introspected at runtime via `model.model_fields`, `model.model_json_schema()` — useful for self-documenting APIs and debug output.

### Structured logging everywhere

All components — Supervisor, Test Rig, and Prime — MUST use `structlog` for logging. Logs are JSON in production (machine-parseable) and key-value in development (human-readable). Every log line includes at minimum: `timestamp`, `level`, `event`, and `component` (one of `supervisor`, `test-rig`, `prime`). Generation-scoped operations MUST include `generation` in the log context. This enables robust log parsing, aggregation, and debugging across containers.

### Asyncio by default

All I/O-bound code MUST use `asyncio`. The Supervisor uses `asyncio` for its HTTP server and container lifecycle management. Prime uses `asyncio` to issue concurrent LLM calls — for example, generating subsystems in parallel when the spec permits it. The Test Rig is the exception — it runs a sequential pipeline and does not need asyncio. Free-threaded Python (3.14t) is deferred to M2.

### One Dockerfile, parameterized

A single base Docker image serves all containers (Test Rig, Prime, offspring). The entry point and mounted workspace change per use case. This reduces the surface area for container configuration bugs.

### Fail loud, fail early

Every component validates its inputs on startup. Missing `ANTHROPIC_API_KEY` is a fatal error with a clear message, not a silent `None` that surfaces as a cryptic 401 three levels deep.

## Model

Core terms (Spec, Prime, Artifact, Manifest, Generation, Viability Report) are defined in CAMBRIAN-SPEC-005 § Glossary. This spec adds:

- **Test Artifact** — A hand-crafted, minimal Python HTTP server used to validate infrastructure before Prime exists. Not a Prime. Disposable after Phase 0.
- **Bootstrap Agent** — The human + Claude Code pair performing the interactive build. Not part of the running system.

## 1. Supervisor

### 1.1 Responsibility

The Supervisor is a host-side HTTP server. It runs on the host machine (not in a container). It manages Docker containers, tracks generation history, and executes git operations.

### 1.2 HTTP API

Implements the following HTTP API (Prime calls these endpoints via CAMBRIAN-SPEC-005 § Supervisor HTTP API):

| Method | Path       | Purpose |
|--------|------------|---------|
| GET    | /          | HTML dashboard (minimal: generation list, current status) |
| GET    | /stats     | JSON: current generation, status, uptime |
| GET    | /versions  | JSON: array of all generation records |
| POST   | /spawn     | Create container, mount artifact, run Test Rig |
| POST   | /promote   | Merge gen-N branch to main in artifacts repo, create annotated tag |
| POST   | /rollback  | Create gen-N-failed tag in artifacts repo, delete branch, record failure |

All POST endpoints return `{"ok": false, "error": "..."}` on failure.

**Request/response schemas:**

`GET /stats` → `{"generation": N, "status": "idle|spawning|testing|promoting|rolling-back", "uptime": S}`
- `generation`: highest completed generation number (0 if none)
- `uptime`: integer seconds since Supervisor start

`GET /versions` → array of all GenerationRecord objects (see Generation Record schema below)

`POST /spawn` request fields:

| Field | Required | Rule |
|-------|----------|------|
| `generation` | MUST | Integer. The generation number being produced. |
| `artifact-path` | MUST | String. Path relative to `CAMBRIAN_ARTIFACTS_ROOT`. The Supervisor resolves it to an absolute host path for the Docker bind mount. Example: `"gen-2"` resolves to `$CAMBRIAN_ARTIFACTS_ROOT/gen-2`. Prime runs inside Docker and cannot know the host-side absolute path — it MUST send a relative path. |
| `spec-hash` | MUST | String. SHA-256 hex of the spec file (with `sha256:` prefix). |

`POST /spawn` response: `{"ok": true, "container-id": "lab-gen-N", "generation": N}`

`POST /promote` request: `{"generation": N}` → response: `{"ok": true, "generation": N}`

`POST /rollback` request: `{"generation": N}` → response: `{"ok": true, "generation": N}`

### 1.3 Implementation Details

**HTTP Server:**

```python
# Uses aiohttp for async HTTP server
from aiohttp import web

app = web.Application()
app.router.add_get("/", dashboard_handler)
app.router.add_get("/stats", stats_handler)
app.router.add_get("/versions", versions_handler)
app.router.add_get("/debug/state", debug_state_handler)  # dev mode only
app.router.add_post("/spawn", spawn_handler)
app.router.add_post("/promote", promote_handler)
app.router.add_post("/rollback", rollback_handler)

# Binds to 0.0.0.0:8400
# All POST endpoints accept JSON body, return JSON response
# All POST endpoints return {"ok": false, "error": "..."} on failure, never raise
# Container lifecycle runs as async tasks, not blocking the server
```

**Generation History:**

- Stored in `generations.json` in the artifacts repo root (`CAMBRIAN_ARTIFACTS_ROOT/generations.json`).
- Append-only means no record is ever deleted and no completed record is ever modified. An `in_progress` record is updated exactly once to its terminal state (promoted, failed, timeout) — this lifecycle transition is not a violation of append-only semantics. Once a record has a terminal outcome it is immutable.
- The file is a JSON array. On startup, if the file doesn't exist, create it with `[]`.
- Concurrent access is not a concern for M1 (single Prime, sequential generations).

**Generation Record schema:**

```json
{
  "generation": 1,
  "parent": 0,
  "spec-hash": "sha256:abc123...",
  "artifact-hash": "sha256:def456...",
  "outcome": "promoted",
  "artifact-ref": "gen-1",
  "created": "2026-03-21T14:30:00Z",
  "completed": "2026-03-21T14:32:30Z",
  "container-id": "lab-gen-1",
  "viability": {
    "status": "viable",
    "failure_stage": "none",
    "checks": { "...": "..." }
  }
}
```

| Field | Required | Rule |
|-------|----------|------|
| `generation` | MUST | Integer >= 1. (Generation 0 is reserved for hand-crafted test artifacts; those do not receive GenerationRecords.) |
| `parent` | MUST | Integer >= 0. |
| `spec-hash` | MUST | SHA-256 hex digest (with `sha256:` prefix). |
| `artifact-hash` | MUST | SHA-256 hex digest read from `manifest.json` in the artifact. |
| `outcome` | MUST | One of: `in_progress`, `tested`, `promoted`, `failed`, `timeout`. `in_progress` while Test Rig runs. `tested` once the Test Rig exits and before Prime calls /promote or /rollback. Terminal states: `promoted`, `failed`, `timeout`. |
| `artifact-ref` | MAY | Git ref pointing to the artifact: `gen-N` for promoted, `gen-N-failed` for rolled back. Absent while `in_progress`. |
| `created` | MUST | ISO-8601 timestamp (when spawn was received). |
| `completed` | MAY | ISO-8601 timestamp. Absent while `in_progress`. |
| `container-id` | MUST | Name of the Test Rig container (e.g., `lab-gen-1`). |
| `viability` | MAY | The full viability report. Absent while `in_progress`. |
| `campaign-id` | MAY | String. Identifier grouping generations into a campaign. Absent in M1. In M2, all generations run against the same spec variant share a `campaign-id`. Format: `campaign-<8-char-uuid>`. Absent-means-M1 — consumers MUST treat absence as equivalent to no campaign. |

**Docker Container Lifecycle:**

The Supervisor manages containers using `aiodocker`, an async Docker client that integrates natively with `asyncio`. All container operations are non-blocking — no `run_in_executor` wrappers needed.

**Spawn is asynchronous.** The `spawn_handler` creates the git branch, commits the artifact, creates the container, then returns immediately. The Test Rig runs as a background `asyncio.Task`. Prime polls `GET /versions` until the generation record shows a terminal outcome.

```python
import aiodocker
import asyncio

async def spawn_handler(request):
    body = await request.json()
    generation = body["generation"]
    artifact_path = resolve_artifact_path(body["artifact-path"])  # make absolute

    # Create git branch and commit artifact files (Supervisor owns all git ops)
    await git("checkout", "-b", f"gen-{generation}")
    await git("add", "-A", artifact_path)
    await git("commit", "-m", f"Generation {generation} artifact")

    # Create generation record with in_progress state
    record = create_generation_record(generation, body)
    append_generation_record(record)

    container_id = f"lab-gen-{generation}"
    # Return immediately — Test Rig runs as background task
    asyncio.create_task(run_test_rig(generation, artifact_path, container_id))
    return web.json_response({"ok": True, "container-id": container_id, "generation": generation})

async def run_test_rig(generation, artifact_path, container_id):
    """Background task: run Test Rig, update generation record when done."""
    client = aiodocker.Docker()
    container_timeout = int(os.environ.get("CAMBRIAN_CONTAINER_TIMEOUT", "600"))
    config = {
        "Image": "cambrian-base",
        "Env": [
            f"ANTHROPIC_API_KEY={os.environ['ANTHROPIC_API_KEY']}",
            f"CAMBRIAN_SUPERVISOR_URL=http://host.docker.internal:8400",
        ],
        "HostConfig": {
            "Binds": [f"{artifact_path}:/workspace:rw"],
        },
    }
    container = await client.containers.create_or_replace(name=container_id, config=config)
    timed_out = False
    try:
        await container.start()
        # Non-blocking wait with container-level timeout — event loop remains free for HTTP requests
        # If the container does not exit within CAMBRIAN_CONTAINER_TIMEOUT seconds, kill it and
        # set outcome to "timeout". This prevents the Supervisor from blocking indefinitely on a hung container.
        try:
            await asyncio.wait_for(container.wait(), timeout=container_timeout)
        except asyncio.TimeoutError:
            await container.kill()
            update_generation_record(generation, outcome="timeout")
            timed_out = True
            return
        # Read viability report from mounted volume (written by Test Rig to /workspace/viability-report.json)
        report = read_viability_report(artifact_path)
        # Set outcome to "tested" — Prime polls for this state, then calls /promote or /rollback.
        # The Supervisor MUST NOT auto-promote or auto-rollback. Prime owns that decision.
        update_generation_record(generation, report, outcome="tested")
    finally:
        # Always clean up the container — even if an exception occurs after start()
        import contextlib
        with contextlib.suppress(Exception):
            await container.delete()
        await client.close()
        # Remove __pycache__/ and .pytest_cache/ left by the container in the
        # bind-mounted workspace. PYTHONDONTWRITEBYTECODE=1 in the Dockerfile is
        # the primary guard; this is a safety net for subprocesses that bypass it.
        for cache_dir in (*artifact_path.rglob("__pycache__"), *artifact_path.rglob(".pytest_cache")):
            shutil.rmtree(cache_dir, ignore_errors=True)
```

**Git Operations:**

The Supervisor operates on the **artifacts repository** (path from `CAMBRIAN_ARTIFACTS_ROOT`), not the Cambrian project repo. Generated artifacts live in a separate git repo to keep the project repo clean. Git commands run as subprocesses, always with `cwd` set to the artifacts repo root. No `gitpython` dependency — git CLI is universally available and the operations are simple.

The `git()` helper takes `*args` and a `cwd` keyword argument pointing to the artifacts repo root. All git commands operate in that directory.

Promote sequence (artifacts repo):

> Note: The gen-N branch and its initial commit were already created during `spawn_handler`. The promote sequence operates on this existing branch:

1. `git checkout main` — return to main
2. `git merge gen-N --no-ff -m "Promote generation N"` — merge
3. `git tag -a gen-N -m "Generation N promoted"` — annotated tag
4. `git branch -d gen-N` — delete branch
5. Update generation record: `outcome=promoted`, `artifact-ref="gen-N"`

Rollback sequence (artifacts repo):
1. `git tag -a gen-N-failed -m "Generation N failed"` — preserve artifact
2. `git branch -D gen-N` — delete branch
3. Update generation record: `outcome=failed`, `artifact-ref="gen-N-failed"`

`artifacts_root = os.environ.get("CAMBRIAN_ARTIFACTS_ROOT", "../cambrian-artifacts")`

**Status Tracking:**

The Supervisor tracks its own state:

- `status`: one of `idle`, `spawning`, `testing`, `promoting`, `rolling-back`
- `current_generation`: the generation currently being processed (or the last completed)
- `start_time`: timestamp for uptime calculation

**Important:** The Supervisor's `status` is its own operational state — what the Supervisor process is doing right now. It is NOT the same as a generation record's `outcome` (what happened to a specific generation). `/stats` returns the Supervisor's status; `/versions` returns generation outcomes. Do NOT derive `status` from the latest record's `outcome` — a Supervisor that just finished promoting a generation is `idle`, not `promoted`.

### 1.4 Startup Sequence

```
1. Parse environment variables (ANTHROPIC_API_KEY required, fail if missing)
2. Load or initialize generations.json
3. Initialize git repo if needed (git init, create main branch)
4. Start HTTP server on port 8400
5. Log: "Supervisor ready on http://0.0.0.0:8400"
```

**Required environment variables:**

| Variable | Required | Default | Notes |
|----------|----------|---------|-------|
| `ANTHROPIC_API_KEY` | MUST | — | Fatal error if missing. |
| `CAMBRIAN_ARTIFACTS_ROOT` | SHOULD | `../cambrian-artifacts` | Path to the artifacts git repo. |
| `DOCKER_HOST` | SHOULD (macOS) | — | On macOS with Docker Desktop, `aiodocker` does not reliably resolve the Docker socket via the default `/var/run/docker.sock` symlink. Set `DOCKER_HOST=unix:///Users/<you>/.docker/run/docker.sock` (or the value of `$HOME/.docker/run/docker.sock`) before starting the Supervisor. On Linux, `unix:///var/run/docker.sock` is the real socket and works without this variable. |
| `CAMBRIAN_CONTAINER_TIMEOUT` | MAY | `600` | Seconds before a container is killed. |
| `CAMBRIAN_SUPERVISOR_PORT` | MAY | `8400` | HTTP port to bind. |
| `CAMBRIAN_DOCKER_IMAGE` | MAY | `cambrian-base` | Docker image name for Test Rig containers. |

**macOS start example:**

```bash
ANTHROPIC_API_KEY=sk-ant-... \
CAMBRIAN_ARTIFACTS_ROOT=../cambrian-artifacts \
DOCKER_HOST=unix://$HOME/.docker/run/docker.sock \
uv run python -m supervisor.supervisor
```

### 1.5 File Layout

```
supervisor/
  supervisor.py      — HTTP server, container lifecycle, git operations
  test_supervisor.py — Unit tests for API endpoints and git operations
```

## 2. Test Rig

### 2.1 Responsibility

A mechanical verification pipeline. Reads `manifest.json` from the artifact, executes each stage in sequence, writes a viability report. No LLM, no agentic loop, no network calls (except health-check to the artifact under test).

### 2.2 Pipeline

Pipeline:

```
read-manifest → build → test → start → health-check → report
```

### 2.3 Implementation Details

**Entry point:** `python /test-rig/test_rig.py`

The Test Rig is baked into the Docker image at `/test-rig/`. It is NOT part of the artifact.

```
Stage 1 — Read Manifest:
  - Read /workspace/manifest.json
  - Validate all MUST fields present and correctly typed per the checklist below
  - Validate cambrian-version == 1
  - If "contracts" is present, validate each contract object against the contract schema (§2.5)
  - Fail: missing file, malformed JSON, missing fields, type mismatch

  Manifest validation checklist (MUST fields):
  - cambrian-version: integer, must equal 1
  - generation: integer, >= 0 (0 is reserved for hand-crafted test artifacts; LLM-generated artifacts MUST use >= 1)
  - parent-generation: integer, >= 0
  - spec-hash: string, matches /^sha256:[0-9a-f]{64}$/
  - artifact-hash: string, matches /^sha256:[0-9a-f]{64}$/
  - producer-model: string, non-empty
  - token-usage: object with integer fields "input" (>= 0) and "output" (>= 0)
  - files: non-empty array of strings, must include "manifest.json"
  - created-at: string, valid ISO-8601 datetime
  - entry.build: string, non-empty
  - entry.test: string, non-empty
  - entry.start: string, non-empty
  - entry.health: string, valid URL (http:// or https://)

Stage 2 — Build:
  - Run entry.build as shell command in /workspace
  - Capture stdout/stderr (used for diagnostics on failure, see §2.6)
  - Timeout: 300 seconds (generous upper bound; `uv pip install` in an uncached container typically completes in under 30s — the timeout exists for pathological dependency trees, not normal cases)
  - Fail: non-zero exit code or timeout

Stage 3 — Test:
  - Run entry.test as shell command in /workspace
  - Capture stdout/stderr to extract test counts AND failure details (see §2.6)
  - Parse pytest output for "N passed" / "N failed" pattern
  - Parse pytest output for individual test failures (see §2.6 for format)
  - Timeout: 120 seconds (a well-written test suite for this codebase size should finish well under 2 minutes)
  - Fail: non-zero exit code or timeout

Stage 4 — Start:
  - Run entry.start as background process
  - Capture stdout/stderr (used for diagnostics on failure, see §2.6)
  - Wait for TCP port (parsed from entry.health URL) to accept connections
  - TCP readiness: poll with `socket.create_connection((host, port), timeout=1)` at 0.5s intervals. The port is ready when the connection succeeds (close it immediately). If the process exits before the port opens, fail immediately without waiting for the full timeout.
  - Timeout: 30 seconds
  - Fail: process exits before port opens, port never accepts connections within 30s, or timeout

Stage 5 — Health Check:
  - If manifest contains a "contracts" array, execute each contract (see §2.5)
  - If manifest does NOT contain "contracts", fall back to hard-coded checks:
    - GET entry.health URL → expect HTTP 200
    - GET /stats on same host:port → expect HTTP 200 with valid JSON containing "generation" field
  - Timeout: 10 seconds per request
  - Fail: any contract fails, or fallback check fails, non-200, invalid JSON, timeout
  - After checks: terminate the started process (SIGTERM, then SIGKILL after 5s)

Report:
  - Write /workspace/viability-report.json per CAMBRIAN-SPEC-005 § Viability Report schema
  - Compute fitness vector from checks data + manifest (see §2.8)
  - If any stage failed, include a diagnostics object (see §2.6)
  - Exit 0 if all stages passed (status=viable)
  - Exit 1 if any stage failed (status=non-viable)
  - Pipeline is fail-fast: if stage N fails, stages N+1..5 are not attempted.
    Their entries MUST still appear in the `checks` dict with `passed: false` and `duration_ms: 0`. This makes the report self-describing — a consumer can determine all 5 stages' outcomes without knowing the pipeline's fail-fast behavior. The `stages_completed` field in the fitness object provides the authoritative list of stages that were actually attempted.
```

**Test count extraction:**

The Test Rig MUST extract test counts from pytest output. It parses stdout for patterns like:
- `N passed` → tests_passed = N
- `N failed` → tests_failed (tests_passed = tests_run - tests_failed)
- `N passed, M failed` → tests_run = N + M, tests_passed = N

If the pattern is not found (non-pytest output), set `tests_run: -1, tests_passed: -1` to signal unknown.

### 2.4 File Layout

```
test-rig/
  test_rig.py       — The verification pipeline
  test_test_rig.py  — Unit tests (mock artifacts, expected reports)
```

### 2.5 Verification Contracts

The manifest MAY include a `contracts` array — a list of declarative checks that the Test Rig executes during the health-check stage. Contracts let the organism declare what it promises; the Test Rig mechanically verifies those promises. The organism defines its own fitness criteria; the environment enforces them.

**Why this matters:** In M1, the health-check stage hard-codes two checks (`GET /health` → 200, `GET /stats` → JSON with `generation`). This works because every M1 organism is a Prime with the same HTTP API. But in M2, when the spec mutates, organisms may expose different endpoints, different schemas, different behavior. Without contracts, every spec mutation would require a corresponding Test Rig change — coupling the organism's evolution to the environment's code. Contracts break that coupling. The organism evolves its spec, declares its contracts, and the Test Rig verifies them without modification.

**Backward compatibility:** Contracts are optional. If `contracts` is absent from the manifest, the Test Rig falls back to the hard-coded health-check behavior (defined in CAMBRIAN-SPEC-005 § Prime HTTP API). This means all existing manifests (including the test artifact) continue to work unchanged. When `contracts` is present, contracts are the sole source of health-check verification — the Test Rig does not supplement with hard-coded checks. The fixed pipeline stages (build → test → start → health → report) are unaffected; contracts only change what happens _within_ the health stage.

#### Contract Schema

Each contract is a JSON object in the `contracts` array:

```json
{
  "contracts": [
    {
      "name": "health-liveness",
      "type": "http",
      "method": "GET",
      "path": "/health",
      "expect": {
        "status": 200,
        "body": {"ok": true}
      }
    },
    {
      "name": "stats-generation",
      "type": "http",
      "method": "GET",
      "path": "/stats",
      "expect": {
        "status": 200,
        "body_contains": {"generation": "$GENERATION"}
      }
    },
    {
      "name": "stats-schema",
      "type": "http",
      "method": "GET",
      "path": "/stats",
      "expect": {
        "status": 200,
        "body_has_keys": ["generation", "status", "uptime"]
      }
    }
  ]
}
```

**Field rules:**

| Field | Required | Rule |
|-------|----------|------|
| `name` | MUST | String. Unique within the contracts array. Used in viability report to identify which contract failed. |
| `type` | MUST | One of: `http`. Future types (e.g., `tcp`, `file`, `exec`) MAY be added without schema-breaking changes. |
| `method` | MUST (for `http`) | HTTP method. One of: `GET`, `POST`. |
| `path` | MUST (for `http`) | URL path. Resolved against the same host:port parsed from `entry.health`. |
| `expect` | MUST | Object describing the expected response. |
| `expect.status` | MUST (for `http`) | Integer. Expected HTTP status code. |
| `expect.body` | MAY | Exact JSON body match. Deep equality. |
| `expect.body_contains` | MAY | Partial JSON body match. Every key-value pair in `body_contains` MUST appear in the response body. Other keys are ignored. |
| `expect.body_has_keys` | MAY | Array of strings. Every key MUST be present in the top-level response body object. Values are not checked. |

**Variable substitution:** The string `"$GENERATION"` in any `expect` value is replaced at runtime with the artifact's `generation` number from the manifest. The substitution produces a value of the correct JSON type: when `$GENERATION` appears as a JSON value (not inside a string), it is replaced with the integer (e.g., `0`), not the string `"0"`. When it appears inside a larger string, it is replaced as text. This allows contracts to reference generation-specific values without hard-coding them.

**Evaluation rules:**

- If multiple `expect` fields are present (`body`, `body_contains`, `body_has_keys`), ALL must pass.
- Contracts are evaluated in array order.
- Contract failure does NOT short-circuit — all contracts are evaluated so the viability report shows which ones passed and which failed. (This differs from the stage-level fail-fast behavior.)
- The health-check stage passes only if ALL contracts pass.
- Timeout per contract: 10 seconds (same as the existing per-request timeout).

#### Contract Results in Viability Report

When contracts are present, the `health` check in the viability report includes a `contracts` sub-object:

```json
{
  "checks": {
    "health": {
      "passed": false,
      "duration_ms": 85,
      "contracts": {
        "health-liveness": {"passed": true, "duration_ms": 12},
        "stats-generation": {"passed": true, "duration_ms": 15},
        "stats-schema": {"passed": false, "duration_ms": 8, "error": "missing key: uptime"}
      }
    }
  }
}
```

Each contract result includes:
- `passed` (boolean) — whether the contract was satisfied
- `duration_ms` (integer) — time to execute the contract check
- `error` (string, optional) — human-readable explanation when `passed` is false

When contracts are absent (fallback mode), the `contracts` sub-object is omitted from the report. Existing report consumers are unaffected.

#### Manifest Extension

The `contracts` field is an optional extension to the Artifact Manifest schema (CAMBRIAN-SPEC-005 § Artifact Manifest). The field rules:

| Field | Required | Rule |
|-------|----------|------|
| `contracts` | SHOULD | Array of contract objects. If absent, Test Rig uses hard-coded health checks. If present, Test Rig evaluates each contract during the health-check stage and does not supplement with hard-coded checks. |

This is a backward-compatible extension — no existing MUST fields change, no existing behavior changes when the field is absent. This extension is incorporated into CAMBRIAN-SPEC-005 § Artifact Manifest (contracts are a SHOULD field in the genome spec).

### 2.6 Structured Diagnostics

When any pipeline stage fails, the Test Rig MUST include a `diagnostics` object in the viability report. Diagnostics capture structured, machine-readable failure context that Prime (or a human) can use to understand *why* a generation failed — not just *that* it failed.

**Why this matters:** Loom achieved a 1.4% viability rate (1 promotion in 72 generations). A major contributor was blind retry — when a generation failed, the next attempt had no structured information about the failure. The LLM re-read the spec and tried again from scratch. Structured diagnostics turn the Test Rig from a Darwinian oracle (binary pass/fail, organism retries blindly) into a Lamarckian teaching signal (structured failure context feeds forward into the next generation's prompt). The organism doesn't mutate randomly — it learns what went wrong.

**Design principle:** The diagnostics section is produced by the environment (Test Rig), stored by the environment (Supervisor, in the generation record), and consumed by the organism (Prime, when constructing the LLM prompt for the next attempt). The organism MUST NOT produce its own diagnostics — only the environment can assess failure. This preserves the "environment judges, not the organism" invariant from CAMBRIAN-SPEC-005 § Invariants.

**Completeness requirement:** The Test Rig MUST populate all MUST fields in the diagnostics object when a stage fails. Returning an empty diagnostics object, returning `null`, or omitting MUST fields is a Test Rig bug, not graceful degradation. A missing `exit_code` or empty `failures` array when tests failed destroys the teaching signal. There is no fallback — diagnostics are all-or-nothing.

#### Diagnostics Schema

The `diagnostics` object is included in the viability report when `status` is `non-viable`:

```json
{
  "status": "non-viable",
  "failure_stage": "test",
  "checks": { "..." : "..." },
  "diagnostics": {
    "stage": "test",
    "summary": "7 of 15 tests failed",
    "exit_code": 1,
    "failures": [
      {
        "test": "test_api::test_spawn_returns_container_id",
        "error": "AssertionError: expected 'lab-gen-1', got None",
        "file": "tests/test_api.py",
        "line": 42
      },
      {
        "test": "test_api::test_promote_creates_tag",
        "error": "KeyError: 'generation'",
        "file": "tests/test_api.py",
        "line": 87
      }
    ],
    "stdout_tail": "...last 100 lines of stdout...",
    "stderr_tail": "...last 100 lines of stderr..."
  }
}
```

**Field rules:**

| Field | Required | Rule |
|-------|----------|------|
| `diagnostics` | MUST (when `status` is `non-viable`) | Object. Absent when `status` is `viable`. |
| `diagnostics.stage` | MUST | String. Same value as `failure_stage`. The stage that failed. |
| `diagnostics.summary` | MUST | String. One-line human-readable summary of the failure. Designed to be useful as the first line of an LLM prompt section. |
| `diagnostics.exit_code` | MUST | Integer or null. The exit code of the failed command. Null for timeout or non-command failures (e.g., manifest validation). |
| `diagnostics.failures` | MUST (for `test` stage) | Array of failure objects. One per failed test. Empty array for non-test stages. |
| `diagnostics.failures[].test` | MUST | String. Fully qualified test name (e.g., `test_module::test_function`). |
| `diagnostics.failures[].error` | MUST | String. The assertion or exception message. Truncated to 500 characters. |
| `diagnostics.failures[].file` | MAY | String. File path relative to `/workspace`. |
| `diagnostics.failures[].line` | MAY | Integer. Line number of the failure. |
| `diagnostics.stdout_tail` | MUST | String. Last 100 lines of stdout from the failed command. Empty string if no output. |
| `diagnostics.stderr_tail` | MUST | String. Last 100 lines of stderr from the failed command. Empty string if no output. |

#### Per-Stage Diagnostics

Each stage produces diagnostics differently:

**manifest** — Validation error. `summary` describes the schema violation (e.g., "missing required field: entry.build"). `failures` is empty. `stdout_tail` and `stderr_tail` are empty. `exit_code` is null.

**build** — Build command failure. `summary` is "build command failed with exit code N" or "build command timed out after 300s". `failures` is empty. `stdout_tail` and `stderr_tail` capture the build output (compiler errors, missing dependencies, pip failures).

**test** — Test suite failure. This is the richest diagnostic stage. `summary` is "N of M tests failed". `failures` contains one entry per failed test, extracted by parsing pytest output (see below). `stdout_tail` and `stderr_tail` capture the full test output.

**start** — Process startup failure. `summary` is "process exited with code N before binding port" or "port 8401 not open after 30s". `failures` is empty. `stdout_tail` and `stderr_tail` capture any output before the process died.

**health** — Health check failure. `summary` is "GET /health returned 503 (expected 200)" or "contract stats-schema failed: missing key: uptime". `failures` is empty. `stdout_tail` and `stderr_tail` capture the running process output up to the point of failure.

#### Pytest Failure Extraction

The Test Rig MUST parse pytest's output to extract individual test failures. Pytest's default output format includes blocks like:

```
FAILED tests/test_api.py::test_spawn_returns_container_id - AssertionError: expected 'lab-gen-1', got None
```

The Test Rig parses these lines using the pattern:

```
FAILED <file>::<test> - <error>
```

For each match, it produces a failure object:
- `test`: `"<file>::<test>"` (e.g., `"tests/test_api.py::test_spawn_returns_container_id"`)
- `error`: `"<error>"` (truncated to 500 characters)
- `file`: extracted from the `<file>` portion
- `line`: extracted from pytest's verbose traceback if available, otherwise omitted

If pytest output does not match this pattern (e.g., a crash before tests run, or non-pytest test runner), `failures` is an empty array. The `stdout_tail` and `stderr_tail` still capture output, so the information is not lost — it is just unstructured.

**Truncation:** Individual `error` strings are capped at 500 characters. `stdout_tail` and `stderr_tail` are capped at 100 lines. These limits prevent the viability report from growing unboundedly on pathological failures (e.g., infinite recursion stack traces) while preserving enough context for an LLM to diagnose the issue.

#### Diagnostics and the Generation Record

The Supervisor stores the full viability report (including diagnostics) in the generation record. When Prime requests generation history via `GET /versions`, it receives all previous diagnostics. Prime SHOULD include diagnostics from the most recent failed generation in the LLM prompt for the next attempt.

The prompt structure is Prime's concern (defined in CAMBRIAN-SPEC-005), but the data flow is:

```
Test Rig writes diagnostics → Supervisor stores in generation record →
Prime reads via GET /versions → Prime includes in LLM prompt →
LLM generates improved code
```

This creates a feedback loop where each failed generation informs the next attempt. The Test Rig's diagnostic output is the teaching signal; the LLM is the learner.

#### Viability Report Extension

The `diagnostics` field is an optional extension to the Viability Report schema (CAMBRIAN-SPEC-005 § Viability Report). The field rules:

| Field | Required | Rule |
|-------|----------|------|
| `diagnostics` | MUST when `non-viable` | Object. Present when `status` is `non-viable`, absent otherwise. Contains structured failure context for the failed stage. |

This is a backward-compatible extension — no existing MUST fields change. When `status` is `viable`, `diagnostics` is absent and existing report consumers are unaffected.

### 2.7 Informed Retry

When a generation fails and Prime has retries remaining (`CAMBRIAN_MAX_RETRIES`), the next attempt SHOULD be an informed retry — the LLM receives not just the spec and diagnostics, but the actual source code of the failed attempt.

**Why this matters:** Diagnostics (§2.6) tell Prime *what* failed. The failed source code tells Prime *where* the bug is. Together, they transform retry from "read the spec and try again from scratch" into "here's what you wrote, here's what broke, fix it." This is the difference between asking a developer to rewrite a program from a spec vs. asking them to debug code they can see. The latter is dramatically more likely to succeed.

**Philosophical reconciliation:** CAMBRIAN-SPEC-005 says "regenerate the entire codebase from scratch each generation." Informed retry does not violate this. The distinction is:

- **Retries** (within `CAMBRIAN_MAX_RETRIES` for the same spec) are **Lamarckian** — the organism learns from failure. The failed code is a scaffold for the fix, not a codebase to accumulate on. Each retry still produces a complete artifact.
- **Generations** (across spec versions in M2) are **Darwinian** — clean-room regeneration from the mutated spec. No previous code is referenced. No path dependence.

Retries fix bugs in an implementation. Generations explore different implementations. These are different evolutionary mechanisms operating at different timescales.

#### Preserving Failed Artifacts

When the Supervisor rolls back a generation, it MUST preserve the failed code as a git tag before deleting the branch:

```python
# rollback (updated):
#   1. Tag the failed branch: git tag -a gen-N-failed -m "Generation N failed"
#   2. Delete the branch: git branch -D gen-N
#   3. Update generation record: outcome=failed, artifact-ref="gen-N-failed"
```

The tag `gen-N-failed` is a permanent, lightweight reference to the failed code. It does not pollute the branch namespace. Tags are cheap in git — thousands of failed-generation tags have negligible cost.

**Naming convention:** Failed generation tags use the pattern `gen-N-failed`. Each retry gets a new generation number (per CAMBRIAN-SPEC-005 Retry Semantics), so each generation number is used exactly once. Tags are therefore always `gen-N-failed` — never `gen-N-failed-2`.

#### Generation Record Extension

The generation record gains an `artifact-ref` field:

| Field | Required | Rule |
|-------|----------|------|
| `artifact-ref` | MAY | String. Git ref (tag name) pointing to the artifact's source tree. Present for both promoted generations (tag `gen-N`) and failed generations (tag `gen-N-failed`). Absent for `in_progress` generations. |

Example generation record for a failed attempt:

```json
{
  "generation": 2,
  "parent": 1,
  "outcome": "failed",
  "artifact-ref": "gen-2-failed",
  "viability": {
    "status": "non-viable",
    "failure_stage": "test",
    "diagnostics": { "...": "..." }
  }
}
```

#### Informed Retry Data Flow

When Prime retries after a failure:

```
1. Prime reads generation history (GET /versions)
   → finds gen-N with outcome=failed, viability.diagnostics={...}

2. Prime reads the failed source code from the local filesystem:
   → files are still on disk from the previous write (Prime's own workspace)
   → reads files listed in the failed manifest's "files" array
   → Prime MUST NOT use git to read files (see CAMBRIAN-SPEC-005 Invariants)

3. Prime constructs the LLM prompt:
   → System: "You are a code generator. Fix the following code based on the test failures."
   → User: [spec] + [failed source code] + [structured diagnostics]

4. LLM produces a corrected complete codebase
   → Prime writes it as a new artifact, NOT a patch on the old one
```

The LLM prompt structure is Prime's concern (defined in CAMBRIAN-SPEC-005). The failed git tag (`gen-N-failed`) is retained for human debugging and historical reference, but Prime reads failed source directly from disk — not from git.

**Important:** Informed retry still produces a complete artifact. The LLM outputs all files, not a diff. This preserves the "complete regeneration" property — the artifact is self-contained and not path-dependent. The failed code is a reference for debugging, not a base for patching.

#### Cleanup

Failed-generation tags accumulate over time. For M1, this is not a concern — tag storage is negligible. For M2+, the Supervisor MAY implement a retention policy (e.g., keep only the last N failed tags per generation lineage). This is explicitly deferred — premature cleanup risks losing debugging context.

### 2.8 Fitness Vector

The Test Rig SHOULD compute a `fitness` object and include it in every viability report. The fitness vector is a quantitative characterization of the artifact — how fast, how correct, how economical, how robust. For M1, fitness is informational. For M2+, it provides the measurement apparatus for selection between viable organisms.

**Why this matters:** Viability is binary — an organism passes or it doesn't. But not all viable organisms are equal. A Prime that builds in 2 seconds, has 20 tests, and uses 10K tokens is fitter than one that builds in 30 seconds, has 3 tests, and uses 100K tokens. Without quantitative fitness data, M2 selection has nothing to work with. By collecting fitness from Gen-1 onward, the system has a historical fitness record from day one — no retrofitting required.

**Data sources:** The fitness vector is computed entirely from data the Test Rig already collects. No additional test execution is needed.

| Metric | Source | Computation |
|--------|--------|-------------|
| `build_duration_ms` | `checks.build.duration_ms` | Direct copy |
| `test_duration_ms` | `checks.test.duration_ms` | Direct copy |
| `test_count` | `checks.test.tests_run` | Direct copy |
| `test_pass_rate` | `checks.test` | `tests_passed / tests_run` |
| `start_duration_ms` | `checks.start.duration_ms` | Direct copy |
| `health_duration_ms` | `checks.health.duration_ms` | Direct copy |
| `total_duration_ms` | All checks | Sum of all stage durations |
| `source_files` | `manifest.files` | Count files NOT matching `test*` or `*_test*` patterns, excluding `manifest.json` |
| `source_lines` | File system | `wc -l` on each source file in `/workspace` |
| `test_files` | `manifest.files` | Count files matching `test*` or `*_test*` patterns |
| `test_lines` | File system | `wc -l` on each test file in `/workspace` |
| `dependency_count` | `/workspace/requirements.txt` | Count non-empty, non-comment lines. `0` if file is empty or absent. |
| `token_input` | `manifest.token-usage.input` | Direct copy |
| `token_output` | `manifest.token-usage.output` | Direct copy |
| `contract_pass_rate` | `checks.health.contracts` | Passed contracts / total contracts. `1.0` if all pass, `0.0` if all fail. **Absent** if manifest contains no `contracts` array (fallback health check mode). In M2, this is the core fitness signal — it measures how well the generated code satisfies its own declared behavioral contracts. |
| `stages_completed` | Pipeline execution | MUST. Array of stage names that were attempted, regardless of whether they passed. Example: `["manifest", "build", "test"]` for a generation that failed at `start`. Always present — for a fully viable run it is `["manifest", "build", "test", "start", "health"]`. |

**Implementation:** Fitness computation is a post-processing step in the Report phase, after all pipeline stages have completed (or failed). The Test Rig:

1. Reads completed `checks` data (durations, test counts)
2. Reads the manifest (`files` array, `token-usage`)
3. Counts lines in source and test files via the filesystem
4. Assembles the `fitness` object
5. Includes it in the viability report

For non-viable reports, fitness MUST include metrics from all completed stages. Metrics from unattempted stages (due to fail-fast) are **absent** from the fitness object — they are never set to zero, because zero is a meaningful value for some metrics (e.g., 0 tests passed). Absent means "not attempted"; zero means "attempted and measured zero."

The fitness object MUST include a `stages_completed` field: an array of stage names that were attempted (regardless of whether they passed). Example: `["manifest", "build", "test"]` for a generation that failed at `start`. This enables **hindsight relabeling** in M2: a generation that fails at `start` but passes `build` and `test` demonstrates partial capability. Binary non-viability is a selection label; partial `stages_completed` is a measurement. Both are recorded.

**Line counting:** The Test Rig counts lines using a simple newline count (`\n`). Binary files are skipped (files that contain null bytes in their first 8KB). This is a rough metric — it measures volume, not complexity — but it's cheap, deterministic, and sufficient for cross-generation comparison.

**Fitness dimensions:**

- **Speed** — `build_duration_ms`, `test_duration_ms`, `start_duration_ms`, `health_duration_ms`, `total_duration_ms`. Faster organisms are cheaper to verify and reproduce.
- **Correctness** — `test_count`, `test_pass_rate`. More tests with higher pass rates indicate more thorough self-verification.
- **Economy** — `token_input`, `token_output`, `source_lines`, `dependency_count`. Organisms that achieve viability with fewer tokens and less code are cheaper to reproduce.
- **Robustness** — `test_files`, `test_lines`, test-to-source ratio (`test_lines / source_lines`). Higher testing density correlates with fewer latent bugs.

**M1 usage:** Fitness data is stored in every generation record. No automated selection occurs in M1.

**M2+ usage:** Selection policies will consume the fitness vector to choose between viable organisms. The specific selection criteria (minimize `total_duration_ms`, maximize `test_count`, Pareto-optimal across dimensions, etc.) are deferred. The measurement apparatus is defined here so historical data exists from Gen-1 onward.

### 2.9 Spec-Derived Verification (Layer 1)

The Test Rig extracts machine-readable acceptance vectors from the spec file and evaluates them during the health stage. This provides unforgeable verification — the vectors live in a FROZEN block that the offspring cannot modify (see CAMBRIAN-SPEC-005 § Verification Layers).

**Why this matters:** In the existing design, the offspring controls its own manifest contracts. A dishonest offspring could declare trivial contracts (`GET /health → 200`) and omit hard ones (`GET /stats` schema validation). Spec vectors are the antidote — they are defined by the spec author, not the offspring, and they cannot be modified by FROZEN-block enforcement.

#### Spec Vector Extraction

The Test Rig reads the spec file from `/workspace`. The spec file path is determined by:

1. If the manifest has a `files` array entry ending in `CAMBRIAN-SPEC-005.md`, use that path.
2. Otherwise, glob `/workspace/**/CAMBRIAN-SPEC-005.md` and use the first match.
3. If no spec file is found, skip spec-vector evaluation (backward-compatible with hand-crafted test artifacts that may not include a spec).

**Parsing algorithm:**

1. Read the spec file as UTF-8 text.
2. Find the region between `<!-- BEGIN FROZEN: acceptance-vectors -->` and `<!-- END FROZEN: acceptance-vectors -->`.
3. Extract all fenced code blocks with the `spec-vector` info string within that region.
4. Parse each code block as YAML. Each YAML document produces one spec vector.
5. Validate each vector against the contract schema (same rules as manifest contracts, see § 2.5).

If the FROZEN markers are not found, the spec has no acceptance vectors — the Test Rig logs a warning and proceeds without them. This makes the feature backward-compatible.

#### Evaluation

Spec vectors are evaluated BEFORE manifest contracts, during the health stage:

```
Health stage:
  1. Evaluate spec vectors (from FROZEN block in spec file)
  2. Evaluate manifest contracts (from manifest.json)
  3. If no spec vectors AND no manifest contracts → fallback health check
```

Spec vectors use the same evaluation rules as manifest contracts: `$GENERATION` substitution, 10-second timeout per vector, no short-circuit. All vectors are evaluated regardless of individual pass/fail.

If ANY spec vector fails, the health stage fails — even if all manifest contracts pass. Spec vectors are the floor; manifest contracts are the ceiling.

#### Viability Report Extension

When spec vectors are evaluated, their results appear in the viability report:

```json
{
  "checks": {
    "health": {
      "passed": false,
      "duration_ms": 120,
      "spec-vectors": {
        "sv-health-liveness": {"passed": true, "duration_ms": 8},
        "sv-health-body": {"passed": true, "duration_ms": 10},
        "sv-stats-schema": {"passed": false, "duration_ms": 12, "error": "missing key: uptime"}
      },
      "contracts": {
        "health-liveness": {"passed": true, "duration_ms": 9}
      }
    }
  }
}
```

The `spec-vectors` sub-object is parallel to the existing `contracts` sub-object. Each entry has the same schema: `{passed, duration_ms, error?}`.

When spec vectors are absent (no FROZEN block in spec), the `spec-vectors` sub-object is omitted. Existing report consumers are unaffected.

#### Fitness Extension

The fitness vector gains a new metric:

| Metric | Source | Computation |
|--------|--------|-------------|
| `spec_vector_pass_rate` | `checks.health.spec-vectors` | Passed vectors / total vectors. **Absent** if no spec vectors evaluated. |

This metric is independent of `contract_pass_rate` (which measures manifest contracts). In M2, `spec_vector_pass_rate` is a stronger fitness signal because spec vectors are not under the offspring's control.

### 2.10 Dual-Blind Examiner (Layer 2, M2)

**This section activates when `CAMBRIAN_MODE=m2` is set.** It has no effect in M1.

The dual-blind examiner is an independent LLM call that generates test cases from the spec alone, without seeing the offspring's code. The Supervisor orchestrates this after the Test Rig container exits. The examiner and the code author share only the spec — they have no channel to collude.

#### Design

```
┌─────────┐     spec      ┌──────────┐
│  Prime   │◄────────────►│   Spec   │
│ (LLM-A)  │              │  (genome) │
│ generates │              └──────┬───┘
│  code    │                     │
└─────────┘                      │ spec (same document)
                                 ▼
                          ┌──────────┐
                          │ Examiner │
                          │ (LLM-B)  │
                          │ generates │
                          │  tests   │
                          └──────────┘
```

**Key property:** LLM-A (code author) and LLM-B (test author) never see each other's output. They share only the spec. If LLM-A writes code that passes LLM-B's tests, the code satisfies an independent reading of the spec.

#### Orchestration

The Supervisor runs the examiner as a post-verification step after the Test Rig container exits and before updating the generation record to `tested`:

1. **Test Rig completes.** Viability report exists.
2. **If Tier >= 1:** The Supervisor calls an LLM (model: `CAMBRIAN_EXAMINER_MODEL`, default: same as `CAMBRIAN_MODEL`) with the prompt:

   > System: You are a test examiner. Given a specification, generate HTTP contract test cases that a correct implementation MUST pass. Output ONLY a JSON array of contract objects (same schema as manifest contracts). Generate at least 5 test cases covering different requirements from the spec.
   >
   > User: [spec file content]

3. **Parse response** as a JSON array of contract objects.
4. **Run the examiner's contracts** against the still-running offspring process (if it's still up from the Test Rig's health stage — see implementation note below) or by restarting it.
5. **Record results** in the generation record under `examiner-results`:

```json
{
  "examiner-results": {
    "model": "claude-sonnet-4-6",
    "contracts-generated": 7,
    "contracts-passed": 5,
    "contracts-failed": 2,
    "failures": [
      {"name": "exam-missing-error-format", "error": "status 200 != expected 400"}
    ],
    "pass-rate": 0.714
  }
}
```

**Implementation note:** The Test Rig kills the offspring process after the health stage. For the examiner, the Supervisor has two options: (a) modify the Test Rig to leave the process running when Tier >= 1 (requires a new env var `CAMBRIAN_KEEP_PROCESS=1`), or (b) restart the offspring in a new container using the same artifact. Option (b) is simpler and more robust — it doesn't couple the Test Rig to the verification layer system.

#### Fitness Extension

| Metric | Source | Computation |
|--------|--------|-------------|
| `examiner_pass_rate` | `examiner-results` | Passed / generated. **Absent** if examiner not run (M1 or Tier 0). |

#### Configuration

| Variable | Required | Default | Purpose |
|----------|----------|---------|---------|
| `CAMBRIAN_EXAMINER_MODEL` | MAY | Same as `CAMBRIAN_MODEL` | LLM model for the examiner. Using a different model than Prime reduces the chance of correlated blind spots. |

### 2.11 Adversarial Red-Team (Layer 3, M2)

**This section activates when `CAMBRIAN_MODE=m2` is set AND Tier >= 2.** It has no effect in M1 or Tier 0/1.

The adversarial red-team is an independent LLM call that receives both the spec AND the offspring's source code. Its goal is to find violations — spec non-compliance, edge case failures, error handling gaps. It produces failing test cases. If any test case reveals a real bug (the offspring fails the test), the fitness score is penalized.

#### Design

Unlike the examiner (which sees only the spec), the red-team sees the actual code. This is strictly more powerful — it can find implementation-specific bugs that a spec-only reader would miss. But it's also more expensive (larger prompt) and only valuable once the offspring consistently passes spec-level verification (Tier 2+).

#### Orchestration

1. **Examiner completes** (if applicable).
2. **If Tier >= 2:** The Supervisor calls an LLM (model: `CAMBRIAN_REDTEAM_MODEL`, default: same as `CAMBRIAN_ESCALATION_MODEL`) with the prompt:

   > System: You are a security and correctness auditor. Given a specification and its implementation, find violations: places where the code doesn't match the spec, handles edge cases incorrectly, or could fail under realistic conditions. For each violation, produce an HTTP contract test case that demonstrates the bug. Output ONLY a JSON array of contract objects.
   >
   > User: [spec file content] + [all source files from the artifact]

3. **Run the red-team's contracts** against the offspring (same restart-in-new-container approach as the examiner).
4. **Record results** in the generation record under `redteam-results`:

```json
{
  "redteam-results": {
    "model": "claude-opus-4-6",
    "contracts-generated": 10,
    "contracts-passed": 3,
    "contracts-failed": 7,
    "violations-confirmed": 2,
    "violations": [
      {"name": "rt-missing-error-body", "description": "POST /spawn with empty body returns 500 instead of 400", "severity": "medium"}
    ],
    "score": 0.8
  }
}
```

**Scoring:** `violations-confirmed` counts red-team test cases where the offspring actually failed (the bug is real, not a false positive from the red-team). `score = 1.0 - (violations_confirmed / contracts_generated)`. A score of 1.0 means the red-team found no real bugs. A score below `CAMBRIAN_REDTEAM_THRESHOLD` (default 0.7) penalizes the offspring's fitness.

#### Fitness Extension

| Metric | Source | Computation |
|--------|--------|-------------|
| `redteam_score` | `redteam-results.score` | 1.0 - (violations / generated). **Absent** if red-team not run. |
| `redteam_violations` | `redteam-results.violations-confirmed` | Integer count. **Absent** if red-team not run. |

#### Configuration

| Variable | Required | Default | Purpose |
|----------|----------|---------|---------|
| `CAMBRIAN_REDTEAM_MODEL` | MAY | Same as `CAMBRIAN_ESCALATION_MODEL` | LLM model for the red-team. Using the strongest model available maximizes bug-finding capability. |
| `CAMBRIAN_REDTEAM_THRESHOLD` | MAY | `0.7` | Minimum red-team score. Below this, the offspring's fitness is penalized (the penalty mechanism is defined by the selection policy in M2). |

## 3. Docker Infrastructure

### 3.1 Base Image

A single Dockerfile produces the `cambrian-base` image used by all containers.

```dockerfile
FROM python:3.14-slim

# Never write .pyc files — the container is ephemeral, bytecode caching has
# no benefit, and it prevents __pycache__/ from leaking into the bind-mounted
# artifact workspace on the host.
ENV PYTHONDONTWRITEBYTECODE=1

# Install uv — used for all dependency installation (host parity, 10-100x faster than pip)
COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

# Create virtual environment via uv (activated for all subsequent commands)
RUN uv venv /venv
ENV PATH="/venv/bin:$PATH"

# Test Rig lives in the image, not the artifact
COPY test-rig/ /test-rig/

# Working directory for artifacts
WORKDIR /workspace

# Default entrypoint: run the Test Rig
ENTRYPOINT ["python", "/test-rig/test_rig.py"]
```

**Notes:**

- `python:3.14-slim` is the standard Python 3.14 image. Free-threaded build (3.14t) is deferred to M2.
- `PYTHONDONTWRITEBYTECODE=1` MUST be set. Without it, every `pytest` and `python` invocation writes `__pycache__/` directories into `/workspace`, which leak through the bind mount onto the host filesystem. The container is ephemeral — bytecode caching provides no benefit.
- `uv` is copied from its official image (`ghcr.io/astral-sh/uv:latest`) as a single binary. It is used for all dependency installation — both the Test Rig's own deps at image build time and the artifact's `entry.build` command at runtime. Never use raw `pip` or `python -m venv` inside containers.
- A virtual environment at `/venv` is created at image build time via `uv venv` and activated via `PATH`. All `uv pip install` commands (including `entry.build`) install into `/venv`, never the system Python.
- The Test Rig is baked in so every container can verify artifacts without additional setup.
- When running Prime (not the Test Rig), the entrypoint is overridden: `docker run --entrypoint python cambrian-base src/prime.py`.

### 3.2 Networking

All containers use Docker's default bridge network:

- **Outbound HTTPS** (port 443): required for LLM API calls. Docker bridge allows this by default.
- **Host access**: containers reach the Supervisor via `host.docker.internal:8400` (Docker Desktop on macOS) or `172.17.0.1:8400` (Linux default gateway). The Supervisor URL is passed as environment variable `CAMBRIAN_SUPERVISOR_URL`.
- **DNS**: Docker bridge provides DNS resolution for external hosts.

No custom Docker networks are needed for M1.

**Docker socket (macOS):** On macOS with Docker Desktop, the default `/var/run/docker.sock` is a symlink that `aiodocker` does not reliably follow when inspecting images or creating containers. The Supervisor MUST be started with `DOCKER_HOST=unix://$HOME/.docker/run/docker.sock` on macOS. Without this, `aiodocker.Docker().images.inspect()` raises an exception and `/spawn` returns `{"ok": false, "error": "Docker image not found"}` even though the image exists. This is a Docker Desktop behaviour, not an `aiodocker` bug — the real socket for Docker Desktop lives at `~/.docker/run/docker.sock`. On Linux, `/var/run/docker.sock` is the real socket and no override is needed.

### 3.3 Credential Injection

Credentials are injected via environment variables at container creation:

```python
config = {
    "Image": "cambrian-base",
    "Env": [
        f"ANTHROPIC_API_KEY={os.environ['ANTHROPIC_API_KEY']}",
        "CAMBRIAN_SUPERVISOR_URL=http://host.docker.internal:8400",
    ],
    "HostConfig": {
        "Binds": ["/path/to/artifact:/workspace:rw"],
    },
}
container = await client.containers.create_or_replace(name="lab-gen-N", config=config)
```

The Supervisor reads `ANTHROPIC_API_KEY` from its own environment and passes it via the `Env` config. The key is never written to disk inside the container, never committed to git, never included in artifacts.

### 3.4 Workspace Mount

The artifact directory is bind-mounted at `/workspace`:

- Read-write: the Test Rig writes `viability-report.json` back to this directory.
- The Supervisor reads the report from the host-side path after the container exits.

### 3.5 File Layout

```
docker/
  Dockerfile         — Base image (Python 3.14-slim + Test Rig)
  build.sh           — Build the cambrian-base image
```

## 4. Test Artifact

A hand-crafted minimal artifact used to validate infrastructure before Prime exists. It is a trivial Python HTTP server that satisfies the manifest contract.

### 4.1 Purpose

The test artifact proves:
- The Supervisor can spawn a container, mount an artifact, and collect a viability report
- The Test Rig can read a manifest, build, test, start, health-check, and write a report
- Docker networking, credential injection, and workspace mounting work correctly
- Promote and rollback git operations work

### 4.2 Location

The test artifact lives in the **artifacts repository** as `gen-0/`. It is the zeroth generation — hand-crafted, not LLM-generated. The artifacts repo is separate from the Cambrian project repo and is initialized during Phase 0.

### 4.3 Contents

```
cambrian-artifacts/   ← separate git repo (CAMBRIAN_ARTIFACTS_ROOT)
  gen-0/
    manifest.json       — Valid manifest pointing to the test server
    src/server.py       — Trivial HTTP server: /health → 200, /stats → JSON
    tests/test_server.py — pytest: GET /health, assert 200
    requirements.txt    — Empty (stdlib only)
```

**manifest.json:**

```json
{
  "cambrian-version": 1,
  "generation": 0,
  "parent-generation": 0,
  "spec-hash": "sha256:0000000000000000000000000000000000000000000000000000000000000000",
  "artifact-hash": "sha256:0000000000000000000000000000000000000000000000000000000000000000",
  "producer-model": "hand-crafted",
  "token-usage": {"input": 0, "output": 0},
  "files": [
    "manifest.json",
    "src/server.py",
    "tests/test_server.py",
    "requirements.txt"
  ],
  "created-at": "2026-03-21T00:00:00Z",
  "entry": {
    "build": "pip install -r requirements.txt",
    "test": "python -m pytest tests/ -v",
    "start": "python src/server.py",
    "health": "http://localhost:8401/health"
  },
  "contracts": [
    {
      "name": "health-liveness",
      "type": "http",
      "method": "GET",
      "path": "/health",
      "expect": {
        "status": 200,
        "body": {"ok": true}
      }
    },
    {
      "name": "stats-schema",
      "type": "http",
      "method": "GET",
      "path": "/stats",
      "expect": {
        "status": 200,
        "body_has_keys": ["generation", "status", "uptime"]
      }
    },
    {
      "name": "stats-generation",
      "type": "http",
      "method": "GET",
      "path": "/stats",
      "expect": {
        "status": 200,
        "body_contains": {"generation": "$GENERATION"}
      }
    }
  ]
}
```

**src/server.py:**

```python
# Minimal HTTP server satisfying the Prime HTTP API contract
# GET /health → 200, {"ok": true}
# GET /stats  → 200, {"generation": 0, "status": "idle", "uptime": N}

import json
import time
from http.server import HTTPServer, BaseHTTPRequestHandler

START_TIME = time.time()

class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path == "/health":
            self._json_response(200, {"ok": True})
        elif self.path == "/stats":
            self._json_response(200, {
                "generation": 0,
                "status": "idle",
                "uptime": int(time.time() - START_TIME)
            })
        else:
            self._json_response(404, {"error": "not found"})

    def _json_response(self, status, body):
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(json.dumps(body).encode())

    def log_message(self, format, *args):
        pass  # Suppress request logging

if __name__ == "__main__":
    server = HTTPServer(("0.0.0.0", 8401), Handler)
    print("Test server listening on port 8401", flush=True)
    server.serve_forever()
```

**tests/test_server.py:**

```python
import json
import subprocess
import time
import urllib.request

import pytest

SERVER_PROC = None

@pytest.fixture(scope="module", autouse=True)
def server():
    global SERVER_PROC
    SERVER_PROC = subprocess.Popen(
        ["python", "src/server.py"],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE
    )
    time.sleep(1)  # Wait for server to start
    yield
    SERVER_PROC.terminate()
    SERVER_PROC.wait()

def test_health():
    resp = urllib.request.urlopen("http://localhost:8401/health")
    assert resp.status == 200
    body = json.loads(resp.read())
    assert body["ok"] is True

def test_stats():
    resp = urllib.request.urlopen("http://localhost:8401/stats")
    assert resp.status == 200
    body = json.loads(resp.read())
    assert "generation" in body
    assert "status" in body
    assert "uptime" in body
    assert body["generation"] == 0

def test_not_found():
    try:
        urllib.request.urlopen("http://localhost:8401/nonexistent")
        assert False, "Expected 404"
    except urllib.error.HTTPError as e:
        assert e.code == 404
```

### 4.4 Validation

After Phase 0 is built, run this sequence:

```
1. Build Docker image:        docker/build.sh
2. Start Supervisor:          python supervisor/supervisor.py
3. Spawn test artifact:       curl -X POST http://localhost:8400/spawn \
                                -H "Content-Type: application/json" \
                                -d '{"spec-hash":"sha256:000...","generation":0,"artifact-path":"gen-0"}'
4. Wait for container to exit
5. Check viability report:    cat $CAMBRIAN_ARTIFACTS_ROOT/gen-0/viability-report.json
   → Expect: status=viable, all checks passed
6. Promote:                   curl -X POST http://localhost:8400/promote \
                                -H "Content-Type: application/json" \
                                -d '{"generation":0}'
7. Verify git:                git -C $CAMBRIAN_ARTIFACTS_ROOT tag → gen-0 exists
8. Rollback test:             Re-spawn, then rollback instead of promote
   → Verify branch deleted, record shows outcome=failed
```

## 5. Bootstrap Sequence

### Stage 0: Build Infrastructure

**Who:** Human + Claude Code (interactive)

**Steps:**
1. Create project directory structure
2. Initialize project with `uv init` and `pyproject.toml`
3. Create venv and install dependencies (`uv sync`)
4. Implement the Supervisor (`supervisor/supervisor.py`)
5. Implement the Test Rig (`test-rig/test_rig.py`)
6. Create the Dockerfile (`docker/Dockerfile`)
7. Initialize the artifacts repo and create gen-0 (the test artifact)
8. Build the Docker image
9. Run the test artifact through the full pipeline
10. Fix any issues until the test artifact passes end-to-end

**Done when:** Test artifact is spawned, passes all Test Rig stages, viability report shows `status: viable`, promote and rollback both work correctly.

### Stage 1: Generate Gen-1 Prime

**Who:** Human + Claude Code (interactive)

**Steps:**
1. Write CAMBRIAN-SPEC-005 (the genome spec — separate document)
2. Use Claude Code to generate Prime source code from CAMBRIAN-SPEC-005
3. Create `manifest.json` for the generated artifact
4. Spawn the artifact through the Supervisor
5. Run the Test Rig
6. Fix issues, regenerate if needed
7. Promote as Gen-1

**Done when:** Gen-1 Prime passes the Test Rig and is promoted. `git -C $CAMBRIAN_ARTIFACTS_ROOT tag` shows `gen-1`. Generation record shows `outcome: promoted`.

### Stage 2: Verify Reproduction

**Who:** Gen-1 Prime (autonomous, observed by human)

**Starting Gen-1 autonomously:** The Supervisor's `/spawn` endpoint creates Test Rig containers, not Prime containers. To start Gen-1 Prime for autonomous reproduction, the human runs it directly via Docker:

```bash
docker run --rm \
  -v "$(pwd)/workspace:/workspace" \
  -v "$(pwd)/spec:/workspace/spec:ro" \
  -e ANTHROPIC_API_KEY="$ANTHROPIC_API_KEY" \
  -e CAMBRIAN_SUPERVISOR_URL="http://host.docker.internal:8400" \
  --add-host host.docker.internal:host-gateway \
  --entrypoint python \
  cambrian-base /workspace/src/prime.py
```

Adjust the `--entrypoint` and source path to match where Gen-1's entrypoint lives in the artifact. In M2+, the Supervisor may gain a `/start` endpoint for this, but for M1 the bootstrap is an explicitly human-supervised process.

**Steps:**
1. Start Gen-1 Prime in a container with the full genome spec (see above)
2. Gen-1 reads the spec, calls an LLM, generates Gen-2 into `/workspace/gen-2/`
3. Gen-1 calls POST /spawn; Supervisor creates branch, runs Test Rig
4. If viable: Gen-1 calls POST /promote
5. Human starts Gen-2 Prime with the Minimal Spec path configured
6. Gen-2 generates Gen-3 (echo server)
7. Gen-3 is spawned and tested
8. If viable: M1 is complete

**Done when:** The M1 acceptance criteria from CAMBRIAN-SPEC-005 § Acceptance Criteria → Reproductive are met.

## 6. Minimal Spec (M1 Operationality Test)

The Minimal Spec is a small specification used to verify that a reproduced Prime is operational — that it can read a spec, call an LLM, and produce viable code — without requiring full self-reproduction.

This is the termination criterion for the M1 acceptance test. Gen-2 Prime is given this spec instead of the full genome spec. The echo server it produces is verified by the Test Rig, then the chain terminates (the echo server cannot reproduce).

### Why This Spec

- **Small enough** to be cheap (few hundred tokens of LLM output)
- **Exercises the full pipeline**: spec → LLM → code → build → test → start → health check
- **Uses the same Test Rig**: the artifact includes a valid `manifest.json`, so the standard verification pipeline works unchanged
- **Proves operationality**: Prime can read a spec, call an LLM, write files, create a manifest, and request verification
- **Does not recurse**: the echo server is not a Prime, so the chain terminates

### Minimal Spec Content

```
Produce an HTTP server with the following behavior:

- GET /health → 200 OK, body: {"ok": true}
- GET /echo?msg=X → 200 OK, body: {"echo": "X"}
- GET /echo with no msg parameter → 400, body: {"error": "missing msg parameter"}

The server MUST:
- Listen on port 8401
- Respond with Content-Type: application/json
- Include a test suite that verifies all three endpoints

This server does NOT implement /stats.
```

The contracts block below is parsed by Prime and included verbatim in manifest.json
(see CAMBRIAN-SPEC-005 § The Generation Loop step 6). This is required because the
echo server has no /stats endpoint and the fallback health check would fail without
contracts.

```contracts
[
  {"name": "health", "type": "http", "method": "GET", "path": "/health",
   "expect": {"status": 200, "body": {"ok": true}}},
  {"name": "echo-valid", "type": "http", "method": "GET", "path": "/echo?msg=hello",
   "expect": {"status": 200, "body_contains": {"echo": "hello"}}},
  {"name": "echo-missing", "type": "http", "method": "GET", "path": "/echo",
   "expect": {"status": 400}}
]
```

### How to Use

In Stage 2, after Gen-2 Prime is promoted, set `CAMBRIAN_SPEC_PATH` to point to a file containing the Minimal Spec content above. Start Gen-2 Prime. It generates Gen-3 (the echo server). If Gen-3 passes the Test Rig, M1 is complete.

## 7. Project Directory Structure


After bootstrap is complete, two sibling repos exist:

```
cambrian/                       ← project repo (this repo)
  supervisor/
    supervisor.py               — Supervisor HTTP server
    test_supervisor.py          — Supervisor unit tests
  test-rig/
    test_rig.py                 — Test Rig verification pipeline
    test_test_rig.py            — Test Rig unit tests
  docker/
    Dockerfile                  — Base image
    build.sh                    — Image build script
  spec/
    CAMBRIAN-SPEC-005.md        — Genome spec (Prime definition)
    BOOTSTRAP-SPEC-002.md       — This document
    SPEC-STYLE-GUIDE.md         — Spec writing guide
    diagrams/                   — Architecture and sequence diagrams
    archive/                    — Superseded specs (SPEC-001 through 004, BOOTSTRAP-SPEC-001)
  lab-journal/
    journal-*.md                — Discussion and decision logs
  pyproject.toml                — Project metadata, dependencies, tool configs (ruff, pyright)
  uv.lock                       — Locked dependency versions (committed)
  .venv/                        — Host-side virtual environment (not committed)
  .env                          — ANTHROPIC_API_KEY (not committed)
  pyrightconfig.json            — Pyright strict mode configuration
  README.md
  CLAUDE.md

cambrian-artifacts/             ← artifacts repo (CAMBRIAN_ARTIFACTS_ROOT, separate git repo)
  gen-0/                        — Hand-crafted test artifact (generation 0)
    manifest.json
    src/server.py
    tests/test_server.py
    requirements.txt
  gen-1/                        — LLM-generated Prime (created during Stage 1)
  gen-2/                        — Created by Gen-1 Prime autonomously
  generations.json              — Generation history (append-only, managed by Supervisor)
```

## 8. Configuration

All configuration is via environment variables on the host.

| Variable | Required | Default | Purpose |
|----------|----------|---------|---------|
| `ANTHROPIC_API_KEY` | MUST | — | LLM API key, forwarded to containers |
| `CAMBRIAN_SUPERVISOR_PORT` | MAY | `8400` | Supervisor listen port |
| `CAMBRIAN_SUPERVISOR_URL` | MAY | `http://host.docker.internal:8400` | Supervisor URL passed to containers |
| `CAMBRIAN_DOCKER_IMAGE` | MAY | `cambrian-base` | Docker image name |
| `CAMBRIAN_ARTIFACTS_ROOT` | MAY | `../cambrian-artifacts` | Path to artifacts repository (separate git repo) |
| `CAMBRIAN_CONTAINER_TIMEOUT` | MAY | `600` | Max seconds to wait for the Test Rig container to exit. On timeout, container is killed and generation record is set to `outcome: timeout`. |

## 9. Failure Modes

| Failure | Trigger | Response |
|---------|---------|----------|
| Docker not installed | `import aiodocker` fails | Fatal error on Supervisor startup. Message: "aiodocker is required. Install with: pip install aiodocker" |
| Docker daemon not running | `aiodocker.Docker()` connection fails | Fatal error on Supervisor startup. Message: "Docker daemon is not running" |
| Port 8400 in use | Supervisor bind fails | Fatal error. Message: "Port 8400 is already in use" |
| Port 8401 in use inside container | Test artifact or Prime bind fails | Test Rig reports `failure_stage: start` |
| ANTHROPIC_API_KEY missing | Env var not set | Fatal error on Supervisor startup. Message: "ANTHROPIC_API_KEY is required" |
| Docker image not built | `await client.images.inspect("cambrian-base")` raises `DockerError` (404) | `/spawn` returns `{"ok": false, "error": "Docker image cambrian-base not found. Run docker/build.sh"}` |
| Artifact path doesn't exist | `/spawn` with invalid path | `{"ok": false, "error": "Artifact path does not exist: /path"}` |
| Container fails to start | `await client.containers.create_or_replace()` raises `DockerError` | `{"ok": false, "error": "Container creation failed: ..."}` |
| Viability report missing | Container exits without writing report | Generation record: `outcome: failed`, viability: `null`. Container logs are still captured for debugging. |
| Git branch conflict | gen-N branch already exists in artifacts repo | `/spawn` returns error. Caller must rollback the existing gen-N first. |
| Contract check failure | HTTP contract returns unexpected status/body | Test Rig records `failure_stage: health`. All contracts are still evaluated; per-contract results included in viability report. |
| Malformed contracts | `contracts` array contains invalid contract objects | Test Rig records `failure_stage: manifest`. Contract schema is validated during manifest parsing. |
| Pytest output unparseable | Test output doesn't match expected patterns | `diagnostics.failures` is empty array. `stdout_tail` and `stderr_tail` still capture raw output. No information lost, just unstructured. |
| Failed tag already exists | `gen-N-failed` tag exists (should not happen — each retry is a new generation number) | Supervisor logs a warning and overwrites the tag. |
| Failed source unreadable | Prime cannot read files from local filesystem after a failed attempt | Non-fatal. Prime falls back to blind retry (spec + diagnostics only, no source code). |
| Pathological output volume | Failed command produces megabytes of output | `stdout_tail` and `stderr_tail` capped at last 100 lines. Individual `error` strings capped at 500 characters. Viability report size stays bounded. |

## 10. Validation

### Mechanical checks (Phase 0 acceptance)

- Supervisor starts on port 8400 and responds to `GET /stats`
- `POST /spawn` with test artifact creates a Docker container
- Container runs the Test Rig to completion
- Test Rig writes valid `viability-report.json`
- `POST /promote` merges branch and creates annotated tag in the artifacts repo
- `POST /rollback` creates a `gen-N-failed` tag and deletes the branch in the artifacts repo
- `GET /versions` returns all generation records
- Generation records are append-only (re-running doesn't overwrite)
- Supervisor rejects spawn when Docker image is missing
- Supervisor rejects spawn when artifact path doesn't exist
- Test Rig fails fast: build failure skips test/start/health stages
- Test Rig extracts test counts from pytest output
- Container receives `ANTHROPIC_API_KEY` via environment variable
- Container can reach external HTTPS endpoints (LLM API)
- Container can reach Supervisor on host network
- Test Rig evaluates manifest contracts when present (test artifact includes contracts)
- Test Rig falls back to hard-coded checks when contracts are absent
- Contract results appear in viability report under `checks.health.contracts`
- Contract failure marks health-check stage as failed
- All contracts are evaluated even when one fails (no short-circuit within contracts)
- Non-viable viability reports include `diagnostics` object
- Viable viability reports do NOT include `diagnostics`
- `diagnostics.failures` contains one entry per failed test (parsed from pytest output)
- `diagnostics.stdout_tail` and `diagnostics.stderr_tail` are capped at 100 lines
- `diagnostics.failures[].error` strings are capped at 500 characters
- Diagnostics are preserved in generation records via `GET /versions`
- Rollback creates `gen-N-failed` tag before deleting the branch
- Generation records include `artifact-ref` pointing to the failed tag
- Prime reads failed source code from local filesystem (not from git)
- Each retry gets a new generation number; `gen-N-failed` tags are never suffixed
- Viable viability reports include `fitness` object with all metrics
- Non-viable viability reports include `fitness` with partial metrics (completed stages only)
- `fitness.test_pass_rate` equals `tests_passed / tests_run` from checks
- `fitness.source_files` + `fitness.test_files` <= total files in manifest (excluding manifest.json)
- `fitness.total_duration_ms` equals sum of individual stage durations
- Fitness data is preserved in generation records via `GET /versions`

### Behavioral checks (code review)

- Supervisor error messages are specific and actionable
- No stack traces exposed in HTTP responses
- Test Rig output is deterministic for the same artifact
- Git operations are clean — no partial merges, no orphaned branches
- Viability report timestamps are monotonically increasing
- Docker containers are cleaned up after test completion
- Container stdout/stderr captured and attached to generation records
- Diagnostics summaries are specific enough for an LLM to act on (not just "test failed")
- Pytest failure extraction handles edge cases gracefully (crash before tests, non-pytest runners)
- Informed retry produces a complete artifact, not a patch — no path dependence
- Failed artifact tags are never deleted during M1 (debugging context preserved)
- Fitness metrics are consistent across runs for the same artifact (deterministic)
- `ruff check` and `ruff format --check` pass with zero errors
- `pyright` in strict mode passes with zero errors
- All async tests run via `pytest-asyncio` without event loop warnings
- `uv.lock` is committed and `uv sync` reproduces the environment

## 11. References

- [CAMBRIAN-SPEC-005](CAMBRIAN-SPEC-005.md) — Genome spec (Prime definition, schemas, contracts)
- [SPEC-STYLE-GUIDE](SPEC-STYLE-GUIDE.md) — Spec writing conventions
- [Loom final retrospective](https://github.com/lispmeister/loom/blob/master/architecture-reviews/review-2026-03-20-001.md) — Lessons from the predecessor project

---

```yaml
spec-version: "002"
version: "0.9.0"
spec-type: "bootstrap"
ancestor: "BOOTSTRAP-SPEC-001"
language: "python 3.14"
```

---

*This spec is scaffolding. Once the bootstrap is complete and Prime is self-hosting, this document becomes historical. The living contract is the genome spec (CAMBRIAN-SPEC-005).*
