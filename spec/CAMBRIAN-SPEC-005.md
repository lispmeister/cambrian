---
date: 2026-03-23
author: Markus Fix <lispmeister@gmail.com>
title: "Cambrian Genome: What Prime Is"
version: 0.7.0
tags: [cambrian, prime, genome, LLM, self-reproduction, M1]
---

# CAMBRIAN-SPEC-005 — The Genome

## What This Document Is

This is the genome. An LLM reads this document and produces a complete, working Prime — a code generator that can read *this same document* and produce another Prime. The output is not a diff, not a patch, not a fragment. It is a complete codebase: source files, tests, manifest, and this spec file — everything needed to run.

Prime is a general-purpose code generator. Give it any spec and it produces a working codebase. Self-reproduction is what happens when Prime is given *its own* spec as input.

## Invariants

These rules are absolute. They define what it means to be Prime.

- **Prime MUST NOT modify the spec.** The spec is the genome — it defines what Prime is. If the spec changes, it changes through an external process (human editing or future M2+ mutation), never through Prime's own initiative.
- **Prime MUST NOT self-assess viability.** Viability is determined by the Test Rig (environment). Prime requests verification and accepts the verdict. It never declares itself viable without external confirmation.
- **Prime MUST NOT perform git operations.** Git is Supervisor-managed infrastructure. Prime writes files to its workspace. The Supervisor handles branches, commits, tags, and merges.

## Glossary

- **Spec** — This document. The genome. Defines what Prime is. Input to LLM code generation.
- **Prime** — The organism. An async HTTP server that reads the spec, calls an LLM, produces code, and requests verification. Contains its own source code and spec.
- **Artifact** — A directory containing a complete generated codebase: source files, test suite, spec copy, and `manifest.json`. Produced by Prime. Immutable once written.
- **Manifest** — `manifest.json` at the artifact root. The fixed-point contract between organism and environment. Describes how to build, test, and start Prime.
- **Generation** — One attempt to produce a viable artifact. Each gets a monotonically increasing number, a git branch (`gen-N`), and an audit record.
- **Viability Report** — Structured JSON written by the Test Rig at `/workspace/viability-report.json`. Binary outcome: `viable` or `non-viable`. Read by Prime via the generation record.

## What Prime Does

Prime is an async Python HTTP server that runs inside a Docker container. It does four things in a loop:

1. **Read** — Load the spec from its local filesystem. Load generation history from the Supervisor.
2. **Generate** — Send the spec (plus history and, on retry, failure context) to an LLM. Parse the response into files.
3. **Verify** — Write the files to a workspace, build a manifest, commit to a git branch, ask the Supervisor to spawn a Test Rig container.
4. **Decide** — Read the viability report. Tell the Supervisor to promote (viable) or rollback (non-viable). On rollback with retries remaining, go to step 2 with failure context.

While idle (not generating), Prime serves an HTTP API so the Test Rig can verify it is alive.

## Contracts

### Prime HTTP API

Prime MUST serve these endpoints on port 8401:

| Method | Path | Response |
|--------|------|----------|
| GET | `/health` | `200 OK` — body: `{"ok": true}` |
| GET | `/stats` | `200 OK` — body: `{"generation": N, "status": "idle", "uptime": S}` |

- `/health` is a liveness check. No preconditions. Always returns 200.
- `/stats` — `generation` MUST match the artifact's generation number. `status` is one of `idle`, `generating`, `verifying`. `uptime` is integer seconds since start.

### Supervisor HTTP API (Prime calls these)

The Supervisor runs on the host at `CAMBRIAN_SUPERVISOR_URL` (default `http://localhost:8400`).

| Method | Path | Request Body | Success Response |
|--------|------|-------------|-----------------|
| GET | `/versions` | — | `[GenerationRecord, ...]` |
| GET | `/stats` | — | `{"generation": N, "status": "...", "uptime": N}` |
| POST | `/spawn` | `{"spec-hash": "...", "generation": N, "artifact-path": "/path"}` | `{"ok": true, "container-id": "...", "generation": N}` |
| POST | `/promote` | `{"generation": N}` | `{"ok": true, "generation": N}` |
| POST | `/rollback` | `{"generation": N}` | `{"ok": true, "generation": N}` |

All POST endpoints return `{"ok": false, "error": "..."}` on failure.

`POST /spawn` is asynchronous — it starts the Test Rig and returns immediately. Prime polls `GET /versions` until the generation record's outcome is no longer `in_progress` (see [Generation Loop](#the-generation-loop) step 8).

### Artifact Manifest

Every artifact Prime produces MUST include `manifest.json` at its root:

```json
{
  "cambrian-version": 1,
  "generation": 1,
  "parent-generation": 0,
  "spec-hash": "sha256:...",
  "artifact-hash": "sha256:...",
  "producer-model": "claude-opus-4-6",
  "token-usage": {"input": 45000, "output": 12000},
  "files": ["src/prime.py", "tests/test_prime.py", "manifest.json", "spec/CAMBRIAN-SPEC-005.md"],
  "created_at": "2026-03-23T12:00:00Z",
  "entry": {
    "build": "pip install -r requirements.txt",
    "test": "python -m pytest tests/ -v",
    "start": "python src/prime.py",
    "health": "http://localhost:8401/health"
  },
  "contracts": [
    {"name": "health-liveness", "type": "http", "method": "GET", "path": "/health",
     "expect": {"status": 200, "body": {"ok": true}}},
    {"name": "stats-generation", "type": "http", "method": "GET", "path": "/stats",
     "expect": {"status": 200, "body_contains": {"generation": "$GENERATION"}}},
    {"name": "stats-schema", "type": "http", "method": "GET", "path": "/stats",
     "expect": {"status": 200, "body_has_keys": ["generation", "status", "uptime"]}}
  ]
}
```

**MUST fields:** `cambrian-version` (integer, currently 1), `generation` (integer >= 1), `parent-generation` (integer >= 0, 0 for bootstrap), `spec-hash` (SHA-256 hex of the spec file), `artifact-hash` (SHA-256 hex of all artifact files except `manifest.json`, computed as: sort all artifact file paths relative to the artifact root lexicographically, then feed `path_bytes + b'\0' + file_bytes` for each file in order into a single SHA-256 hasher, prefix the hex digest with `sha256:`), `producer-model` (LLM model string), `token-usage` (object with `input` and `output` integers), `files` (array of all file paths, MUST include `manifest.json` and the spec file), `created_at` (ISO-8601), `entry.build`, `entry.test`, `entry.start`, `entry.health`.

**SHOULD fields:** `contracts` (array of verification contract objects). When present, contracts are the sole source of health-check verification — the Test Rig does not supplement with hardcoded checks.

### Viability Report (Prime reads this)

Written by the Test Rig at `/workspace/viability-report.json`. The Supervisor returns it in the generation record.

```json
{
  "generation": 1,
  "status": "viable",
  "failure_stage": "none",
  "checks": {
    "manifest": {"passed": true},
    "build": {"passed": true, "duration_ms": 3200},
    "test": {"passed": true, "tests_run": 15, "tests_passed": 15, "duration_ms": 8400},
    "start": {"passed": true, "duration_ms": 1200},
    "health": {"passed": true, "duration_ms": 50}
  },
  "completed_at": "2026-03-23T12:05:00Z"
}
```

When `status` is `non-viable`, the report includes a `diagnostics` object:

```json
{
  "diagnostics": {
    "stage": "test",
    "summary": "7 of 15 tests failed",
    "exit_code": 1,
    "failures": [
      {"test": "tests/test_api.py::test_spawn", "error": "AssertionError: expected 200, got 404", "file": "tests/test_api.py", "line": 42}
    ],
    "stdout_tail": "...last 100 lines...",
    "stderr_tail": "...last 100 lines..."
  }
}
```

Prime reads `status`. If `viable`, promote. If `non-viable`, rollback and optionally retry with `diagnostics` as context.

**Viability report field rules:**

| Field | Required | Rule |
|-------|----------|------|
| `generation` | MUST | Integer matching the artifact's generation. |
| `status` | MUST | One of: `viable`, `non-viable`. |
| `failure_stage` | MUST | One of: `none`, `manifest`, `build`, `test`, `start`, `health`. First stage that failed, or `none` when viable. |
| `checks.*` | MUST | Each check MUST include `passed` (boolean). `duration_ms` SHOULD be included. `test` check MUST include `tests_run` and `tests_passed`. `health` check MAY include a `contracts` sub-object with per-contract results. |
| `completed_at` | MUST | ISO-8601 timestamp. |
| `diagnostics` | MUST when `non-viable` | Present when `status` is `non-viable`, absent otherwise. Object with `stage`, `summary`, `exit_code`, `failures[]`, `stdout_tail`, `stderr_tail`. |

Pipeline is fail-fast: if `build` fails, `test`/`start`/`health` are not attempted and their `passed` fields are `false`.

## The Generation Loop

```
start → [read spec + history] → [call LLM] → [parse + write files] → [build manifest]
       → [POST /spawn] → [poll until outcome != in_progress]
       → [read viability report] → promote or rollback
       → if rollback and retries left: [read failed code + diagnostics] → [call LLM again]
       → if rollback and no retries: stop
```

### Step by step

1. **Determine generation number.** `GET /versions` → find the highest generation number → next is N+1. If no history, N=1. This is also the value Prime returns from `GET /stats` as `generation`.

2. **Read the spec.** Load from `CAMBRIAN_SPEC_PATH` (default `./spec/CAMBRIAN-SPEC-005.md`). Compute its SHA-256 hash.

3. **Build the LLM prompt.** See [LLM Integration](#llm-integration) below.

4. **Call the LLM.** Use the Anthropic API (`ANTHROPIC_API_KEY` from environment). Model: `CAMBRIAN_MODEL` (default: `claude-opus-4-6`). The response contains file contents in tagged blocks.

5. **Parse the response.** Extract files from `<file path="...">content</file>` blocks. Each block is one file. The `path` attribute is relative to the artifact root.

6. **Write files to workspace.** Create a directory for the artifact. Write all parsed files. Copy the spec file into the artifact at its expected path. Write `manifest.json` with computed hashes and metadata.

7. **Request verification.** `POST /spawn` with the artifact path, spec-hash, and generation number. The Supervisor handles all git operations (branch creation, commit). Prime MUST NOT touch git.

8. **Poll.** `GET /versions` until the generation record's outcome is no longer `in_progress`. The Supervisor sets the outcome to `tested` once the Test Rig container exits. Poll interval: 2 seconds.

9. **Decide.** Read the viability report from the generation record.
    - If viability status is `viable`: `POST /promote {"generation": N}`. The Supervisor performs the git merge and sets outcome to `promoted`. Done.
    - If viability status is `non-viable` and retries remain: `POST /rollback {"generation": N}`. The Supervisor tags the failed branch and sets outcome to `failed`. Go to step 3 with failure context (see [Informed Retry](#informed-retry)).
    - If viability status is `non-viable` and no retries: `POST /rollback {"generation": N}`. Stop.

### Retry semantics

Each retry is a NEW generation with a new number. Retries MUST NOT reuse previous LLM output — each call produces a complete codebase. The retry counter tracks how many consecutive failures have occurred for the same "intent" (same spec-hash). A successful promotion resets the counter.

Maximum retries: `CAMBRIAN_MAX_RETRIES` (default 3).
Maximum generations: `CAMBRIAN_MAX_GENS` (default 5).

## Failure Handling

| Failure | Trigger | Response |
|---------|---------|----------|
| Unparseable LLM output | LLM returns malformed or incomplete `<file>` blocks | Record failed attempt. Retry with fresh LLM call (up to `CAMBRIAN_MAX_RETRIES`). |
| Build failure | `entry.build` exits non-zero | Non-viable verdict from Test Rig. Rollback, then retry as informed retry. |
| Test failure | `entry.test` exits non-zero | Non-viable verdict. Rollback, then retry. |
| Start timeout | Prime doesn't bind HTTP port within 10s | Non-viable verdict. Rollback, then retry. |
| Health check failure | `GET /health` returns non-200 | Non-viable verdict. Rollback, then retry. |
| Supervisor unreachable | Network error on any Supervisor call | Exponential backoff: 1s, 2s, 4s, 8s, 16s, then 60s ceiling. Do not proceed without verification — never self-promote. |
| LLM rate-limited | HTTP 429 from LLM API | Respect `retry-after` header. Pause and retry. Do not count as a generation failure. |
| All retries exhausted | `CAMBRIAN_MAX_RETRIES` reached for one generation | Record generation as failed. Stop. Do not modify the spec. |
| Container crash | Test Rig container exits without writing viability report | Supervisor records `outcome: failed`. Treat as non-viable. Retry. |

## LLM Integration

### Output format

Prime instructs the LLM to emit files as tagged blocks:

```
<file path="src/prime.py">
#!/usr/bin/env python3
"""Prime — the organism."""
...
</file>

<file path="tests/test_prime.py">
...
</file>
```

Prime parses these blocks with a simple regex: `<file path="([^"]+)">(.*?)</file>` (dotall mode). Anything outside `<file>` blocks is ignored (the LLM may emit commentary).

### Fresh generation prompt

**System message:**

```
You are a code generator. You produce complete, working Python codebases from specifications.

Rules:
- Output ONLY <file path="...">content</file> blocks. One block per file.
- Every file needed to build, test, and run the project must be in a <file> block.
- Include a requirements.txt with all dependencies.
- Include a test suite that exercises all functionality.
- The code must work in Python 3.14 inside a Docker container with a venv at /venv.
- Do NOT include manifest.json — it is generated separately.
- Do NOT include the spec file — it is copied separately.
```

**User message:**

```
# Specification

{spec_content}

# Generation History

{generation_records_json}

# Task

Produce a complete working codebase that implements the specification above.
Generation number: {N}
Parent generation: {parent}
```

### Informed retry prompt

When retrying after a failure, Prime adds failure context to the user message:

```
# Specification

{spec_content}

# Generation History

{generation_records_json}

# Previous Attempt Failed

Generation {N-1} failed at stage: {diagnostics.stage}
Summary: {diagnostics.summary}

## Failed Source Code

{for each file in failed artifact:}
### {file_path}
```{language}
{file_content}
```
{end for}

## Diagnostics

{diagnostics_json}

# Task

The previous attempt failed. Study the failed code and diagnostics above.
Produce a complete, corrected codebase that fixes the identified issues.
Generation number: {N}
Parent generation: {parent}
```

Prime reads the failed source code from the local filesystem — the files are still on disk from the previous write. Prime MUST NOT use git to read files (see Invariants).

## Implementation Requirements

### Language and runtime

- Python 3.14 (free-threaded build deferred to M2)
- All I/O-bound code MUST use `asyncio`
- HTTP server: `aiohttp`
- HTTP client (for Supervisor API and LLM calls): `aiohttp.ClientSession`
- Logging: `structlog` — every log line includes `timestamp`, `level`, `event`, `component` ("prime"), and `generation` where applicable
- Type annotations: full coverage, Pyright strict compatible
- Validation: Pydantic v2 for all I/O boundary data (manifest, viability report, API responses)

### Startup sequence

```
1. Validate ANTHROPIC_API_KEY is set (fatal error if missing)
2. Read CAMBRIAN_SPEC_PATH, CAMBRIAN_SUPERVISOR_URL, CAMBRIAN_MODEL, etc.
3. Start HTTP server on port 8401 (serves /health and /stats immediately)
4. Begin generation loop as a background task
```

Prime MUST be ready to serve `/health` before starting generation. The Test Rig checks health during the start stage — if Prime blocks on generation before binding the port, it will timeout.

### File layout

```
src/
  prime.py          — entry point, HTTP server, main loop
  generate.py       — LLM integration: prompt building, API calls, response parsing
  supervisor.py     — Supervisor API client
  manifest.py       — manifest building, hash computation
  models.py         — Pydantic models (manifest, viability report, generation record)
tests/
  test_prime.py     — HTTP API tests (/health, /stats)
  test_generate.py  — LLM prompt construction, response parsing
  test_manifest.py  — manifest building, hash computation
  test_supervisor.py — Supervisor client (mock HTTP)
requirements.txt    — dependencies
```

This layout is a SHOULD, not a MUST. The LLM may organize files differently as long as all functionality is present and tests pass.

### Tests

The test suite MUST cover:
- `/health` returns 200 with `{"ok": true}`
- `/stats` returns 200 with valid JSON containing `generation`, `status`, `uptime`
- Manifest building produces valid JSON with all MUST fields
- `spec-hash` computation matches SHA-256 of the spec file
- `artifact-hash` computation excludes `manifest.json`
- LLM response parsing extracts files from `<file>` blocks correctly
- LLM response parsing handles malformed responses gracefully
- Generation number is computed correctly from history
- Retry counter increments and respects `CAMBRIAN_MAX_RETRIES`

Tests MUST be runnable with `python -m pytest tests/ -v`.

## Configuration

| Variable | Required | Default | Purpose |
|----------|----------|---------|---------|
| `ANTHROPIC_API_KEY` | MUST | — | LLM API authentication |
| `CAMBRIAN_MODEL` | MAY | `claude-opus-4-6` | LLM model for generation |
| `CAMBRIAN_MAX_GENS` | MAY | `5` | Max generation attempts before stopping |
| `CAMBRIAN_MAX_RETRIES` | MAY | `3` | Max consecutive failures before stopping |
| `CAMBRIAN_SUPERVISOR_URL` | MAY | `http://localhost:8400` | Supervisor endpoint |
| `CAMBRIAN_SPEC_PATH` | MAY | `./spec/CAMBRIAN-SPEC-005.md` | Path to spec file |
| `CAMBRIAN_TOKEN_BUDGET` | MAY | `0` | Max cumulative tokens (0 = unlimited) |

## Acceptance Criteria

### Mechanical (test suite verifies)

- `GET /health` → 200
- `GET /stats` → valid JSON with `generation`, `status`, `uptime`
- `manifest.json` has all MUST fields, correct types
- `spec-hash` matches SHA-256 of spec file
- `artifact-hash` matches SHA-256 of all files except manifest
- LLM response parsing extracts `<file>` blocks correctly
- Generation loop calls Supervisor API in correct sequence
- Retry logic stops at `CAMBRIAN_MAX_RETRIES`

### Behavioral (code review verifies)

- Prime starts serving `/health` before beginning generation
- LLM prompt includes full spec content and generation history
- Informed retry prompt includes failed source code and diagnostics
- Credentials are never written to artifacts, manifests, or git
- All log lines use structlog with component="prime"
- Errors from Supervisor API calls are logged and handled (backoff + retry)

### Reproductive (M1 acceptance)

```
Bootstrap → Gen-1 Prime (from this spec)
Gen-1     → Gen-2 Prime (from this spec)     ← self-reproduction
Gen-2     → Gen-3 echo server (Minimal Spec) ← operationality proof
```

1. Gen-1 passes the Test Rig and is promoted.
2. Gen-1 reads this spec, calls an LLM, produces Gen-2.
3. Gen-2 passes the Test Rig and is promoted. Gen-2 is a full Prime.
4. Gen-2 is given the Minimal Spec. It produces Gen-3 (an echo server).
5. Gen-3 passes the Test Rig.

The chain terminates at Gen-3 because the Minimal Spec does not produce a Prime.

---

```yaml
spec-version: "005"
version: "0.7.0"
organism: "cambrian"
lineage: "genesis"
language: "python 3.14 (M1)"
```

---

*This is a fixed point. An LLM reads this document and produces an organism
that reads this document and produces an organism. The code is disposable.
The spec is the genome. You are the phenotype.*
