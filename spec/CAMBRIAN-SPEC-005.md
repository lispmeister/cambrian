---
date: 2026-03-23
author: Markus Fix <lispmeister@gmail.com>
title: "Cambrian Genome: What Prime Is"
version: 0.14.4
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
- **Viability Report** — Structured JSON written by the Test Rig at `/output/viability-report.json` (see BOOTSTRAP-SPEC-002 §2.12 — the `/output` mount is separate from `/workspace` to prevent organism code from predicting or overwriting the report). Binary outcome: `viable` or `non-viable`. Read by Prime via the generation record.
- **Hash** — All hash values in Cambrian use the format `sha256:<64 hex characters>`. The `sha256:` prefix is literal text, not a URL scheme. Example: `sha256:a3b4c5...ef` (exactly 64 hex chars after the colon). An LLM MUST include this prefix when generating hash fields.

## Problem Statement

A self-reproducing code factory needs a genome — a document that, when read by an LLM, produces a complete working organism capable of reading the same document and reproducing. This spec is that genome.

The core challenge: the spec must be complete enough that a fresh LLM, with no external context, can produce a Prime that passes mechanical verification *and* can regenerate itself. Every ambiguity in the spec is a potential failure mode in the next generation.

## Goals

1. Define Prime completely enough that an LLM can produce a working implementation from this document alone.
2. Define all wire formats (manifest, viability report, API contracts) precisely enough to prevent drift across generations.
3. Carry the M2 mutation strategy in the genome itself — so mutation logic can evolve in M3+.

## Non-Goals

- Runtime performance optimization (M1 is functional, not fast)
- Multi-language support (Python 3.14 only)
- Production security (no authentication, no TLS)
- Dashboard or administrative UI
- Horizontal scaling or multi-Prime coordination

## Design Principles

1. **Fail loud.** Any ambiguity in verification produces a non-viable verdict, not a silent pass.
2. **Asyncio by default.** All I/O must be non-blocking. The HTTP server must remain responsive during generation.
3. **Never self-promote.** Prime MUST NOT declare itself viable. Only the Test Rig verdict counts.
4. **The manifest is a verified contract, not generated content.** Hashes and metadata are computed by Prime's own code, not emitted by the LLM.
5. **Specs before code.** When the spec and the implementation disagree, fix the spec first, then fix the code.

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
- `/stats` — `generation` is Prime's own generation number — the value of the `CAMBRIAN_GENERATION` environment variable set by the Supervisor at spawn time (same as the `generation` field in Prime's own manifest). This is a fixed identity; it does not change as the loop produces offspring. `status` is one of `idle`, `generating`, `verifying`. `uptime` is integer seconds since start. If `CAMBRIAN_GENERATION` is not set (e.g., local dev), `generation` is 0. **`uptime` SHOULD be computed using `time.monotonic()`, not `time.time()` — the system clock can jump backward on NTP adjustments, producing incorrect or negative uptime values.**

### Supervisor HTTP API (Prime calls these)

The Supervisor runs on the host at `CAMBRIAN_SUPERVISOR_URL` (default `http://host.docker.internal:8400`). Note: Prime runs inside a Docker container, so `localhost` refers to the container itself — `host.docker.internal` is required to reach the Supervisor on the host.

| Method | Path | Request Body | Success Response |
|--------|------|-------------|-----------------|
| GET | `/versions` | — | `[GenerationRecord, ...]` |
| GET | `/stats` | — | `{"generation": N, "status": "idle|spawning|testing|promoting|rolling-back", "uptime": N}` |
| POST | `/spawn` | `{"spec-hash": "...", "generation": N, "artifact-path": "gen-N"}` | `{"ok": true, "container-id": "...", "generation": N}` |
| POST | `/promote` | `{"generation": N}` | `{"ok": true, "generation": N}` |
| POST | `/rollback` | `{"generation": N}` | `{"ok": true, "generation": N}` |

All POST endpoints return `{"ok": false, "error": "..."}` on failure.

**GenerationRecord schema** (returned by `GET /versions` as an array):

| Field | Required | Rule |
|-------|----------|------|
| `generation` | MUST | Integer >= 1. The generation number. (Generation 0 is reserved for hand-crafted test artifacts; those do not receive GenerationRecords.) |
| `parent` | MUST | Integer >= 0. Parent generation (0 for bootstrap). |
| `spec-hash` | MUST | SHA-256 hex of the spec file (with `sha256:` prefix). |
| `artifact-hash` | MUST | SHA-256 hex of the artifact files (with `sha256:` prefix). |
| `outcome` | MUST | One of: `in_progress`, `tested`, `promoted`, `failed`, `timeout`. `in_progress` while the Test Rig runs. `tested` once the Test Rig exits (Prime then calls /promote or /rollback). Terminal states: `promoted`, `failed`, `timeout`. |
| `viability` | MAY | The full viability report. Present once outcome is `tested` or terminal. Absent while `in_progress`. |
| `artifact-ref` | MAY | Git ref (tag) pointing to the artifact: `gen-N` for promoted, `gen-N-failed` for failed. Absent while `in_progress`. |
| `created` | MUST | ISO-8601 timestamp (when spawn was received). |
| `completed` | MAY | ISO-8601 timestamp. Absent while `in_progress`. |
| `container-id` | MUST | Name of the Test Rig Docker container. |
| `campaign-id` | MAY | String. Groups generations into a campaign. Absent in M1. In M2, all generations run against the same spec variant share a `campaign-id`. Format: `campaign-<8-char-uuid>`. Consumers MUST treat absence as equivalent to no campaign. |

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
  "files": ["src/__init__.py", "src/prime.py", "tests/test_prime.py", "manifest.json", "spec/CAMBRIAN-SPEC-005.md"],
  "created-at": "2026-03-23T12:00:00Z",
  "entry": {
    "build": "uv pip install -r requirements.txt",
    "test": "python -m pytest tests/ -v",
    "start": "python -m src.prime",
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

**Field naming convention:** All JSON field names in manifests, generation records, and API bodies use kebab-case (hyphens). Python code uses snake_case internally. The canonical wire format is kebab-case. The contract schema inside `contracts` is an exception and uses snake_case keys (e.g., `body_contains`, `body_has_keys`).

**Viability report naming note:** The viability report schema uses snake_case for historical reasons (e.g., `failure_stage`, `duration_ms`). The `spec-vectors` and `contracts` keys remain kebab-case, and the contract objects inside them use the contract schema (including snake_case keys like `body_contains`). This mixed naming is an intentional exception to the kebab-case rule above.

**MUST fields:** `cambrian-version` (integer, currently 1), `generation` (integer >= 0; 0 is reserved for hand-crafted test artifacts, LLM-generated artifacts MUST use >= 1), `parent-generation` (integer >= 0, 0 for bootstrap), `spec-hash` (SHA-256 hex of the spec file with `sha256:` prefix), `artifact-hash` (SHA-256 hex of all artifact files except `manifest.json` — see algorithm below), `producer-model` (LLM model string), `token-usage` (object with `input` and `output` integers), `files` (array of all file paths, MUST include `manifest.json`, the spec file, and `src/__init__.py`), `created-at` (ISO-8601), `entry.build`, `entry.test`, `entry.start`, `entry.health`.

**`entry.start` — module form required.** When source lives under a package directory (e.g. `src/`), the start command MUST use `python -m src.prime`, not `python src/prime.py`. The script form adds the script's own directory to `sys.path`, so `from src.loop import …` fails at runtime even though pytest (which adds the working directory to `sys.path` automatically) passes it. The module form adds the working directory (`/workspace`) instead, making both absolute (`from src.loop import …`) and relative (`from .loop import …`) intra-package imports work correctly. The `src/` package directory MUST contain an `__init__.py` file (may be empty).

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

**MAY field:** `spec-lineage` (array of `sha256:...` hash strings): ordered list from oldest ancestor spec to immediate parent spec. Empty array `[]` for artifacts produced from the original human-written spec. Present when the spec was produced by M2+ mutation. M1 ignores this field; M2 uses it to reconstruct evolutionary history independent of git.

**Version compatibility:** `cambrian-version: 1` is the M1 contract defined in this document. Future versions (2+) MAY add fields (e.g., `mutation-type`, `parent-spec-hash`, `campaign-id`) that version-1 consumers ignore. The Test Rig MUST reject manifests with `cambrian-version` greater than its supported maximum and MUST accept manifests with `cambrian-version` at or below its supported maximum.

### Viability Report (Prime reads this)

Written by the Test Rig at `/output/viability-report.json` (the `/output` bind-mount is separate from `/workspace` — see BOOTSTRAP-SPEC-002 §2.12). The Supervisor reads it from the host-side temp directory and returns it in the generation record.

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

4. **Call the LLM.** Use the Anthropic API (`ANTHROPIC_API_KEY` from environment). Model: `CAMBRIAN_MODEL` (default: `claude-sonnet-4-6`) on the first attempt (`retry_count == 0`); `CAMBRIAN_ESCALATION_MODEL` (default: `claude-opus-4-6`) on any retry (`retry_count >= 1`). The response contains file contents in tagged blocks.

5. **Parse the response.** Extract files from `<file path="...">content</file:end>` blocks. Each block is one file. The `path` attribute is relative to the artifact root.

6. **Write files to workspace.** Create a subdirectory `./gen-{N}/` inside `/workspace` for the artifact. Write all parsed files into it. Copy the spec file into the artifact at its expected path. Write `manifest.json` with computed hashes and metadata. If the spec file contains a JSON array under a fenced code block marked with `contracts` (e.g., ` ```contracts `) include that array verbatim as the `contracts` field in `manifest.json`. This is how spec-defined contracts propagate to the manifest without passing through the LLM.

7. **Request verification.** `POST /spawn` with `"artifact-path": "gen-{N}"` (relative to `CAMBRIAN_ARTIFACTS_ROOT`), `spec-hash`, and `generation`. The Supervisor resolves the relative path to an absolute host path for the Docker bind mount. The Supervisor handles all git operations (branch creation, commit). Prime MUST NOT touch git. Prime MUST NOT send an absolute path — it runs inside Docker and cannot know the host-side path.

8. **Poll.** `GET /versions` until the generation record's outcome is no longer `in_progress`. The Supervisor sets the outcome to `tested` once the Test Rig container exits. Poll interval: 2 seconds.

9. **Decide.** Read the viability report from the generation record.
    - If viability status is `viable`: `POST /promote {"generation": N}`. The Supervisor performs the git merge and sets outcome to `promoted`. Done.
    - If viability status is `non-viable` and retries remain: `POST /rollback {"generation": N}`. The Supervisor tags the failed branch and sets outcome to `failed`. Go to step 3 with failure context (see [Informed Retry](#informed-retry)).
    - If viability status is `non-viable` and no retries: `POST /rollback {"generation": N}`. Stop.

### Retry semantics

Each retry is a NEW generation with a new number. Retries MUST NOT reuse previous LLM output — each call produces a complete codebase. The retry counter tracks how many consecutive failures have occurred for the same "intent" (same spec-hash). A successful promotion resets the counter.

Maximum retries: `CAMBRIAN_MAX_RETRIES` (default 3). Tracks consecutive failures for the same spec-hash. A successful promotion resets this counter.
Maximum generations: `CAMBRIAN_MAX_GENS` (default 5). Counts all generation attempts in a Prime's lifetime.

**Interaction:** Prime stops when *either* limit is reached first. `CAMBRIAN_MAX_RETRIES` prevents infinite loops on a broken spec; `CAMBRIAN_MAX_GENS` caps total cost. Example: with `MAX_RETRIES=3` and `MAX_GENS=5`, if the first 3 generations all fail consecutively, Prime stops (retries exhausted) even though only 3 of 5 generation slots are used.

## Failure Handling

| Failure | Trigger | Response |
|---------|---------|----------|
| Unparseable LLM output | `parse_files()` raises `ParseError` | Attempt parse repair (up to `CAMBRIAN_MAX_PARSE_RETRIES`, default 2). If all parse repairs fail, record as a failed attempt and retry with a fresh LLM call (up to `CAMBRIAN_MAX_RETRIES`). |
| Build failure | `entry.build` exits non-zero | Non-viable verdict from Test Rig. Rollback, then retry as informed retry. |
| Test failure | `entry.test` exits non-zero | Non-viable verdict. Rollback, then retry. |
| Start timeout | Prime doesn't bind HTTP port within 10s | Non-viable verdict. Rollback, then retry. |
| Health check failure | `GET /health` returns non-200 | Non-viable verdict. Rollback, then retry. |
| Supervisor unreachable | Network error on any Supervisor call | Exponential backoff: 1s, 2s, 4s, 8s, 16s, then 60s ceiling. Do not proceed without verification — never self-promote. |
| LLM rate-limited | HTTP 429 from LLM API | Respect `retry-after` header. Pause and retry. Do not count as a generation failure. |
| All retries exhausted | `CAMBRIAN_MAX_RETRIES` reached for one generation | Record generation as failed. Stop. Do not modify the spec. |
| Container crash | Test Rig container exits without writing viability report | Supervisor records `outcome: failed`. Treat as non-viable. Retry. |
| Supervisor crash (M1) | Supervisor restarts while a generation is `in_progress` | Recovery is manual in M1: restart the Supervisor, inspect `generations.json`, manually set the orphaned record to `failed`. The background asyncio task monitoring the container is lost on restart. Automatic crash recovery is deferred to M2. |

## LLM Integration

### Output format

Prime instructs the LLM to emit files as tagged blocks:

```
<file path="src/prime.py">
#!/usr/bin/env python3
"""Prime — the organism."""
...
</file:end>

<file path="tests/test_prime.py">
...
</file:end>
```

Prime parses these blocks with a **line-by-line state machine** — NOT a dotall regex. A dotall regex fails when file content contains `<file>` or `</file>` literals (e.g. in test fixtures), because the non-greedy `.*?` stops at the first `</file:end>` it finds, silently truncating the block. The state machine is not confused by this.

```python
current_path: str | None = None
current_lines: list[str] = []
files: dict[str, str] = {}
for line in response.splitlines(keepends=True):
    if current_path is None:
        m = re.match(r'<file path="([^"]+)">', line)
        if m:
            current_path = m.group(1)
            current_lines = []
    elif line.rstrip("\n\r") == "</file:end>":
        files[current_path] = "".join(current_lines)
        current_path = None
    else:
        current_lines.append(line)
if current_path is not None:
    raise ParseError(f"Unclosed <file path={current_path!r}> block")
```

A response that opens a `<file>` block without a matching `</file:end>` is malformed — raise `ParseError`. Anything outside `<file>` blocks (LLM commentary) is silently discarded. The `:end` suffix makes the closing delimiter unique — it cannot appear in natural file content.

**Critical: the close-tag match is exact, not a substring search.** The condition `line.rstrip("\n\r") == "</file:end>"` matches ONLY when the entire line (stripped of newlines) is `</file:end>`. A line like `x = "</file:end>"` or `# ends with </file:end>` does NOT match — the content is accumulated as-is. A common implementation mistake is `"</file:end>" in line` (substring check), which incorrectly closes blocks when `</file:end>` appears anywhere on the line.

**Test vectors for `parse_files()`** — two generations failed on `test_parse_multiple_close_tags_in_content` due to the substring check mistake. These vectors pin the correct behavior:

```python
# Vector 1: </file:end> embedded in a longer line — NOT a close tag
response = '<file path="a.py">\nx = "</file:end>"\n</file:end>\n'
assert parse_files(response) == {"a.py": 'x = "</file:end>"\n'}

# Vector 2: Multiple files in sequence
response = (
    '<file path="a.py">\nfoo\n</file:end>\n'
    '<file path="b.py">\nbar\n</file:end>\n'
)
assert parse_files(response) == {"a.py": "foo\n", "b.py": "bar\n"}

# Vector 3: </file:end> alone on its own line ALWAYS closes the block —
# content after it (before the next <file> header) is silently discarded.
response = '<file path="a.py">\nline1\n</file:end>\ndiscarded\n'
assert parse_files(response) == {"a.py": "line1\n"}
# (The unclosed trailing content after </file:end> is discarded because no
#  <file> header opens a new block — it is not a ParseError.)
```

### Fresh generation prompt

**System message:**

```
You are a code generator. You produce complete, working Python codebases from specifications.

Rules:
- Output ONLY <file path="...">content</file:end> blocks. One block per file.
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

The informed retry uses the **same system message** as the fresh generation prompt (see above). Only the user message changes — it adds failure context:

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

### Parse repair prompt

When `parse_files()` raises `ParseError`, Prime MAY attempt an in-place repair before counting the failure as a generation retry. The repair prompt feeds the raw malformed response back to the LLM with an explicit error message. Use the same model as the current generation attempt — do NOT escalate for parse repairs.

**User message:**

```
# Parse Error

The previous response could not be parsed. Error: {parse_error_message}

# Malformed Response

{raw_llm_response}

# Task

Re-emit the EXACT SAME files using the correct format. Every <file> block MUST have a
matching </file:end> on its own line. No nesting. No extra content between blocks.
```

Maximum parse retries: `CAMBRIAN_MAX_PARSE_RETRIES` (default 2). A successful parse after a repair does NOT consume a generation retry. If all parse repairs fail, the generation counts as one failed attempt and the normal retry logic applies.

## Implementation Requirements

### Language and runtime

- Python 3.14 (free-threaded build deferred to M2)
- All I/O-bound code MUST use `asyncio`
- HTTP server: `aiohttp`
- HTTP client for Supervisor API calls: `aiohttp.ClientSession`. **Supervisor API calls SHOULD use exponential backoff on network errors: start at 1s, double each retry, cap at 60s. Catch `aiohttp.ClientError` specifically — do not swallow all exceptions. Example:**
  ```python
  backoff = 1.0
  while True:
      try:
          async with session.get(url) as resp:
              return await resp.json()
      except aiohttp.ClientError:
          await asyncio.sleep(backoff)
          backoff = min(backoff * 2, 60.0)
  ```
- LLM API client: `anthropic` Python SDK in async mode (`anthropic.AsyncAnthropic()`). The SDK handles authentication, retries, rate limiting, and streaming — do not reimplement these with raw `aiohttp`.
- **`call_llm()` MUST use streaming.** Use `async with client.messages.stream(...) as stream: message = await stream.get_final_message()`. Do NOT use `client.messages.create()` — the SDK raises an error for large `max_tokens` values with non-streaming calls. This applies regardless of `max_tokens` value.
- Logging: `structlog` — every log line includes `timestamp`, `level`, `event`, `component` ("prime"), and `generation` where applicable

  **structlog API — the first positional argument IS the event string.** Unlike stdlib `logging`, structlog does not take a format string. The first positional arg is stored as the `event` key.
  - Correct: `log.info("prime_starting", component="prime", generation=1)`
  - WRONG: `log.info("event", event="prime_starting")` — passes `event` twice → `TypeError: got multiple values for argument 'event'`
  - WRONG: `log.info("Starting generation %d", gen)` — structlog does not interpolate; produces a literal `%d` in the event string
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
  __init__.py       — required; makes src/ a package so `python -m src.prime` works
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
- LLM response parsing extracts files from `<file>` blocks correctly (state machine, not regex)
- LLM response parsing handles malformed responses gracefully (raises `ParseError`)
- Parse repair loop retries on `ParseError` up to `CAMBRIAN_MAX_PARSE_RETRIES`
- Generation number is computed correctly from history
- Retry counter increments and respects `CAMBRIAN_MAX_RETRIES`
- Model escalates to `CAMBRIAN_ESCALATION_MODEL` on `retry_count >= 1`
- **Every test function MUST import the symbols it uses** at the top of the function body (e.g. `from src.prime import make_app`). Python function scopes are isolated — a local import in `test_health()` is NOT visible to `test_stats()`. Do NOT rely on module-level imports; each test function must be self-contained. This applies to ALL test functions, not just the first few.

Tests MUST be runnable with `python -m pytest tests/ -v`.

#### aiohttp test pattern

Use the `aiohttp_client` pytest fixture — do NOT use `AioHTTPTestCase` or `@unittest_run_loop`. Both are deprecated in aiohttp 3.8+ and break in 3.10+.

Correct pattern:
```python
# conftest.py or top of test file — configure pytest-asyncio
# pytest.ini or pyproject.toml: asyncio_mode = "auto"

async def test_health(aiohttp_client):
    from src.prime import make_app
    client = await aiohttp_client(make_app())
    resp = await client.get("/health")
    assert resp.status == 200
```

For a mock Supervisor server, use `aiohttp_server`:
```python
async def test_spawn(aiohttp_server):
    app = web.Application()
    app.router.add_post("/spawn", my_handler)
    server = await aiohttp_server(app)
    # use server.make_url("/spawn")
```

`pytest-asyncio` in AUTO mode (`asyncio_mode = "auto"` in `pytest.ini`) means `async def test_*` functions run automatically without `@pytest.mark.asyncio`.

## Configuration

| Variable | Required | Default | Purpose |
|----------|----------|---------|---------|
| `ANTHROPIC_API_KEY` | MUST | — | LLM API authentication |
| `CAMBRIAN_MODEL` | MAY | `claude-sonnet-4-6` | LLM model for the first generation attempt |
| `CAMBRIAN_ESCALATION_MODEL` | MAY | `claude-opus-4-6` | LLM model for retry attempts (`retry_count >= 1`) |
| `CAMBRIAN_MAX_GENS` | MAY | `5` | Max generation attempts before stopping |
| `CAMBRIAN_MAX_RETRIES` | MAY | `3` | Max consecutive failures before stopping |
| `CAMBRIAN_MAX_PARSE_RETRIES` | MAY | `2` | Max parse repair attempts before counting as a generation failure |
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
- LLM response parsing extracts `<file>` blocks correctly (state machine)
- Parse repair loop retries up to `CAMBRIAN_MAX_PARSE_RETRIES` on `ParseError`
- Generation loop calls Supervisor API in correct sequence
- Retry logic stops at `CAMBRIAN_MAX_RETRIES`
- Model escalates to `CAMBRIAN_ESCALATION_MODEL` on retry

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

## Verification Layers

A generation controls its own code, including its own tests. Without independent verification, an offspring could "cheat" — writing trivial tests that pass but don't verify the spec's actual requirements. This section defines three verification layers, each catching what the others miss. Together they ensure that fitness is measured against the spec, not against self-generated criteria.

**Threat model:** The entity being evaluated (the offspring) controls its source code, test suite, and manifest contracts. It does NOT control: the spec (FROZEN blocks are byte-for-byte immutable), the Test Rig (baked into the Docker image), or the Supervisor (runs on the host). The verification layers exploit this trust boundary.

### Layer 0 — Self-Tests (existing, gameable)

The offspring's own test suite, run by `entry.test` during the Test Rig's test stage. This is useful for internal correctness — a well-written test suite catches implementation bugs. But it is the weakest verification signal because the offspring controls both the tests and the code under test.

**What it catches:** Internal bugs, regressions within the offspring's own logic.
**What it misses:** Spec violations, shallow tests, tautological assertions.

### Layer 1 — Spec Acceptance Vectors (FROZEN, mechanical, no LLM)

Machine-readable test vectors embedded in the spec inside a FROZEN block. The Test Rig reads them directly from the spec file in `/workspace` and evaluates them during the health stage, alongside any manifest-defined contracts. Because the vectors live in a FROZEN block, the offspring cannot modify them — they are part of the immutable genome.

**What it catches:** Spec contract violations, API schema drift, missing endpoints.
**What it misses:** Behavioral correctness beyond the declared vectors, edge cases not anticipated by the spec author.

The vectors are defined below in the [Acceptance Vectors](#acceptance-vectors) section. The Test Rig implementation is specified in BOOTSTRAP-SPEC-002 § 2.9.

### Layer 2 — Dual-Blind Examiner (LLM, independent, M2)

An independent LLM generates test cases from the spec alone, without seeing the offspring's code. The Supervisor orchestrates this as a separate step after the Test Rig completes. The examiner and the code author share only the spec — they have no channel to collude.

**What it catches:** Shallow self-tests (the examiner tests spec requirements the offspring chose not to test), tautological assertions (the examiner derives expectations from the spec, not from the code).
**What it misses:** Implementation-specific edge cases that require reading the code.

**Activation:** M2 only (`CAMBRIAN_MODE=m2`). Specified in BOOTSTRAP-SPEC-002 § 2.10.

### Layer 3 — Adversarial Red-Team (LLM, hostile, M2)

An independent LLM receives the spec AND the offspring's source code with the instruction: "find violations — places where the code doesn't match the spec, handles edge cases incorrectly, or could fail under realistic conditions." It produces failing test cases. If any pass (i.e., the test case reveals a real bug), the offspring's fitness is penalized.

**What it catches:** Semantic mismatches between spec and code, edge cases, error handling gaps.
**What it misses:** Nothing systematically — this is the most powerful layer, but also the most expensive (additional LLM call per generation).

**Activation:** M2 only (`CAMBRIAN_MODE=m2`). Specified in BOOTSTRAP-SPEC-002 § 2.11.

### Verification Matrix

| Layer | Trust | LLM? | Cost | Catches | Activation |
|-------|-------|------|------|---------|------------|
| 0: Self-Tests | Offspring controls | No | Zero | Internal bugs | Always (M1) |
| 1: Spec Vectors | FROZEN in spec | No | Zero | Contract violations | Always (M1) |
| 2: Dual-Blind | Independent LLM | Yes | ~1 call | Shallow self-tests | M2 |
| 3: Red-Team | Independent LLM | Yes | ~1 call | Semantic mismatches | M2 |

**Composition:** Layer 0 runs during the `test` stage. Layers 1 runs during the `health` stage (as spec-derived contracts). Layers 2 and 3 run as Supervisor-orchestrated post-verification steps in M2, after the Test Rig container exits. A generation must pass ALL active layers to be considered viable.

### Acceptance Vectors

<!-- BEGIN FROZEN: acceptance-vectors -->

The following test vectors are machine-readable acceptance criteria. The Test Rig reads this section from the spec file and evaluates each vector during the health stage. The offspring cannot modify this block — it is FROZEN.

**Format:** Each vector is a YAML document in a fenced code block. The Test Rig extracts all `spec-vector` code blocks from between the FROZEN markers. Each vector has:
- `name`: unique identifier (used in viability report)
- `type`: `http` (same contract types as manifest contracts)
- `method`, `path`, `expect`: same schema as manifest contracts (see BOOTSTRAP-SPEC-002 § 2.5)

```spec-vector
name: sv-health-liveness
type: http
method: GET
path: /health
expect:
  status: 200
```

```spec-vector
name: sv-health-body
type: http
method: GET
path: /health
expect:
  status: 200
  body:
    ok: true
```

```spec-vector
name: sv-stats-schema
type: http
method: GET
path: /stats
expect:
  status: 200
  body_has_keys:
    - generation
    - status
    - uptime
```

```spec-vector
name: sv-stats-generation
type: http
method: GET
path: /stats
expect:
  status: 200
  body_contains:
    generation: "$GENERATION"
```

```spec-vector
name: sv-stats-status-is-string
type: http
method: GET
path: /stats
expect:
  status: 200
  body_has_keys:
    - status
```

<!-- END FROZEN: acceptance-vectors -->

**Spec vector rules:**

- Spec vectors are evaluated BEFORE manifest contracts. If any spec vector fails, the health stage fails regardless of manifest contract results.
- Spec vectors use the same evaluation rules as manifest contracts (see BOOTSTRAP-SPEC-002 § 2.5): `$GENERATION` substitution, 10-second timeout per vector, no short-circuit (all vectors evaluated).
- Spec vector results appear in the viability report under `checks.health.spec-vectors`, parallel to `checks.health.contracts`.
- A spec without an `acceptance-vectors` FROZEN block has no spec vectors — the Test Rig skips this step. This makes the feature backward-compatible with specs that predate it.
- The Minimal Spec (used for M1 operationality testing) MAY define its own acceptance vectors appropriate to its simpler contract (e.g., echo server health check).

## Examples

### Happy path: Gen-1 reproduces to Gen-2

1. Gen-1 Prime starts. `CAMBRIAN_GENERATION=1`. `/stats` returns `{"generation": 1, "status": "idle", "uptime": 0}`.
2. Prime calls `GET /versions` → `[]` (no history yet). Offspring will be generation 2.
3. Prime reads the spec, hashes it: `spec-hash = sha256:a3b4...`.
4. Prime calls the LLM (claude-sonnet-4-6). The LLM emits `<file path="src/prime.py">...</file:end>` blocks.
5. Prime writes files to `/workspace/gen-2/`, computes `artifact-hash`, writes `manifest.json` with `{"generation": 2, "created-at": "2026-03-23T12:00:00Z", ...}`.
6. Prime calls `POST /spawn {"generation": 2, "artifact-path": "gen-2", "spec-hash": "sha256:a3b4..."}`.
7. Supervisor creates git branch `gen-2`, commits the artifact, starts a Test Rig container.
8. Prime polls `GET /versions` every 2 seconds. After 45 seconds, the record shows `outcome: tested, viability: {status: viable, ...}`.
9. Prime calls `POST /promote {"generation": 2}`. Supervisor merges `gen-2` to main, tags `gen-2`, sets `outcome: promoted`.
10. Prime stops (one successful promotion per loop).

### Retry path: Gen-2 fails, Gen-3 fixes it

1. Prime produces Gen-2. Test Rig reports `status: non-viable, failure_stage: test, diagnostics: {summary: "3 of 8 tests failed", ...}`.
2. Prime calls `POST /rollback {"generation": 2}`. Supervisor tags `gen-2-failed`.
3. Prime reads the failed source code from `/workspace/gen-2/` and the diagnostics.
4. Prime calls the LLM with the informed retry prompt (same system message, enriched user message with failed code + diagnostics). Model escalates to `CAMBRIAN_ESCALATION_MODEL` (claude-opus-4-6).
5. Prime produces Gen-3. Test Rig reports `status: viable`. Prime promotes Gen-3.

## References

- [BOOTSTRAP-SPEC-002](BOOTSTRAP-SPEC-002.md) — Supervisor, Test Rig, Docker infrastructure, and bootstrap procedure
- [SPEC-STYLE-GUIDE](SPEC-STYLE-GUIDE.md) — Conventions for writing and versioning specs
- [Loom](https://github.com/lispmeister/loom) — Predecessor project (archived at v0.2.0)

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

**Coherence screening:** Before a mutated spec enters a campaign, a deterministic grammar validator checks: (1) all required ## sections are still present, (2) FROZEN sections are unchanged (byte-for-byte), (3) the spec still describes an HTTP server on port 8401, (4) at least one MUST/SHALL/MAY normative keyword is present. Incoherent mutations are discarded without running a campaign. This screening is deliberately deterministic rather than LLM-based — an LLM screening its own output can learn to pass its own checks (adversarial review §5). Implemented in `supervisor/spec_grammar.py`.

**LLM mutation model:** Use the larger model (Opus) for creative mutations (Types 1-3) via `CAMBRIAN_MUTATION_MODEL` (default: `claude-opus-4-6`). Budget-constrained runs may set `CAMBRIAN_MUTATION_MODEL=claude-sonnet-4-6`.

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
| `generation_count` | total number of generation attempts in this campaign |

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

**Stage 1 implementation note (M2 Stage 1):** MAP-Elites requires 200+ evaluations to populate meaningfully. Stage 1 has budget for ~20-30 campaigns. For Stage 1, the archive is replaced by a **single-objective Bayesian Optimization loop** (Gaussian Process + Expected Improvement via scikit-optimize) that maximizes `viability_rate`. The feature space is per-section line-delta from the base spec (one Real dimension per evolvable section). MAP-Elites activates in Stage 2+ when the evaluation budget supports it. Implemented in `supervisor/bo_loop.py`.

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
| 0 | Basic viability: build, test, start, health pass. Includes spec acceptance vectors (§ Verification Layers, Layer 1). | Always active (M1) |
| 1 | Behavioral contracts + dual-blind examiner (Layer 2) finds no spec violations. | Tier 0 viability > 80% for 3 campaigns |
| 2 | Robustness checks + adversarial red-team (Layer 3) score above threshold. | Tier 1 > 80% for 3 campaigns |
| 3 | Performance: p99 latency < 100ms, throughput > 1000 req/s for 10s, RSS < 50MB | Tier 2 > 80% for 3 campaigns |

The tier system adds checks **within** the health stage — the pipeline structure (build → test → start → health → report) is unchanged. Current tier is tracked in the archive alongside the viability rate. Verification Layers 2 and 3 (§ Verification Layers) are Supervisor-orchestrated post-verification steps that activate at Tier 1 and Tier 2 respectively — see BOOTSTRAP-SPEC-002 §§ 2.10–2.11.

**Why tiers:** Without progressive difficulty, a spec optimized for the echo server task will overfit. Tiers act as a Red Queen — the fitness landscape grows harder as the population improves. A spec that thrives at Tier 0 must prove itself at Tier 1 before it can dominate the archive.

---

```yaml
spec-version: "005"
version: "0.14.4"
organism: "cambrian"
lineage: "genesis"
language: "python 3.14 (M1)"
```

---

*This is a fixed point. An LLM reads this document and produces an organism
that reads this document and produces an organism. The code is disposable.
The spec is the genome. You are the phenotype.*
