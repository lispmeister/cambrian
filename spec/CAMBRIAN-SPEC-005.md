---
date: 2026-03-23
author: Markus Fix <lispmeister@gmail.com>
title: "Cambrian Genome: What Prime Is"
version: 0.10.4
tags: [cambrian, prime, genome, LLM, self-reproduction, M1, M2]
---

# CAMBRIAN-SPEC-005 — The Genome

## What This Document Is

This is the genome. An LLM reads this document and produces a complete, working Prime — a code generator that can read *this same document* and produce another Prime. The output is not a diff, not a patch, not a fragment. It is a complete codebase: source files, tests, manifest, and this spec file — everything needed to run.

Prime is a general-purpose code generator. Give it any spec and it produces a working codebase. Self-reproduction is what happens when Prime is given *its own* spec as input.

**Spec version inheritance:** Each promoted generation carries a frozen copy of the spec at its creation time (at `CAMBRIAN_SPEC_PATH` in the artifact). Updating this document does not retroactively change promoted generations — they each carry the genome that produced them. To propagate spec changes, a new generation must be produced from the updated spec and promoted. This is analogous to biological inheritance: the genome is copied at reproduction, not shared by reference.

<!-- BEGIN FROZEN: identity-anchor -->
## Invariants

These rules are absolute. They define what it means to be Prime. In M2, the Spec Mutator MUST NOT modify text within FROZEN blocks. The markers are HTML comments — they do not affect rendering and are invisible to M1.

- **Prime MUST NOT modify the spec.** The spec is the genome — it defines what Prime is. If the spec changes, it changes through an external process (human editing or future M2+ mutation), never through Prime's own initiative.
- **Prime MUST NOT self-assess viability.** Viability is determined by the Test Rig (environment). Prime requests verification and accepts the verdict. It never declares itself viable without external confirmation.
- **Prime MUST NOT perform git operations.** Git is Supervisor-managed infrastructure. Prime writes files to its workspace. The Supervisor handles branches, commits, tags, and merges.
- **Prime MUST NOT generate the manifest using the LLM.** The manifest is computed by Prime's own code from measured data (hashes, token usage, file list). It is a verified contract, not generated content. LLM output affects source files only.
- **Prime MUST copy the spec file to the artifact workspace unchanged.** The spec is the genome. Faithful copying is inheritance. Modification or omission corrupts the lineage.
<!-- END FROZEN: identity-anchor -->

## Glossary

- **Spec** — This document. The genome. Defines what Prime is. Input to LLM code generation.
- **Prime** — The organism. An async HTTP server that reads the spec, calls an LLM, produces code, and requests verification. Contains its own source code and spec.
- **Artifact** — A directory containing a complete generated codebase: source files, test suite, spec copy, and `manifest.json`. Produced by Prime. Immutable once written.
- **Manifest** — `manifest.json` at the artifact root. The fixed-point contract between organism and environment. Describes how to build, test, and start Prime.
- **Generation** — One attempt to produce a viable artifact. Each gets a monotonically increasing number, a git branch (`gen-N`), and an audit record.
- **Viability Report** — Structured JSON written by the Test Rig at `/workspace/viability-report.json`. Binary outcome: `viable` or `non-viable`. Read by Prime via the generation record.
- **Hash** — All hash values in Cambrian use the format `sha256:<64 hex characters>`. The `sha256:` prefix is literal text, not a URL scheme. Example: `sha256:a3b4c5...ef` (exactly 64 hex chars after the colon). An LLM MUST include this prefix when generating hash fields.

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
- `/stats` — `generation` is Prime's own generation number — the value of the `CAMBRIAN_GENERATION` environment variable set by the Supervisor at spawn time (same as the `generation` field in Prime's own manifest). This is a fixed identity; it does not change as the loop produces offspring. `status` is one of `idle`, `generating`, `verifying`. `uptime` is integer seconds since start. If `CAMBRIAN_GENERATION` is not set (e.g., local dev), `generation` is 0.

### Supervisor HTTP API (Prime calls these)

The Supervisor runs on the host at `CAMBRIAN_SUPERVISOR_URL` (default `http://host.docker.internal:8400`). Note: Prime runs inside a Docker container, so `localhost` refers to the container itself — `host.docker.internal` is required to reach the Supervisor on the host.

| Method | Path | Request Body | Success Response |
|--------|------|-------------|-----------------|
| GET | `/versions` | — | `[GenerationRecord, ...]` |
| GET | `/stats` | — | `{"generation": N, "status": "...", "uptime": N}` |
| POST | `/spawn` | `{"spec-hash": "...", "generation": N, "artifact-path": "gen-N"}` | `{"ok": true, "container-id": "...", "generation": N}` |
| POST | `/promote` | `{"generation": N}` | `{"ok": true, "generation": N}` |
| POST | `/rollback` | `{"generation": N}` | `{"ok": true, "generation": N}` |

All POST endpoints return `{"ok": false, "error": "..."}` on failure.

**GenerationRecord schema** (returned by `GET /versions` as an array):

| Field | Required | Rule |
|-------|----------|------|
| `generation` | MUST | Integer >= 1. The generation number. |
| `parent` | MUST | Integer >= 0. Parent generation (0 for bootstrap). |
| `spec-hash` | MUST | SHA-256 hex of the spec file (with `sha256:` prefix). |
| `artifact-hash` | MUST | SHA-256 hex of the artifact files (with `sha256:` prefix). |
| `outcome` | MUST | One of: `in_progress`, `tested`, `promoted`, `failed`, `timeout`. `in_progress` while the Test Rig runs. `tested` once the Test Rig exits (Prime then calls /promote or /rollback). Terminal states: `promoted`, `failed`, `timeout`. |
| `viability` | MAY | The full viability report. Present once outcome is `tested` or terminal. Absent while `in_progress`. |
| `artifact_ref` | MAY | Git ref (tag) pointing to the artifact: `gen-N` for promoted, `gen-N-failed` for failed. Absent while `in_progress`. |
| `created` | MUST | ISO-8601 timestamp (when spawn was received). |
| `completed` | MAY | ISO-8601 timestamp. Absent while `in_progress`. |
| `container-id` | MUST | Name of the Test Rig Docker container. |

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

**MUST fields:** `cambrian-version` (integer, currently 1), `generation` (integer >= 1), `parent-generation` (integer >= 0, 0 for bootstrap), `spec-hash` (SHA-256 hex of the spec file with `sha256:` prefix), `artifact-hash` (SHA-256 hex of all artifact files except `manifest.json` — see algorithm below), `producer-model` (LLM model string), `token-usage` (object with `input` and `output` integers), `files` (array of all file paths, MUST include `manifest.json` and the spec file), `created_at` (ISO-8601), `entry.build`, `entry.test`, `entry.start` (SHOULD use `python path/to/script.py` syntax, NOT `python -m module.path` — the `-m` form requires `__init__.py` files and breaks in containers without proper package structure), `entry.health`.

**`artifact-hash` algorithm** — this MUST be implemented exactly or hash verification will fail:

```python
import hashlib
from pathlib import Path

def compute_artifact_hash(artifact_root: Path, files: list[str]) -> str:
    hasher = hashlib.sha256()
    for rel_path in sorted(files):              # lexicographic sort is required
        if rel_path == "manifest.json":
            continue                            # manifest.json is excluded
        hasher.update(rel_path.encode())
        hasher.update(b"\0")                    # null separator between path and content
        hasher.update((artifact_root / rel_path).read_bytes())
    return f"sha256:{hasher.hexdigest()}"
```

The null byte separator (`b"\0"`) between `path_bytes` and `file_bytes` is critical — omitting it allows hash collisions between files with complementary path/content boundaries. An LLM generating this code MUST include `hasher.update(b"\0")`.

**SHOULD fields:** `contracts` (array of verification contract objects). When present, contracts are the sole source of health-check verification — the Test Rig does not supplement with hardcoded checks.

`spec-lineage` (array of `sha256:...` hash strings): ordered list from oldest ancestor spec to immediate parent spec. Empty array `[]` for artifacts produced from the original human-written spec. Present when the spec was produced by M2+ mutation. M1 ignores this field; M2 uses it to reconstruct evolutionary history independent of git.

**Version compatibility:** `cambrian-version: 1` is the M1 contract defined in this document. Future versions (2+) MAY add fields (e.g., `mutation-type`, `parent-spec-hash`, `campaign-id`) that version-1 consumers ignore. The Test Rig MUST reject manifests with `cambrian-version` greater than its supported maximum and MUST accept manifests with `cambrian-version` at or below its supported maximum.

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
[1: determine gen#] → [2: read spec] → [3: build prompt] → [4: call LLM]
→ [5: parse response] → [6: write files + manifest] → [7: POST /spawn]
→ [8: poll until outcome != in_progress]
→ [9: decide: promote or rollback]
→ if rollback + retries: [read failed code + diagnostics] → back to step 3
→ if rollback + no retries: stop
```

### Step by step

1. **Determine generation number.** `GET /versions` → find the highest generation number across all records → offspring is N+1. If no history, N=0 so offspring is 1. This is the generation number written into the offspring's manifest and used for the `/spawn` call — it is NOT the value Prime reports from `/stats` (that is always Prime's own generation from `CAMBRIAN_GENERATION`).

2. **Read the spec.** Load from `CAMBRIAN_SPEC_PATH` (default `./spec/CAMBRIAN-SPEC-005.md`). Compute its SHA-256 hash.

3. **Build the LLM prompt.** See [LLM Integration](#llm-integration) below.

4. **Call the LLM.** Use the Anthropic API (`ANTHROPIC_API_KEY` from environment). Model: `CAMBRIAN_MODEL` (default: `claude-opus-4-6`). The response contains file contents in tagged blocks.

5. **Parse the response.** Extract files from `<file path="...">content</file>` blocks. Each block is one file. The `path` attribute is relative to the artifact root.

6. **Write files to workspace.** Create a subdirectory `./gen-{N}/` inside `/workspace` for the artifact. Write all parsed files into it. Copy the spec file into the artifact at its expected path. Write `manifest.json` with computed hashes and metadata. If the spec file contains a JSON array under a fenced code block marked with `contracts` (e.g., ` ```contracts `) include that array verbatim as the `contracts` field in `manifest.json`. This is how spec-defined contracts propagate to the manifest without passing through the LLM.

7. **Request verification.** `POST /spawn` with `"artifact-path": "gen-{N}"` (relative to `CAMBRIAN_ARTIFACTS_ROOT`), `spec-hash`, and `generation`. The Supervisor resolves the relative path to an absolute host path for the Docker bind mount. The Supervisor handles all git operations (branch creation, commit). Prime MUST NOT touch git. Prime MUST NOT send an absolute path — it runs inside Docker and cannot know the host-side path.

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
- Python 3.14 STRICT: string literals MUST NOT contain unescaped newlines. Use triple
  quotes (""" or ''') for multi-line strings. Use \n for embedded newlines in
  single-line strings. A bare newline inside "..." or '...' is a SyntaxError.
- Test strings that embed XML-like content (e.g. <file> blocks) MUST use raw strings
  (r"...") or triple-quoted strings to avoid escaping issues.
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
- HTTP client for Supervisor API calls: `aiohttp.ClientSession`
- LLM API client: `anthropic` Python SDK in async mode (`anthropic.AsyncAnthropic()`). The SDK handles authentication, retries, rate limiting, and streaming — do not reimplement these with raw `aiohttp`.
- Logging: `structlog` — every log line includes `timestamp`, `level`, `event`, `component` ("prime"), and `generation` where applicable
- Type annotations: full coverage, Pyright strict compatible
- Validation: Pydantic v2 for all I/O boundary data (manifest, viability report, API responses)

### Python 3.14 syntax constraints

Python 3.14 enforces stricter syntax rules than earlier versions. Generated code MUST comply:

- **No implicit line continuation inside strings.** A newline character inside a `"..."` or
  `'...'` string literal is a `SyntaxError`. Python 3.12 made this a `SyntaxWarning`;
  Python 3.14 promotes it to a hard error.
  - Wrong: `s = "first line\nsecond line"` split naively across two physical lines
  - Correct: `s = "first line\nsecond line"` (on one line) or `s = """first line\nsecond line"""`
- **Triple quotes for multi-line strings.** Any string that spans multiple source lines MUST
  use `"""..."""` or `'''...'''`.
- **Test code is especially vulnerable.** Tests that embed XML-like content (`<file>` blocks,
  HTML tags, multi-line expected output) frequently trigger this error. Use raw strings
  (`r"..."`) or triple-quoted strings in test assertions.

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
| `CAMBRIAN_SUPERVISOR_URL` | MAY | `http://host.docker.internal:8400` | Supervisor endpoint (Prime runs in a container; use `host.docker.internal` to reach the host) |
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

## M2: Genome Evolution

This section is **inactive during M1**. It activates when `CAMBRIAN_MODE=m2` is set. Nothing in this section changes M1 behavior. It is written now so that the design is captured in the genome itself — the mutation strategy lives in the spec, not in infrastructure, so that it can itself evolve in M3+.

### Spec Mutation

The **Spec Mutator** is a component that reads spec variants and produces modified spec variants. It is not Prime. It operates outside the generation loop, between campaigns. It receives campaign summaries as input and produces candidate mutated specs as output. It MUST NOT call the Test Rig directly or read viability reports directly.

**Three mutation types:**

**Type 1 — Refinement** (small, targeted): Input: one spec variant + one campaign's failure mode distribution. Prompt: "Here is a spec. When Prime followed this spec, it consistently failed at [stage]. What single change to the spec would address this failure mode?" Output: one section rewritten or one paragraph added. Expected effect: immediate improvement. Protection needed: none — these almost always improve or maintain fitness.

**Type 2 — Section Transplant** (medium, structural): Input: two spec variants from different MAP-Elites niches. Prompt: "Spec A produces fast code. Spec B produces correct code. Rewrite Spec A's [section] using Spec B's approach to [concept]." Output: a hybrid spec. Expected effect: uncertain — the hybrid may combine strengths or be incoherent. Protection needed: coherence screening before campaign.

**Type 3 — Restructuring** (large, exploratory): Input: one spec + evidence of a plateau (fitness flat over N campaigns). Prompt: "This spec has reached a fitness plateau. Propose a fundamentally different approach to [section] that might break out of this local optimum." Output: section completely rewritten with different approach. Expected effect: highly uncertain. Protection needed: NEAT-style speciation (the new niche must be evaluated independently, not competing directly with the incumbent until it proves itself).

**Mutation constraints:**

- Sections marked `<!-- BEGIN FROZEN -->` MUST NOT be modified. The Spec Mutator checks this with byte-for-byte comparison after mutation — if any FROZEN section differs, the mutation is discarded.
- Every mutation produces a **complete spec**, not a diff. The diff is implicit in the git history.
- The mutation strategy defined in this section is itself mutable in M3+. To mutate the mutation strategy, the Spec Mutator would need to modify this section — which is permitted since this section is not FROZEN. This is intentional.

**Coherence screening:** Before a mutated spec enters a campaign, a fast screening model checks: (1) all MUST fields are still present in manifest and API sections, (2) FROZEN sections are unchanged (byte-for-byte), (3) the spec still describes an HTTP server on port 8401, (4) no internal contradictions between sections. Incoherent mutations are discarded without running a campaign.

**Dual model:** Use the larger model (Opus) for creative mutations (Types 1-3). Use the smaller model (Sonnet) for coherence screening. This prevents one model from generating and rubber-stamping its own output.

### Campaign

A **campaign** is a sequence of N generation attempts against a single spec variant. Default N: `CAMBRIAN_CAMPAIGN_LENGTH` (default 5).

At the end of a campaign, the Supervisor computes a **campaign summary**:

| Field | Computation |
|-------|-------------|
| `viability_rate` | viable generations / total generations |
| `fitness_mean` | mean fitness vector across all generations (partial where non-viable) |
| `fitness_trend` | slope of viability across generations (linear regression on [0,1] outcomes) |
| `failure_distribution` | count per `failure_stage` value |
| `stages_completed_distribution` | count per maximum stage reached across attempts |

**Campaign as unit of selection:** In M2, the spec is judged by its campaign summary, not any single generation. A spec that produces 80% viability over 5 generations is better than one that produces 1 perfect generation and 4 failures. Consistency matters more than peak performance. A positive `fitness_trend` rewards specs that "teach well" — where the LLM improves across retries within the campaign.

### Spec Archive

The spec archive is a **MAP-Elites grid** indexed by 4 behavioral dimensions. Each cell holds the best-scoring spec variant for that niche. The archive is sparse — most cells empty initially, filling as evolution progresses.

**Behavior descriptor (4 dimensions):**

| Dimension | Source | Buckets |
|-----------|--------|---------|
| Viability rate | campaign summary | 5: [0,0.2), [0.2,0.4), [0.4,0.6), [0.6,0.8), [0.8,1.0] |
| Token economy | mean(token_input + token_output) per generation | 4: <10K, <50K, <100K, ≥100K |
| Time to viability | generations until first viable artifact | 4: 1, 2, 3, ≥4 |
| Fitness trend | campaign viability slope | 3: declining (<-0.05), flat ([-0.05, 0.05]), improving (>0.05) |

Maximum archive capacity: 5 × 4 × 4 × 3 = 240 cells. In practice, the archive will be sparse for a long time.

**Storage:** `spec-archive.json` in the artifacts repo root. Each entry: `{cell_index, spec_hash, spec_path, campaign_summary, updated_at}`.

**Parent selection per mutation type:**
- Type 1 (refinement): select the cell with the highest `viability_rate` and most recent `failure_distribution`
- Type 2 (transplant): select two cells from distant regions of the grid (e.g., high viability + high economy)
- Type 3 (restructuring): select the cell with the longest plateau (same spec for ≥3 campaigns with flat trend)

**Why MAP-Elites over hill-climbing:** A spec optimal for the current task (echo server) may fail on future tasks. The archive preserves all behavioral niches, not just the current winner. When a new task tier activates (see Test Tiers below), archived variants from different niches provide a diverse starting population.

### Meta-Monitor

The **Meta-Monitor** is deterministic code (NOT an LLM) that watches campaign-level trends and adjusts meta-loop parameters. It implements VSM System 4 (intelligence function) — environmental scanning and adaptation. An LLM meta-monitor could learn to game its own observations; a deterministic one cannot.

**Triggers:**

| Condition | Detection | Response |
|-----------|-----------|----------|
| Archive collapsing | < 5 occupied cells after 10 campaigns | Increase Type 3 mutation rate to 50% |
| Fitness plateau | Best cell unchanged for 5 campaigns | Trigger one Type 2 transplant from distant niche |
| Budget burn | Projected exhaustion in < 10 campaigns at current rate | Halve campaign length (`CAMBRIAN_CAMPAIGN_LENGTH`) |
| Failure clustering | > 80% of failures at same stage | Increase Type 1 mutations targeting that stage |
| Failure random | No stage accounts for > 30% of failures | Decrease Type 1 rate, increase Type 3 |

**Neutral drift:** Every `CAMBRIAN_DRIFT_INTERVAL` campaigns (default 10), relax selection for `CAMBRIAN_DRIFT_LENGTH` campaigns (default 2). During neutral drift, all Tier 0 viable specs are added to the archive regardless of fitness ranking. This prevents monoculture and preserves structural diversity (Kimura neutral theory applied to spec evolution).

**Budget tracking:** The Meta-Monitor tracks cumulative token spend and projects remaining campaigns at the current burn rate. When projected spend exceeds `CAMBRIAN_TOKEN_BUDGET`, it stops the meta-loop gracefully: complete the current campaign, do not start new ones, write a final archive snapshot.

### Test Tiers

The Test Rig evaluates against progressively harder tiers. Each tier activates when the previous tier's `viability_rate` exceeds 80% across 3 consecutive campaigns. M1 operates at Tier 0 only.

| Tier | Requirement | Activation |
|------|-------------|------------|
| 0 | Basic viability: build, test, start, health pass | Always active (M1) |
| 1 | Behavioral contracts declared in spec pass | Tier 0 viability > 80% for 3 campaigns |
| 2 | Robustness: concurrent requests (10 simultaneous), malformed input returns 400, process survives SIGHUP | Tier 1 > 80% for 3 campaigns |
| 3 | Performance: p99 latency < 100ms, throughput > 1000 req/s for 10s, RSS < 50MB | Tier 2 > 80% for 3 campaigns |

The tier system adds checks **within** the health stage — the pipeline structure (build → test → start → health → report) is unchanged. Current tier is tracked in the archive alongside the viability rate.

**Why tiers:** Without progressive difficulty, a spec optimized for the echo server task will overfit. Tiers act as a Red Queen — the fitness landscape grows harder as the population improves. A spec that thrives at Tier 0 must prove itself at Tier 1 before it can dominate the archive.

---

```yaml
spec-version: "005"
version: "0.10.4"
organism: "cambrian"
lineage: "genesis"
language: "python 3.14 (M1)"
```

---

*This is a fixed point. An LLM reads this document and produces an organism
that reads this document and produces an organism. The code is disposable.
The spec is the genome. You are the phenotype.*
