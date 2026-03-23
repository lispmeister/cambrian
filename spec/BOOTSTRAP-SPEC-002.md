---
date: 2026-03-23
author: Markus Fix <lispmeister@gmail.com>
title: "Cambrian Bootstrap: Supervisor, Test Rig, and First Prime"
tags: [cambrian, bootstrap, supervisor, test-rig, docker, M1, contracts, diagnostics]
parent-spec: CAMBRIAN-SPEC-004
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

The Cambrian system has no running code yet. The spec (CAMBRIAN-SPEC-004) defines what the system should be, but there is no Supervisor to manage containers, no Test Rig to verify artifacts, and no Prime to generate code. All three must be built from scratch before the self-reproducing loop can begin.

The bootstrap is a chicken-and-egg problem: Prime needs the Supervisor and Test Rig to verify its offspring, but nobody exists yet to build them. The solution is human + agent collaboration for the initial build, then hand-off to the autonomous loop.

## Goals

- Build a working Supervisor that implements the full HTTP API from CAMBRIAN-SPEC-004.
- Build a working Test Rig that executes the verification pipeline from CAMBRIAN-SPEC-004.
- Create Docker infrastructure (base images, networking, credential injection).
- Verify the infrastructure end-to-end with a hand-crafted test artifact before generating Prime.
- Generate Gen-1 Prime interactively and promote it through the standard pipeline.
- Leave a clean, documented state for CAMBRIAN-SPEC-005 to take over.

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

All Python code — Supervisor, Test Rig, and generated artifacts — MUST run inside a virtual environment. The host-side venv is created and managed by `uv` (`uv venv`, `uv sync`). Inside containers, the Dockerfile creates a venv at `/venv` and activates it. The `entry.build` command in artifacts installs into the container's venv, never into the system Python. This prevents dependency conflicts and keeps the system Python clean.

### Type safety

All Python code MUST be fully type-annotated. Type checking is enforced in CI — type errors fail the build.

Rules:
- **Annotate aggressively.** Every function signature, every return type, every class attribute. Start strict from day one — retrofitting annotations is expensive.
- **Type checker:** Pyright in strict mode. Configured via `pyrightconfig.json` at project root. Zero errors tolerated in CI. Migration path: switch to `ty` (Astral, Rust-based, 10-60x faster) when its Pydantic plugin ships ([astral-sh/ty#2403](https://github.com/astral-sh/ty/issues/2403)). No annotation changes required — both consume the same type syntax.
- **I/O boundary validation and JSON handling:** Pydantic v2 for all data crossing process or network boundaries — manifest parsing, HTTP request/response bodies, viability reports, generation records. Pydantic models are the single source of truth for schemas defined in CAMBRIAN-SPEC-004. All JSON deserialization at boundaries MUST use `model_validate_json()`, all serialization MUST use `model_dump_json()`. Raw `json.loads()`/`json.dumps()` MUST NOT be used at I/O boundaries — only for internal formatting where no validation is needed.
- **Precise constructs:** Prefer `Protocol` over abstract base classes, `TypedDict` for JSON-shaped data, `Literal` for enums with few values (e.g., `Literal["viable", "non-viable"]`), `Self` for fluent APIs. Avoid `Any` — if a type is truly unknown, use `object` and narrow with `isinstance`.
- **No runtime cost:** Type annotations are erased at runtime. Pydantic validation happens at I/O boundaries only, not on internal function calls.

### Runtime introspection

This is experimental code. All components MUST be built for introspection and live debugging. Use `inspect` (stdlib) and `typing-inspect` for runtime type and call-stack introspection. Use `devtools` and `rich` for pretty-printing complex objects (Pydantic models, generation records, viability reports) to the console during development.

Concrete expectations:
- Every major object (Prime, Supervisor state, generation records) MUST have a `rich`-compatible `__repr__` or implement `__rich_repr__` for readable console output.
- Debug endpoints on the Supervisor (e.g., `GET /debug/state`) SHOULD dump internal state as formatted JSON using `rich` in development mode.
- Generation failures MUST log the full call stack and relevant object state using `inspect.stack()` and `devtools.debug()`, not just an error message.
- Pydantic models can be introspected at runtime via `model.model_fields`, `model.model_json_schema()` — use this for self-documenting APIs and debug output.

### Structured logging everywhere

All components — Supervisor, Test Rig, and Prime — MUST use `structlog` for logging. Logs are JSON in production (machine-parseable) and key-value in development (human-readable). Every log line includes at minimum: `timestamp`, `level`, `event`, and `component` (one of `supervisor`, `test-rig`, `prime`). Generation-scoped operations MUST include `generation` in the log context. This enables robust log parsing, aggregation, and debugging across containers.

### Asyncio by default

All I/O-bound code MUST use `asyncio`. The Supervisor uses `asyncio` for its HTTP server and container lifecycle management. Prime uses `asyncio` to issue concurrent LLM calls — for example, generating subsystems in parallel when the spec permits it. Combined with free-threaded Python 3.14t, this gives both cooperative concurrency (asyncio) for I/O and true parallelism (threads) for CPU work. The Test Rig is the exception — it runs a sequential pipeline and does not need asyncio.

### One Dockerfile, parameterized

A single base Docker image serves all containers (Test Rig, Prime, offspring). The entry point and mounted workspace change per use case. This reduces the surface area for container configuration bugs.

### Fail loud, fail early

Every component validates its inputs on startup. Missing `ANTHROPIC_API_KEY` is a fatal error with a clear message, not a silent `None` that surfaces as a cryptic 401 three levels deep.

## Model

All terms are defined in CAMBRIAN-SPEC-004 § Model. This spec adds:

- **Test Artifact** — A hand-crafted, minimal Python HTTP server used to validate infrastructure before Prime exists. Not a Prime. Disposable after Phase 0.
- **Bootstrap Agent** — The human + Claude Code pair performing the interactive build. Not part of the running system.

## 1. Supervisor

### 1.1 Responsibility

The Supervisor is a host-side HTTP server. It runs on the host machine (not in a container). It manages Docker containers, tracks generation history, and executes git operations.

### 1.2 HTTP API

Implements all endpoints defined in CAMBRIAN-SPEC-004 § Supervisor HTTP API:

| Method | Path       | Purpose |
|--------|------------|---------|
| GET    | /          | HTML dashboard (minimal: generation list, current status) |
| GET    | /stats     | JSON: current generation, status, uptime |
| GET    | /versions  | JSON: array of all generation records |
| POST   | /spawn     | Create container, mount artifact, run Test Rig |
| POST   | /promote   | Merge gen-N branch to main, create annotated tag |
| POST   | /rollback  | Delete gen-N branch, record failure |

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

- Stored in `generations.json` in the project root (host filesystem).
- Append-only: new records are appended, existing records MUST NOT be modified.
- Schema per CAMBRIAN-SPEC-004 § Generation Record.
- The file is a JSON array. On startup, if the file doesn't exist, create it with `[]`.
- Concurrent access is not a concern for M1 (single Prime, sequential generations).

**Docker Container Lifecycle:**

The Supervisor manages containers using `aiodocker`, an async Docker client that integrates natively with `asyncio`. All container operations are non-blocking — no `run_in_executor` wrappers needed.

```python
import aiodocker

async def spawn_generation(generation, artifact_path, supervisor_url):
    client = aiodocker.Docker()
    config = {
        "Image": "cambrian-base",
        "Env": [
            f"ANTHROPIC_API_KEY={os.environ['ANTHROPIC_API_KEY']}",
            f"CAMBRIAN_SUPERVISOR_URL={supervisor_url}",
        ],
        "HostConfig": {
            "Binds": [f"{artifact_path}:/workspace:rw"],
        },
    }
    container = await client.containers.create_or_replace(
        name=f"lab-gen-{generation}", config=config
    )
    await container.start()
    # Stream container logs for debugging (stored with generation record)
    logs = await container.log(stdout=True, stderr=True)
    # Non-blocking wait — event loop remains free for HTTP requests
    await container.wait()
    # Read viability report from mounted volume, update generation record
    # Attach captured logs to generation record for post-mortem debugging
    await container.delete()
    await client.close()
```

**Git Operations:**

The Supervisor operates on a git repository in the project root. Git commands run via `asyncio.create_subprocess_exec` to stay non-blocking. No `gitpython` dependency — git CLI is universally available and the operations are simple.

```python
async def git(*args: str) -> str:
    proc = await asyncio.create_subprocess_exec(
        "git", *args,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, stderr = await proc.communicate()
    if proc.returncode != 0:
        raise GitError(stderr.decode())
    return stdout.decode()

# promote:
#   1. await git("merge", "gen-N", "--no-ff", "-m", "Promote generation N")
#   2. await git("tag", "-a", f"gen-{n}", "-m", f"Generation {n} promoted")
#   3. await git("branch", "-d", f"gen-{n}")
#   4. Update generation record: outcome=promoted

# rollback:
#   1. await git("tag", "-a", f"gen-{n}-failed", "-m", f"Generation {n} failed")
#   2. await git("branch", "-D", f"gen-{n}")
#   3. Update generation record: outcome=failed, artifact_ref=f"gen-{n}-failed"
```

**Status Tracking:**

The Supervisor tracks its own state:

- `status`: one of `idle`, `spawning`, `testing`, `promoting`, `rolling-back`
- `current_generation`: the generation currently being processed (or the last completed)
- `start_time`: timestamp for uptime calculation

### 1.4 Startup Sequence

```
1. Parse environment variables (ANTHROPIC_API_KEY required, fail if missing)
2. Load or initialize generations.json
3. Initialize git repo if needed (git init, create main branch)
4. Start HTTP server on port 8400
5. Log: "Supervisor ready on http://0.0.0.0:8400"
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

Per CAMBRIAN-SPEC-004 § Lifecycle → Verify:

```
read-manifest → build → test → start → health-check → report
```

### 2.3 Implementation Details

**Entry point:** `python /test-rig/test_rig.py`

The Test Rig is baked into the Docker image at `/test-rig/`. It is NOT part of the artifact.

```
Stage 1 — Read Manifest:
  - Read /workspace/manifest.json
  - Validate all MUST fields present and correctly typed
  - Validate cambrian-version == 1
  - Fail: missing file, malformed JSON, missing fields

Stage 2 — Build:
  - Run entry.build as shell command in /workspace
  - Capture stdout/stderr (used for diagnostics on failure, see §2.6)
  - Timeout: 120 seconds
  - Fail: non-zero exit code or timeout

Stage 3 — Test:
  - Run entry.test as shell command in /workspace
  - Capture stdout/stderr to extract test counts AND failure details (see §2.6)
  - Parse pytest output for "N passed" / "N failed" pattern
  - Parse pytest output for individual test failures (see §2.6 for format)
  - Timeout: 300 seconds
  - Fail: non-zero exit code or timeout

Stage 4 — Start:
  - Run entry.start as background process
  - Capture stdout/stderr (used for diagnostics on failure, see §2.6)
  - Wait for TCP port (parsed from entry.health URL) to accept connections
  - Timeout: 30 seconds
  - Fail: process exits, port never opens, or timeout

Stage 5 — Health Check:
  - If manifest contains a "contracts" array, execute each contract (see §2.5)
  - If manifest does NOT contain "contracts", fall back to hard-coded checks:
    - GET entry.health URL → expect HTTP 200
    - GET /stats on same host:port → expect HTTP 200 with valid JSON containing "generation" field
  - Timeout: 10 seconds per request
  - Fail: any contract fails, or fallback check fails, non-200, invalid JSON, timeout
  - After checks: terminate the started process (SIGTERM, then SIGKILL after 5s)

Report:
  - Write /workspace/viability-report.json per CAMBRIAN-SPEC-004 schema
  - If any stage failed, include a diagnostics object (see §2.6)
  - Exit 0 if all stages passed (status=viable)
  - Exit 1 if any stage failed (status=non-viable)
  - Pipeline is fail-fast: if stage N fails, stages N+1..5 are not attempted.
    Their checks show passed=false, duration_ms=0.
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

**Backward compatibility:** Contracts are optional. If `contracts` is absent from the manifest, the Test Rig falls back to the hard-coded health-check behavior defined in CAMBRIAN-SPEC-004 § Prime HTTP API. This means all existing manifests (including the test artifact) continue to work unchanged. Contracts are additive — they extend verification, never replace the fixed pipeline stages (build → test → start → health → report).

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

**Variable substitution:** The string `"$GENERATION"` in any `expect` value is replaced at runtime with the artifact's `generation` number from the manifest. This allows contracts to reference generation-specific values without hard-coding them.

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

#### SPEC-004 Manifest Extension

This section proposes adding `contracts` as an optional field to the Artifact Manifest schema defined in CAMBRIAN-SPEC-004. The field rules:

| Field | Required | Rule |
|-------|----------|------|
| `contracts` | MAY | Array of contract objects. If absent, Test Rig uses hard-coded health checks. If present, Test Rig evaluates each contract during the health-check stage. |

This is a backward-compatible extension — no existing MUST fields change, no existing behavior changes when the field is absent. The extension SHOULD be incorporated into CAMBRIAN-SPEC-005 (the genome spec) so that LLM-generated organisms declare their contracts explicitly.

### 2.6 Structured Diagnostics

When any pipeline stage fails, the Test Rig MUST include a `diagnostics` object in the viability report. Diagnostics capture structured, machine-readable failure context that Prime (or a human) can use to understand *why* a generation failed — not just *that* it failed.

**Why this matters:** Loom achieved a 1.4% viability rate (1 promotion in 72 generations). A major contributor was blind retry — when a generation failed, the next attempt had no structured information about the failure. The LLM re-read the spec and tried again from scratch. Structured diagnostics turn the Test Rig from a Darwinian oracle (binary pass/fail, organism retries blindly) into a Lamarckian teaching signal (structured failure context feeds forward into the next generation's prompt). The organism doesn't mutate randomly — it learns what went wrong.

**Design principle:** The diagnostics section is produced by the environment (Test Rig), stored by the environment (Supervisor, in the generation record), and consumed by the organism (Prime, when constructing the LLM prompt for the next attempt). The organism MUST NOT produce its own diagnostics — only the environment can assess failure. This preserves the "environment judges, not the organism" invariant from CAMBRIAN-SPEC-004.

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

**build** — Build command failure. `summary` is "build command failed with exit code N" or "build command timed out after 120s". `failures` is empty. `stdout_tail` and `stderr_tail` capture the build output (compiler errors, missing dependencies, pip failures).

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

#### SPEC-004 Viability Report Extension

This section proposes adding `diagnostics` as an optional field to the Viability Report schema defined in CAMBRIAN-SPEC-004. The field rules:

| Field | Required | Rule |
|-------|----------|------|
| `diagnostics` | MAY | Object. Present when `status` is `non-viable`. Contains structured failure context for the failed stage. |

This is a backward-compatible extension — no existing MUST fields change. When `status` is `viable`, `diagnostics` is absent and existing report consumers are unaffected.

### 2.7 Informed Retry

When a generation fails and Prime has retries remaining (`CAMBRIAN_MAX_RETRIES`), the next attempt SHOULD be an informed retry — the LLM receives not just the spec and diagnostics, but the actual source code of the failed attempt.

**Why this matters:** Diagnostics (§2.6) tell Prime *what* failed. The failed source code tells Prime *where* the bug is. Together, they transform retry from "read the spec and try again from scratch" into "here's what you wrote, here's what broke, fix it." This is the difference between asking a developer to rewrite a program from a spec vs. asking them to debug code they can see. The latter is dramatically more likely to succeed.

**Philosophical reconciliation:** CAMBRIAN-SPEC-004 says "regenerate the entire codebase from scratch each generation." Informed retry does not violate this. The distinction is:

- **Retries** (within `CAMBRIAN_MAX_RETRIES` for the same spec) are **Lamarckian** — the organism learns from failure. The failed code is a scaffold for the fix, not a codebase to accumulate on. Each retry still produces a complete artifact.
- **Generations** (across spec versions in M2) are **Darwinian** — clean-room regeneration from the mutated spec. No previous code is referenced. No path dependence.

Retries fix bugs in an implementation. Generations explore different implementations. These are different evolutionary mechanisms operating at different timescales.

#### Preserving Failed Artifacts

When the Supervisor rolls back a generation, it MUST preserve the failed code as a git tag before deleting the branch:

```python
# rollback (updated):
#   1. Tag the failed branch: git tag -a gen-N-failed -m "Generation N failed"
#   2. Delete the branch: git branch -D gen-N
#   3. Update generation record: outcome=failed, artifact_ref="gen-N-failed"
```

The tag `gen-N-failed` is a permanent, lightweight reference to the failed code. It does not pollute the branch namespace. Tags are cheap in git — thousands of failed-generation tags have negligible cost.

**Naming convention:** Failed generation tags use the pattern `gen-N-failed`. If the same generation number is retried and fails again (within `CAMBRIAN_MAX_RETRIES`), subsequent tags use `gen-N-failed-2`, `gen-N-failed-3`, etc. The generation record's `artifact_ref` always points to the most recent failed attempt.

#### Generation Record Extension

The generation record gains an `artifact_ref` field:

| Field | Required | Rule |
|-------|----------|------|
| `artifact_ref` | MAY | String. Git ref (tag name) pointing to the artifact's source tree. Present for both promoted generations (tag `gen-N`) and failed generations (tag `gen-N-failed`). Absent for `in-progress` generations. |

Example generation record for a failed attempt:

```json
{
  "generation": 2,
  "parent": 1,
  "outcome": "failed",
  "artifact_ref": "gen-2-failed",
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
   → finds gen-N with outcome=failed, artifact_ref="gen-N-failed", diagnostics={...}

2. Prime reads the failed source code from git:
   → git show gen-N-failed:src/api.py
   → git show gen-N-failed:src/prime.py
   → (reads files listed in the failed manifest's "files" array)

3. Prime constructs the LLM prompt:
   → System: "You are a code generator. Fix the following code based on the test failures."
   → User: [spec] + [failed source code] + [structured diagnostics]

4. LLM produces a corrected complete codebase
   → Prime writes it as a new artifact, NOT a patch on the old one
```

The LLM prompt structure is Prime's concern (defined in CAMBRIAN-SPEC-005), but the data availability is an environment concern specified here. The Supervisor MUST make failed artifacts accessible via git tags so Prime can read them.

**Important:** Informed retry still produces a complete artifact. The LLM outputs all files, not a diff. This preserves the "complete regeneration" property — the artifact is self-contained and not path-dependent. The failed code is a reference for debugging, not a base for patching.

#### Cleanup

Failed-generation tags accumulate over time. For M1, this is not a concern — tag storage is negligible. For M2+, the Supervisor MAY implement a retention policy (e.g., keep only the last N failed tags per generation lineage). This is explicitly deferred — premature cleanup risks losing debugging context.

## 3. Docker Infrastructure

### 3.1 Base Image

A single Dockerfile produces the `cambrian-base` image used by all containers.

```dockerfile
FROM python:3.14t-slim

# Create virtual environment (activated for all subsequent commands)
RUN python -m venv /venv
ENV PATH="/venv/bin:$PATH"

# Test Rig lives in the image, not the artifact
COPY test-rig/ /test-rig/

# Working directory for artifacts
WORKDIR /workspace

# Default entrypoint: run the Test Rig
ENTRYPOINT ["python", "/test-rig/test_rig.py"]
```

**Notes:**

- `python:3.14t-slim` is the free-threaded Python 3.14 image (GIL disabled, PEP 779). Chosen for true multithreading support with ~5-10% single-threaded overhead. The `t` suffix denotes the free-threaded build.
- A virtual environment at `/venv` is created at image build time and activated via `PATH`. All `pip install` commands (including `entry.build`) install into `/venv`, never the system Python.
- The Test Rig is baked in so every container can verify artifacts without additional setup.
- When running Prime (not the Test Rig), the entrypoint is overridden: `docker run --entrypoint python cambrian-base src/prime.py`.

### 3.2 Networking

All containers use Docker's default bridge network:

- **Outbound HTTPS** (port 443): required for LLM API calls. Docker bridge allows this by default.
- **Host access**: containers reach the Supervisor via `host.docker.internal:8400` (Docker Desktop on macOS) or `172.17.0.1:8400` (Linux default gateway). The Supervisor URL is passed as environment variable `CAMBRIAN_SUPERVISOR_URL`.
- **DNS**: Docker bridge provides DNS resolution for external hosts.

No custom Docker networks are needed for M1.

### 3.3 Credential Injection

Per CAMBRIAN-SPEC-004 § Container Requirements → Credential Injection:

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
  Dockerfile         — Base image (Python 3.14t-slim + Test Rig)
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

### 4.2 Contents

```
test-artifact/
  manifest.json       — Valid manifest pointing to the test server
  src/server.py       — Trivial HTTP server: /health → 200, /stats → JSON
  tests/test_server.py — pytest tests for both endpoints
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
  "created_at": "2026-03-21T00:00:00Z",
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

### 4.3 Validation

After Phase 0 is built, run this sequence:

```
1. Build Docker image:        docker/build.sh
2. Start Supervisor:          python supervisor/supervisor.py
3. Spawn test artifact:       curl -X POST http://localhost:8400/spawn \
                                -H "Content-Type: application/json" \
                                -d '{"spec-hash":"sha256:000...","generation":0,"artifact-path":"test-artifact"}'
4. Wait for container to exit
5. Check viability report:    cat test-artifact/viability-report.json
   → Expect: status=viable, all checks passed
6. Promote:                   curl -X POST http://localhost:8400/promote \
                                -H "Content-Type: application/json" \
                                -d '{"generation":0}'
7. Verify git:                git tag → gen-0 exists
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
7. Create the test artifact (`test-artifact/`)
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

**Done when:** Gen-1 Prime passes the Test Rig and is promoted. `git tag gen-1` exists. Generation record shows `outcome: promoted`.

### Stage 2: Verify Reproduction

**Who:** Gen-1 Prime (autonomous, observed by human)

**Steps:**
1. Start Gen-1 Prime in a container with the full genome spec
2. Gen-1 reads the spec, calls an LLM, generates Gen-2
3. Gen-2 is spawned and tested by the Test Rig
4. If viable: promote Gen-2
5. Start Gen-2 Prime with the Minimal Spec
6. Gen-2 generates Gen-3 (echo server)
7. Gen-3 is spawned and tested
8. If viable: M1 is complete

**Done when:** The M1 acceptance criteria from CAMBRIAN-SPEC-004 § Validation → Reproductive check are met.

## 6. Project Directory Structure

After bootstrap is complete:

```
cambrian/
  supervisor/
    supervisor.py         — Supervisor HTTP server
    test_supervisor.py    — Supervisor unit tests
  test-rig/
    test_rig.py           — Test Rig verification pipeline
    test_test_rig.py      — Test Rig unit tests
  docker/
    Dockerfile            — Base image
    build.sh              — Image build script
  test-artifact/
    manifest.json         — Hand-crafted test manifest
    src/server.py         — Trivial test server
    tests/test_server.py  — Test server tests
    requirements.txt      — Empty
  spec/
    CAMBRIAN-SPEC-004.md  — System spec
    CAMBRIAN-SPEC-005.md  — Genome spec (Prime definition)
    BOOTSTRAP-SPEC-001.md — This document
    SPEC-STYLE-GUIDE.md   — Spec writing guide
    diagrams/             — Architecture and sequence diagrams
  lab-journal/
    journal-*.md          — Discussion and decision logs
  generations.json        — Generation history (created at runtime)
  pyproject.toml          — Project metadata, dependencies, tool configs (ruff, pyright)
  uv.lock                 — Locked dependency versions (committed)
  .venv/                  — Host-side virtual environment (not committed)
  .env                    — ANTHROPIC_API_KEY (not committed)
  pyrightconfig.json      — Pyright strict mode configuration
  README.md
  CLAUDE.md
```

## 7. Configuration

All configuration is via environment variables on the host.

| Variable | Required | Default | Purpose |
|----------|----------|---------|---------|
| `ANTHROPIC_API_KEY` | MUST | — | LLM API key, forwarded to containers |
| `CAMBRIAN_SUPERVISOR_PORT` | MAY | `8400` | Supervisor listen port |
| `CAMBRIAN_SUPERVISOR_URL` | MAY | `http://host.docker.internal:8400` | Supervisor URL passed to containers |
| `CAMBRIAN_DOCKER_IMAGE` | MAY | `cambrian-base` | Docker image name |
| `CAMBRIAN_WORKSPACE_ROOT` | MAY | `.` | Root directory for artifacts and git |

## 8. Failure Modes

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
| Git branch conflict | gen-N branch already exists | `/spawn` returns error. Caller must rollback the existing gen-N first. |
| Contract check failure | HTTP contract returns unexpected status/body | Test Rig records `failure_stage: health`. All contracts are still evaluated; per-contract results included in viability report. |
| Malformed contracts | `contracts` array contains invalid contract objects | Test Rig records `failure_stage: manifest`. Contract schema is validated during manifest parsing. |
| Pytest output unparseable | Test output doesn't match expected patterns | `diagnostics.failures` is empty array. `stdout_tail` and `stderr_tail` still capture raw output. No information lost, just unstructured. |
| Failed tag already exists | `gen-N-failed` tag exists from a previous retry | Supervisor appends retry suffix: `gen-N-failed-2`, `gen-N-failed-3`, etc. |
| Failed artifact unreadable | Prime cannot `git show` files from failed tag | Non-fatal. Prime falls back to blind retry (spec + diagnostics only, no source code). |
| Pathological output volume | Failed command produces megabytes of output | `stdout_tail` and `stderr_tail` capped at last 100 lines. Individual `error` strings capped at 500 characters. Viability report size stays bounded. |

## 9. Validation

### Mechanical checks (Phase 0 acceptance)

- Supervisor starts on port 8400 and responds to `GET /stats`
- `POST /spawn` with test artifact creates a Docker container
- Container runs the Test Rig to completion
- Test Rig writes valid `viability-report.json`
- `POST /promote` merges branch and creates git tag
- `POST /rollback` deletes branch and records failure
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
- Generation records include `artifact_ref` pointing to the failed tag
- Prime can read failed source code via `git show <artifact_ref>:<file>`
- Retry with suffix tags works (`gen-N-failed-2`, `gen-N-failed-3`)

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
- `ruff check` and `ruff format --check` pass with zero errors
- `pyright` in strict mode passes with zero errors
- All async tests run via `pytest-asyncio` without event loop warnings
- `uv.lock` is committed and `uv sync` reproduces the environment

## 10. References

- [CAMBRIAN-SPEC-004](CAMBRIAN-SPEC-004.md) — System spec defining all contracts and schemas
- [SPEC-STYLE-GUIDE](SPEC-STYLE-GUIDE.md) — Spec writing conventions
- [Loom final retrospective](https://github.com/lispmeister/loom/blob/master/architecture-reviews/review-2026-03-20-001.md) — Lessons from the predecessor project

---

```yaml
spec-version: "002"
spec-type: "bootstrap"
parent-spec: "CAMBRIAN-SPEC-004"
ancestor: "BOOTSTRAP-SPEC-001"
language: "python 3.14t"
```

---

*This spec is scaffolding. Once the bootstrap is complete and Prime is self-hosting, this document becomes historical. The living contract is the genome spec (CAMBRIAN-SPEC-005).*
