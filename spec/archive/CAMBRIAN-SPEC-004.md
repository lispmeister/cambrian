---
date: 2026-03-21
author: Markus Fix <lispmeister@gmail.com>
title: "Cambrian: Self-Reproducing Code Factory"
tags: [cambrian, prime, self-hosting, spec, M1]
ancestor: CAMBRIAN-SPEC-003
---

# CAMBRIAN-SPEC-004

## Overview

Cambrian is a self-reproducing code factory. A running instance (Prime) reads a specification, calls an LLM, and produces a complete working codebase — including a new Prime capable of doing the same thing. The code is disposable; the specification is the genome.

Three components cooperate. Prime is the organism — it generates code from a spec and decides whether offspring are viable. The Supervisor is host-side infrastructure — it manages containers, tracks generation history, and executes promote/rollback operations. The Test Rig is a mechanical verification pipeline — it builds, tests, starts, and health-checks an artifact without any LLM involvement.

This spec has two audiences. Sections 1–10 follow the standard spec structure and are written for agents implementing the system. Sections 11–12 separate the environment (infrastructure that persists across generations) from the genome (the definition of what Prime is, readable by an LLM to produce a working organism).

The runtime execution path:

- Prime loads the spec from its local filesystem
- Prime sends the spec + generation history to an LLM
- The LLM produces a complete codebase; Prime writes it to a workspace with a `manifest.json`
- Prime commits the artifact to a `gen-N` git branch
- Prime asks the Supervisor to spawn a Test Rig container with the artifact
- The Test Rig reads `manifest.json`, builds, tests, starts, health-checks, writes a viability report
- The Supervisor collects the report and returns it to Prime
- Prime tells the Supervisor to promote (merge + tag) or rollback (discard branch)

## Problem Statement

Loom (the predecessor project) proved that an LLM-powered agent can autonomously modify its own source code, verify the changes, and promote them. It also proved that source-code-level evolution is the wrong abstraction:

- **Cruft accumulation.** Each generation produces a diff against the current codebase. Over 72 generations, diffs interacted unpredictably, code style drifted across models, and dead code accumulated.
- **Path dependence.** The order of promotions mattered. The codebase became a product of its history, not its specification.
- **Low viability rate.** 72 generations attempted, 1 promoted (1.4%). Only the most expensive model (Opus) succeeded. Cheaper models could not produce code that passed two-stage verification.
- **Model capability bottleneck.** LLMs struggle with editing existing ClojureScript — they stumble over parentheses and produce subtle bugs in async code. The training data for Lisp dialects is thin.

The core insight: evolve the specification (genotype), regenerate the entire codebase from scratch each generation (phenotype). No accumulated cruft. No path dependence. Every generation starts clean.

## Goals

- Produce a self-hosted Prime that can reproduce itself from its own spec (M1).
- Define a manifest contract that decouples the Test Rig from the implementation language.
- Build a mechanical Test Rig that verifies viability without any LLM involvement.
- Record every generation attempt in an append-only audit trail.
- Support multiple implementation languages — the spec MUST NOT prescribe a language.
- Keep the Supervisor as minimal host-side infrastructure, not part of the organism.

## Non-Goals

- **Spec mutation.** Prime reads the spec, never modifies it. Spec evolution is M2+.
- **Fitness ranking.** M1 is binary: viable or not. Fitness comparison between organisms is M2+.
- **Human interaction via chat.** Prime does not expose a conversational interface in M1.
- **Multi-Lab tournament selection.** One organism is generated and tested at a time.
- **Distributed or cloud deployment.** M1 runs locally on a single host.
- **Income generation or economic autonomy.** Out of scope entirely for M1.
- **Crossover between specs.** Single-lineage reproduction only.

## Design Principles

### The spec is sacred

Prime reads the spec. Prime MUST NOT modify the spec. The spec is the genome — it defines what Prime is. If the spec changes, it changes through an external process (human editing or future M2+ mutation), never through Prime's own initiative during M1.

### Containers are the keep/revert boundary

A generation lives or dies at the container level. Promote means merge the branch and tag it. Revert means destroy the container and discard the branch. There is no partial promotion, no rollback-after-promote. The decision is final and recorded. Containers are Docker containers — chosen for cross-platform compatibility (macOS and Linux).

### Git is the version system

Every generation attempt is a `gen-N` branch. Every promotion creates an annotated tag. History is immutable. Tags provide unlimited rollback depth. Git is chosen explicitly because it is universal, every implementation language has tooling for it, and LLMs understand git operations.

### Reproduce first, improve later

M1 is reproduction. The organism reads its spec and produces a viable offspring that can do the same. Self-modification, optimization, and selection come after reproduction is proven. Do not build M2 infrastructure during M1.

### The environment judges, not the organism

Viability is determined by the Test Rig (environment), not by Prime (organism). Prime MUST NOT self-assess viability. Prime requests verification and accepts the verdict. This prevents organisms from gaming their own fitness criteria.

### Append-only audit trail

Every generation attempt — successful or not — is recorded. Records are never deleted. An in-progress record is updated exactly once to its terminal state (promoted, failed, or timeout) — this single mutation is the lifecycle transition, not a correction or overwrite. Once a record has a terminal outcome it is immutable. The history is the ground truth for debugging, analysis, and future fitness evaluation.

## Model

The durable abstractions of the system. These terms are used consistently throughout the spec.

- **Spec** — This document (or the Minimal Spec for operationality tests). The genome. Defines what Prime is. Input to code generation.
- **Prime** — The organism. A running process that reads the spec, calls an LLM, produces code, and requests verification. Contains its own source code and spec.
- **Artifact** — A directory containing everything needed to run Prime: source code, test suite, spec, and `manifest.json`. Produced by Prime during generation. Immutable once committed.
- **Manifest** — `manifest.json` at the artifact root. The fixed-point contract between organism and environment. Describes how to build, test, and start the organism.
- **Supervisor** — Host-side infrastructure. Manages container lifecycle, generation history, and promote/rollback operations. Not part of the organism.
- **Test Rig** — A mechanical verification pipeline. Reads the manifest, builds the artifact, runs tests, starts Prime, health-checks it, writes a viability report. No LLM. No agentic loop.
- **Generation** — One attempt to produce a viable organism. Each generation gets a number, a git branch (`gen-N`), and an audit record.
- **Viability Report** — Structured JSON written by the Test Rig. Binary outcome: viable or non-viable. Read by the Supervisor and forwarded to Prime.
- **Generation Record** — One entry per generation attempt in the append-only history. Records outcome, hashes, timing, and the viability report.
- **Minimal Spec** — A small, self-contained specification used to test that a Prime is operational without requiring full self-reproduction. Produces a simple artifact (not another Prime). Used as the termination criterion for the M1 acceptance test.

Relationships:

- Prime reads the Spec and produces an Artifact.
- The Artifact contains a Manifest.
- The Supervisor spawns a container, mounts the Artifact, runs the Test Rig.
- The Test Rig reads the Manifest, executes the pipeline, writes a Viability Report.
- The Supervisor reads the Viability Report and returns it to Prime.
- Prime tells the Supervisor to promote or rollback. The Supervisor records the outcome in a Generation Record.

## Contracts and Schemas

### Artifact Manifest (fixed-point contract)

The manifest is the interface between organism and environment. It MUST NOT change across generations. Both the Test Rig and Prime conform to it.

Every artifact MUST include a `manifest.json` at its root.

**Schema:**

```json
{
  "cambrian-version": 1,
  "generation": 1,
  "parent-generation": 0,
  "spec-hash": "sha256:abc123...",
  "artifact-hash": "sha256:def456...",
  "producer-model": "claude-opus-4-6",
  "token-usage": {"input": 45000, "output": 12000},
  "files": [
    "src/prime.py",
    "src/api.py",
    "tests/test_prime.py",
    "manifest.json",
    "spec/CAMBRIAN-SPEC-004.md"
  ],
  "created_at": "2026-03-21T14:30:00Z",
  "entry": {
    "build": "pip install -r requirements.txt",
    "test": "python -m pytest tests/",
    "start": "python src/prime.py",
    "health": "http://localhost:8401/health"
  },
  "contracts": [
    {
      "name": "health-liveness",
      "type": "http",
      "method": "GET",
      "path": "/health",
      "expect": {"status": 200, "body": {"ok": true}}
    }
  ]
}
```

**Field rules:**

| Field | Required | Rule |
|-------|----------|------|
| `cambrian-version` | MUST | Integer. Currently `1`. |
| `generation` | MUST | Integer >= 0. `0` for hand-crafted test artifacts only; >= 1 for LLM-generated artifacts. Monotonically increasing across generated artifacts. |
| `parent-generation` | MUST | Integer >= 0. `0` for bootstrap-produced artifacts. |
| `spec-hash` | MUST | SHA-256 hex digest of the spec file used to generate this artifact. |
| `artifact-hash` | MUST | SHA-256 hex digest of all artifact files except `manifest.json`. |
| `producer-model` | MUST | LLM model identifier string. |
| `token-usage` | MUST | Object with `input` and `output` integer fields. |
| `files` | MUST | Array of all file paths in the artifact. MUST include `manifest.json` and the spec file. |
| `created_at` | MUST | ISO-8601 timestamp. |
| `entry.build` | MUST | Shell command to build/install. MUST exit 0 on success. |
| `entry.test` | MUST | Shell command to run the test suite. MUST exit 0 on all-pass. |
| `entry.start` | MUST | Shell command to start Prime as a long-running process. |
| `entry.health` | MUST | URL. Test Rig sends GET; MUST return HTTP 200 when Prime is ready. |
| `contracts` | MAY | Array of verification contract objects. If absent, Test Rig uses hard-coded health checks. If present, Test Rig evaluates each contract during health-check stage. See BOOTSTRAP-SPEC-002 §2.5 for contract schema and evaluation rules. |

**Notes:**

- `artifact-hash` excludes `manifest.json` to avoid circular hashing.
- `entry` commands are executed by the Test Rig in a container with the artifact mounted at `/workspace`. The working directory is `/workspace`.
- `entry.start` MUST start Prime in the foreground. The Test Rig manages the process lifecycle.

### Viability Report

Written by the Test Rig to `/workspace/viability-report.json`. Read by the Supervisor after the container exits.

**Schema:**

**Viable example:**

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
  "completed_at": "2026-03-21T14:32:00Z"
}
```

**Non-viable example (with diagnostics):**

```json
{
  "generation": 2,
  "status": "non-viable",
  "failure_stage": "test",
  "checks": {
    "manifest": {"passed": true},
    "build": {"passed": true, "duration_ms": 2800},
    "test": {"passed": false, "tests_run": 15, "tests_passed": 8, "duration_ms": 6200},
    "start": {"passed": false, "duration_ms": 0},
    "health": {"passed": false, "duration_ms": 0}
  },
  "diagnostics": {
    "stage": "test",
    "summary": "7 of 15 tests failed",
    "exit_code": 1,
    "failures": [
      {"test": "tests/test_api.py::test_spawn_returns_container_id", "error": "AssertionError: expected 'lab-gen-1', got None", "file": "tests/test_api.py", "line": 42}
    ],
    "stdout_tail": "...last 100 lines...",
    "stderr_tail": "...last 100 lines..."
  },
  "completed_at": "2026-03-21T14:35:00Z"
}
```

**Field rules:**

| Field | Required | Rule |
|-------|----------|------|
| `generation` | MUST | Integer matching the artifact's generation. |
| `status` | MUST | One of: `viable`, `non-viable`. |
| `failure_stage` | MUST | One of: `none`, `manifest`, `build`, `test`, `start`, `health`. First stage that failed, or `none`. |
| `checks.*` | MUST | Each check MUST include `passed` (boolean). `duration_ms` SHOULD be included. `test` MUST include `tests_run` and `tests_passed`. `health` MAY include a `contracts` sub-object with per-contract results when manifest contracts are present. |
| `completed_at` | MUST | ISO-8601 timestamp. |
| `diagnostics` | MAY | Object. Present when `status` is `non-viable`. Contains structured failure context for the failed stage: `stage`, `summary`, `exit_code`, `failures[]`, `stdout_tail`, `stderr_tail`. See BOOTSTRAP-SPEC-002 §2.6 for full schema and per-stage formats. |

**Notes:**

- The Test Rig exits 0 if `status` is `viable`, exit 1 if `non-viable`.
- Pipeline is fail-fast: if `build` fails, `test`/`start`/`health` are not attempted. Their `passed` fields are set to `false` with `duration_ms: 0`.
- `diagnostics` is absent when `status` is `viable`. Existing report consumers that do not expect `diagnostics` are unaffected.

### Fitness Vector

The viability report SHOULD include a `fitness` object — a quantitative characterization of the artifact, computed by the Test Rig from data it already collects. Fitness is present for both viable and non-viable reports (non-viable reports include partial metrics up to the failed stage).

For M1, fitness is informational only — viability remains binary. For M2+, the fitness vector provides the measurement apparatus for selection between viable organisms.

**Schema:**

```json
{
  "fitness": {
    "build_duration_ms": 3200,
    "test_duration_ms": 8400,
    "test_count": 15,
    "test_pass_rate": 1.0,
    "start_duration_ms": 1200,
    "health_duration_ms": 50,
    "total_duration_ms": 12850,
    "source_files": 5,
    "source_lines": 342,
    "test_files": 2,
    "test_lines": 187,
    "dependency_count": 3,
    "token_input": 45000,
    "token_output": 12000
  }
}
```

**Field rules:**

| Field | Required | Rule |
|-------|----------|------|
| `fitness` | SHOULD | Object. Present in all viability reports. Partial when non-viable (only metrics from completed stages). |
| `fitness.build_duration_ms` | SHOULD | Integer. Time to execute `entry.build`. From `checks.build.duration_ms`. |
| `fitness.test_duration_ms` | SHOULD | Integer. Time to execute `entry.test`. From `checks.test.duration_ms`. |
| `fitness.test_count` | SHOULD | Integer. Number of tests run. From `checks.test.tests_run`. |
| `fitness.test_pass_rate` | SHOULD | Float 0.0–1.0. `tests_passed / tests_run`. |
| `fitness.start_duration_ms` | SHOULD | Integer. Time for Prime to bind HTTP port. From `checks.start.duration_ms`. |
| `fitness.health_duration_ms` | SHOULD | Integer. Time for health check to succeed. From `checks.health.duration_ms`. |
| `fitness.total_duration_ms` | SHOULD | Integer. Sum of all stage durations. Wall-clock time from pipeline start to report. |
| `fitness.source_files` | SHOULD | Integer. Count of non-test, non-manifest files in the manifest's `files` array. |
| `fitness.source_lines` | SHOULD | Integer. Total line count of source files. |
| `fitness.test_files` | SHOULD | Integer. Count of test files (matching `test*` or `*test*` patterns in `files`). |
| `fitness.test_lines` | SHOULD | Integer. Total line count of test files. |
| `fitness.dependency_count` | SHOULD | Integer. Number of dependencies in `requirements.txt` (or equivalent). `0` for stdlib-only artifacts. |
| `fitness.token_input` | SHOULD | Integer. From manifest's `token-usage.input`. |
| `fitness.token_output` | SHOULD | Integer. From manifest's `token-usage.output`. |

**Data sources:** All fitness metrics are computed by the Test Rig from two sources: (1) the viability report's own `checks` data (durations, test counts), and (2) the manifest (file list, token usage). No additional test execution is needed — fitness extraction is a post-processing step after all pipeline stages complete.

**Fitness dimensions:**

- **Speed** — `build_duration_ms`, `test_duration_ms`, `start_duration_ms`, `health_duration_ms`, `total_duration_ms`. Faster organisms are more efficient to verify.
- **Correctness** — `test_count`, `test_pass_rate`. More tests with higher pass rates indicate more thorough self-verification.
- **Economy** — `token_input`, `token_output`, `source_lines`, `dependency_count`. Organisms that achieve viability with fewer tokens and less code are cheaper to reproduce.
- **Robustness** — `test_files`, `test_lines`, test-to-source ratio (`test_lines / source_lines`). Higher testing density correlates with fewer latent bugs.

**M1 usage:** Fitness data is stored in every generation record. Humans can query `GET /versions` and compare fitness across generations. No automated selection occurs.

**M2+ usage:** Selection policies will consume the fitness vector to choose between viable organisms. The specific selection criteria (e.g., minimize `total_duration_ms`, maximize `test_count`, Pareto-optimal across dimensions) are deferred to a future environment spec. The measurement apparatus is defined here so that historical data exists from Gen-1 onward.

### Generation Record

Maintained by the Supervisor in an append-only JSON file (`generations.json`).

**Schema:**

```json
{
  "generation": 1,
  "parent": 0,
  "spec-hash": "sha256:abc123...",
  "artifact-hash": "sha256:def456...",
  "outcome": "promoted",
  "artifact_ref": "gen-1",
  "created": "2026-03-21T14:30:00Z",
  "completed": "2026-03-21T14:32:30Z",
  "container-id": "lab-gen-1",
  "viability": {
    "status": "viable",
    "failure_stage": "none",
    "checks": { "..." : "..." }
  }
}
```

**Field rules:**

| Field | Required | Rule |
|-------|----------|------|
| `generation` | MUST | Integer >= 1. |
| `parent` | MUST | Integer >= 0. |
| `spec-hash` | MUST | SHA-256 hex digest. |
| `artifact-hash` | MUST | SHA-256 hex digest. |
| `outcome` | MUST | One of: `promoted`, `failed`, `timeout`, `in-progress`. |
| `artifact_ref` | MAY | String. Git ref (tag) pointing to the artifact source tree. `gen-N` for promoted, `gen-N-failed` for rolled back. Absent while `in-progress`. See BOOTSTRAP-SPEC-002 §2.7 for naming convention and retry suffixes. |
| `created` | MUST | ISO-8601 timestamp. |
| `completed` | MAY | ISO-8601 timestamp. Absent while `in-progress`. |
| `container-id` | MUST | String identifying the Test Rig container. |
| `viability` | MAY | The full viability report (including diagnostics when non-viable). Absent while `in-progress`. |

### Supervisor HTTP API

**Direction:** Prime → Supervisor. The Supervisor runs on the host. Prime calls these endpoints.

| Method | Path       | Request Body                          | Success Response                 | Error Response |
|--------|------------|---------------------------------------|----------------------------------|----------------|
| GET    | /          | —                                     | HTML dashboard                   | — |
| GET    | /stats     | —                                     | `{"generation": 1, "status": "idle", "uptime": 3600}` | — |
| GET    | /versions  | —                                     | `[Generation Record, ...]`       | — |
| POST   | /spawn     | `{"spec-hash": "...", "generation": 1, "artifact-path": "/path"}` | `{"ok": true, "container-id": "lab-gen-1", "generation": 1}` | `{"ok": false, "error": "..."}` |
| POST   | /promote   | `{"generation": 1}`                   | `{"ok": true, "generation": 1}`  | `{"ok": false, "error": "..."}` |
| POST   | /rollback  | `{"generation": 1}`                   | `{"ok": true, "generation": 1}`  | `{"ok": false, "error": "..."}` |

**Notes:**

- `POST /spawn` is asynchronous. Upon receiving the request, the Supervisor: (1) creates the `gen-N` git branch and commits the artifact files from `artifact-path`; (2) creates the Test Rig container; (3) returns `{"ok": true, "container-id": ..., "generation": N}` immediately; (4) runs the Test Rig as a background async task. Prime polls `GET /versions` until generation N has a terminal `outcome`.
- `artifact-path` in the spawn request MUST be an absolute path, or a path relative to `CAMBRIAN_WORKSPACE_ROOT`. The Supervisor resolves relative paths before performing Docker bind mounts (which require absolute paths).
- `artifact-hash` in the generation record is computed by the Supervisor from the manifest's `artifact-hash` field after reading the artifact at `artifact-path`. The Supervisor reads this from `manifest.json` — it does not recompute it independently in M1.
- `POST /promote` MUST: merge `gen-N` to `main`, create annotated tag `gen-N`, delete the branch, update the generation record.
- `POST /rollback` MUST: create annotated tag `gen-N-failed` (preserving the failed artifact for informed retry), delete branch `gen-N`, update the generation record to `outcome: failed` with `artifact_ref: "gen-N-failed"`.
- All POST endpoints MUST return `{"ok": false, "error": "..."}` on failure, never throw.

### Prime HTTP API

**Direction:** Test Rig → Prime. The Test Rig calls these endpoints to verify Prime is alive.

| Method | Path     | Success Response |
|--------|----------|------------------|
| GET    | /health  | `200 OK` (empty body or `{"ok": true}`) |
| GET    | /stats   | `{"generation": 1, "status": "idle", "uptime": 120}` |

**Notes:**

- `/health` MUST return 200 with no preconditions. It is a liveness check.
- `/stats` MUST return valid JSON. The `generation` field MUST match the artifact's generation number.

## Lifecycle

The generation lifecycle has four phases:

```
generate → verify → decide → record
```

### Generate

Prime reads the spec and calls an LLM to produce a complete codebase.

- **Consumes:** Spec file, generation history (previous generation records).
- **Produces:** An Artifact directory with `manifest.json`, source code, tests, and the spec file. Committed to a `gen-N` git branch.
- **Failure:** LLM returns unparseable output, or Prime cannot write files. Retryable.

### Verify

The Supervisor spawns a Test Rig container with the artifact mounted.

- **Consumes:** Artifact directory at `/workspace`.
- **Produces:** Viability Report at `/workspace/viability-report.json`.
- **Failure:** Container fails to start, or Test Rig crashes before writing report. Non-retryable (counts as non-viable).

### Decide

Prime reads the viability report and tells the Supervisor to promote or rollback.

- **Consumes:** Viability Report.
- **Produces:** A promote or rollback request to the Supervisor.
- **Failure:** Supervisor unreachable. Retryable with backoff.

### Record

The Supervisor executes the promote or rollback and writes the generation record.

- **Consumes:** Promote or rollback request.
- **Produces:** Updated generation history. On promote: merged branch, git tag. On rollback: discarded branch.
- **Failure:** Git operation fails. Non-retryable (manual intervention required).

## Failure Modes

| Failure | Trigger | Response | Retryable |
|---------|---------|----------|-----------|
| Unparseable LLM output | LLM returns malformed or incomplete code | Record failed attempt. Retry with fresh LLM call, up to `CAMBRIAN_MAX_RETRIES` times. | Yes |
| Build failure | `entry.build` exits non-zero | Test Rig records `failure_stage: build`, writes report, exits 1. Prime receives non-viable verdict. | Yes (new generation) |
| Test failure | `entry.test` exits non-zero | Test Rig records `failure_stage: test`, writes report, exits 1. | Yes (new generation) |
| Start timeout | Prime doesn't bind HTTP port within 30s | Test Rig records `failure_stage: start`, writes report, exits 1. | Yes (new generation) |
| Health check failure | `GET /health` returns non-200 or times out | Test Rig records `failure_stage: health`, writes report, exits 1. | Yes (new generation) |
| Supervisor unreachable | Network error on POST /spawn, /promote, /rollback | Exponential backoff (1s, 2s, 4s, 8s, 16s). Do not proceed without verification. | Yes |
| LLM rate-limited | HTTP 429 from LLM API | Respect `retry-after` header. Pause and retry. | Yes |
| All retries exhausted | `CAMBRIAN_MAX_RETRIES` reached for one generation | Record generation as failed. Stop. Do not modify the spec. | No |
| Container crash | Test Rig container exits without writing report | Supervisor records `outcome: failed`. No viability report attached. | Yes (new generation) |

Each retry is a separate generation record. Retries MUST NOT reuse previous LLM output — each is a fresh generation call. Retries SHOULD be informed: Prime reads the failed artifact's source code (via `artifact_ref` in the generation record) and structured diagnostics to construct a targeted LLM prompt. The LLM still produces a complete codebase (not a patch), preserving the clean-regeneration property. See BOOTSTRAP-SPEC-002 §2.7 for the informed retry data flow.

## Configuration

All configuration is via environment variables. Precedence: env var > default.

| Variable              | Required | Default          | Purpose |
|-----------------------|----------|------------------|---------|
| `ANTHROPIC_API_KEY`   | MUST     | —                | LLM API authentication |
| `CAMBRIAN_MODEL`      | MAY      | `claude-opus-4-6` | LLM model identifier for generation |
| `CAMBRIAN_MAX_GENS`   | MAY      | `5`              | Maximum generation attempts before stopping |
| `CAMBRIAN_MAX_RETRIES`| MAY      | `3`              | Maximum retries per generation on LLM output failure |
| `CAMBRIAN_TOKEN_BUDGET` | MAY    | `0`              | Maximum cumulative tokens. `0` means unlimited. |
| `CAMBRIAN_SUPERVISOR_URL` | MAY  | `http://localhost:8400` | Supervisor HTTP endpoint |
| `CAMBRIAN_SPEC_PATH`  | MAY      | `./spec/CAMBRIAN-SPEC-005.md` | Path to the genome spec file (CAMBRIAN-SPEC-005, not this system spec) |
| `CAMBRIAN_WORKSPACE_ROOT` | MAY  | `./workspace`    | Host-side directory bind-mounted into containers as `/workspace`. Supervisor resolves relative `artifact-path` values in spawn requests against this directory. |

## Container Requirements

Prime and all spawned containers (Test Rig, offspring) share the same container configuration requirements.

### Networking

All containers MUST have:
- Outbound HTTPS access (port 443) to LLM API endpoints — required for code generation
- HTTP access to the Supervisor on the host network (typically `http://host:8400`)
- DNS resolution for API hostnames

Without outbound network access, Prime cannot call an LLM and therefore cannot reproduce. The Supervisor is responsible for configuring container networking at spawn time. Lab containers (running the Test Rig or offspring Prime) inherit the same network configuration.

### Credential Injection

Offspring generations require API credentials to call an LLM. Credentials are an environment concern — the Supervisor injects them, the organism consumes them.

Rules:
- The Supervisor MUST pass `ANTHROPIC_API_KEY` to every spawned container via environment variables
- Credentials MUST NOT be embedded in artifacts, committed to git, or written to `manifest.json`
- The Supervisor reads credentials from its own environment and forwards them at container creation time
- Prime reads `ANTHROPIC_API_KEY` from its environment; it MUST NOT attempt to discover, generate, or persist credentials

### Virtual Environment

Every container has a pre-created virtual environment at `/venv`, activated via `PATH="/venv/bin:$PATH"` in the Docker image. All `entry.build` commands (e.g., `pip install -r requirements.txt`) install into `/venv`, never the system Python. Generated artifacts MUST NOT create their own venvs — they inherit the container's `/venv`.

### Workspace

Every container mounts the artifact at `/workspace`:
- `/workspace/manifest.json` — the fixed-point contract
- `/workspace/spec/` — the spec file(s)
- `/workspace/src/` — source code (layout varies by language)
- `/workspace/tests/` — test suite

The working directory for all `entry.*` commands is `/workspace`.

## Minimal Spec (M1 operationality test)

The Minimal Spec is a small specification used to verify that a reproduced Prime is operational — that it can read a spec, call an LLM, and produce viable code — without requiring full self-reproduction.

This is the termination criterion for the M1 acceptance test. Gen-2 (the first self-hosted offspring) generates code from this spec instead of the full Prime spec.

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

Produce a manifest.json conforming to the Cambrian artifact manifest contract.
```

### Why This Spec

- **Small enough** to be cheap (few hundred tokens of LLM output)
- **Exercises the full pipeline**: spec → LLM → code → build → test → start → health check
- **Uses the same Test Rig**: the artifact includes a valid `manifest.json`, so the standard verification pipeline works unchanged
- **Proves operationality**: Prime can read a spec, call an LLM, write files, create a manifest, and request verification
- **Does not recurse**: the echo server is not a Prime, so the chain terminates

## Examples

### Example 1: Happy path — successful generation and promotion

```
1. Prime starts in container. Reads spec from /workspace/spec/CAMBRIAN-SPEC-005.md.
2. Prime loads generation history from Supervisor (GET /versions → empty list).
3. Prime calls LLM:
   - System: "You are a code generator. Produce a complete, working codebase
     from the following specification."
   - User: [full spec content]
4. LLM returns 8 files: src/prime.py, src/api.py, src/generate.py,
   tests/test_api.py, tests/test_generate.py, requirements.txt,
   manifest.json, spec/CAMBRIAN-SPEC-005.md.
5. Prime writes files to /workspace/gen-1/ (a directory within the mounted workspace).
6. Prime computes spec-hash and artifact-hash, writes manifest.json.
   Note: Prime MUST NOT perform git operations. Git is Supervisor-managed infrastructure.
7. Prime calls Supervisor: POST /spawn {"spec-hash": "sha256:abc...", "generation": 1,
   "artifact-path": "/workspace/gen-1"}.
   The artifact-path MUST be an absolute path accessible on the host filesystem.
   The Supervisor maps container paths to host paths via CAMBRIAN_WORKSPACE_ROOT.
8. Supervisor creates gen-1 branch, commits artifact files from artifact-path,
   then creates container, mounts artifact at /workspace, starts Test Rig.
9. Test Rig:
    - Reads manifest.json → entry.build = "pip install -r requirements.txt" → exit 0
    - entry.test = "python -m pytest tests/" → 15 tests, 15 passed → exit 0
    - entry.start = "python src/prime.py" → port 8401 bound in 1.2s
    - GET http://localhost:8401/health → 200
    - GET http://localhost:8401/stats → {"generation": 1, "status": "idle", "uptime": 2}
    - Writes viability-report.json: status=viable
    - Exits 0.
10. Supervisor reads report, updates generation record, signals outcome to waiting Prime.
11. Prime polls GET /versions until generation 1 has terminal outcome. Sees status=viable.
12. Prime calls POST /promote {"generation": 1}.
13. Supervisor: git merge gen-1, git tag -a gen-1, deletes branch, records outcome=promoted.
```

### Example 2: Build failure — non-viable, rollback, retry

```
1. Prime starts generation 2. Calls LLM.
2. LLM produces code with a syntax error in src/api.py (missing import).
3. Prime writes files to /workspace/gen-2/.
4. Prime calls POST /spawn.
5. Test Rig:
   - Reads manifest.json
   - entry.build = "pip install -r requirements.txt" → exit 0
   - entry.test = "python -m pytest tests/" → ImportError in test_api.py → exit 1
   - Records failure_stage=test, tests_run=15, tests_passed=8
   - Writes viability-report.json: status=non-viable
   - Exits 1.
6. Supervisor reads report, returns to Prime.
7. Prime sees status=non-viable. Calls POST /rollback {"generation": 2}.
8. Supervisor: tags gen-2-failed, deletes gen-2 branch, records outcome=failed
   with artifact_ref="gen-2-failed".
9. Prime has retries remaining (CAMBRIAN_MAX_RETRIES=3, attempt 1 of 3).
10. Prime reads failed source code from gen-2-failed tag and diagnostics from
    generation record. Constructs informed retry prompt with spec + failed code
    + structured diagnostics.
11. LLM produces corrected complete codebase. Prime writes to /workspace/gen-3/.
12. Test Rig passes. Prime promotes gen-3.
```

### Example 3: M1 acceptance — Gen-2 runs the Minimal Spec

```
1. Gen-1 Prime (bootstrap-produced) is running. It has been promoted.
2. Gen-1 reads the full spec, calls LLM, produces Gen-2 artifact.
3. Gen-2 passes the Test Rig → promoted.
4. Gen-2 Prime is started in a new container.
   - Supervisor injects ANTHROPIC_API_KEY via env var.
   - Container has outbound HTTPS access.
5. Gen-2 is given the Minimal Spec (echo server) instead of the full Prime spec.
6. Gen-2 calls LLM with the Minimal Spec.
7. LLM returns: server.py, test_server.py, requirements.txt, manifest.json.
8. Gen-2 writes files, computes hashes, commits to gen-3 branch.
9. Gen-2 calls POST /spawn.
10. Test Rig:
    - entry.build = "pip install -r requirements.txt" → exit 0
    - entry.test = "python -m pytest test_server.py" → 3 tests, 3 passed → exit 0
    - entry.start = "python server.py" → port 8401 bound in 0.3s
    - GET http://localhost:8401/health → 200, {"ok": true}
    - Writes viability-report.json: status=viable
    - Exits 0.
11. Gen-2 promotes gen-3.
12. M1 is complete: Gen-1 reproduced (Gen-2), Gen-2 is operational (produced
    viable code from the Minimal Spec). The chain terminates.
```

## Implementation Phases

### Phase 0: Supervisor and Test Rig

Build the environment infrastructure. No Prime yet.

**Adds:**
- Supervisor HTTP server with `/spawn`, `/promote`, `/rollback`, `/stats`, `/versions` endpoints
- Generation history file (`generations.json`), append-only
- Test Rig script that reads `manifest.json` and executes the pipeline
- Container lifecycle management (create, start, stop, destroy)
- Container networking (outbound HTTPS for LLM API access)
- Credential injection (`ANTHROPIC_API_KEY` forwarded to spawned containers)
- Git operations (branch, merge, tag, delete)

**Done when:** A hand-crafted artifact with a valid `manifest.json` can be spawned, tested, promoted, and rolled back through the Supervisor API. The Test Rig writes a correct viability report. Spawned containers have outbound HTTPS access and receive API credentials via environment variables.

### Phase 1: Bootstrap Prime

Generate the first Prime using Claude Code (interactive, one-time).

**Adds:**
- Prime source code generated from this spec
- HTTP server with `/health` and `/stats`
- Core loop: read spec → call LLM → write artifact → request verification → promote/rollback
- Manifest generation (spec-hash, artifact-hash computation)
- Generation history consumption (reads from Supervisor)

**Done when:** The generated Prime starts, passes the Test Rig, and is promoted as gen-1.

### Phase 2: Self-Hosting

The promoted Prime reproduces itself and proves operationality.

**Adds:** Nothing new — this phase validates that Phase 1 produced a Prime capable of reproduction.

**Requires:**
- Container networking (outbound HTTPS for LLM API calls)
- Credential injection (Supervisor passes `ANTHROPIC_API_KEY` to spawned containers)

**Done when:**
1. Gen-1 Prime reads the full spec, calls an LLM, produces Gen-2.
2. Gen-2 passes the Test Rig and is promoted.
3. Gen-2 Prime is given the Minimal Spec (echo server).
4. Gen-2 produces Gen-3 (echo server artifact) from the Minimal Spec.
5. Gen-3 passes the Test Rig.

This is the M1 acceptance criterion. The chain terminates at Gen-3 because the Minimal Spec does not produce a Prime.

## Validation

### Mechanical checks (test suite)

- `GET /health` returns 200
- `GET /stats` returns valid JSON with `generation`, `status`, `uptime` fields
- `manifest.json` conforms to schema: all MUST fields present, types correct
- `manifest.json` `spec-hash` matches SHA-256 of the spec file on disk
- `manifest.json` `artifact-hash` matches SHA-256 of all files except `manifest.json`
- `POST /spawn` with valid artifact returns `{"ok": true, ...}`
- `POST /spawn` with invalid artifact-path returns `{"ok": false, "error": "..."}`
- `POST /promote` after viable generation merges branch and creates tag
- `POST /rollback` after non-viable generation deletes branch
- Generation history contains one record per attempt, outcomes correct
- Test Rig writes valid `viability-report.json` for both viable and non-viable artifacts
- Test Rig exits 0 for viable, 1 for non-viable
- Test Rig fail-fast: if build fails, subsequent checks show `passed: false, duration_ms: 0`
- Retry logic: failed generation triggers new LLM call, not reuse of old output
- Retry limit: after `CAMBRIAN_MAX_RETRIES`, Prime stops and records failure

### Behavioral checks (code review)

- Error messages from Supervisor include enough context to diagnose the failure
- LLM prompt includes the spec in full and generation history
- Artifact directory is clean — no leftover files from previous generations
- Git operations are atomic — no partial merges or orphaned branches
- Config validation fails fast with an actionable error message if `ANTHROPIC_API_KEY` is missing
- Prime does not self-assess viability — it always defers to the Test Rig verdict
- Credentials are never written to artifacts, manifests, or git history
- Spawned containers have outbound HTTPS access and receive `ANTHROPIC_API_KEY`

### Reproductive check (M1 acceptance)

```
Bootstrap → Gen-1 (full Prime, full spec)
Gen-1    → Gen-2 (full Prime, full spec)
Gen-2    → Gen-3 (echo server, Minimal Spec)  ← chain terminates here
```

1. Gen-1 Prime (bootstrap-produced) passes the Test Rig
2. Gen-1 Prime generates Gen-2 from the full spec
3. Gen-2 passes the Test Rig — it is a fully functional Prime
4. Gen-2 Prime generates Gen-3 from the **Minimal Spec** (echo server)
5. Gen-3 passes the Test Rig — it is a viable echo server

**Termination:** Gen-3 is not a Prime. It cannot reproduce. The chain stops. M1 is proven because:
- Reproduction works (Gen-1 → Gen-2, both full Primes)
- The reproduced Prime is operational (Gen-2 can read a spec, call an LLM, produce viable code)
- The process terminates naturally (Minimal Spec produces a non-Prime artifact)

## References

- [CAMBRIAN-SPEC-003](CAMBRIAN-SPEC-003.md) — Previous iteration (style-guide rewrite)
- [CAMBRIAN-SPEC-002](CAMBRIAN-SPEC-002.md) — First language-agnostic draft
- [CAMBRIAN-SPEC-001](CAMBRIAN-SPEC-001.md) — Original spec carried from Loom (ClojureScript-specific, historical)
- [Loom v0.2.0](https://github.com/lispmeister/loom) — Predecessor project, source-code-level evolution (archived)
- [Loom final retrospective](https://github.com/lispmeister/loom/blob/master/architecture-reviews/review-2026-03-20-001.md) — Lessons learned from 72 generations of code-level evolution
- [SPEC-STYLE-GUIDE](SPEC-STYLE-GUIDE.md) — Style guide used to write this spec

---

## Appendix: Environment vs. Genome

This spec is structured as a single document following the standard spec style guide. However, the content serves two audiences:

**Environment (Supervisor, Test Rig, Bootstrap):** Sections on contracts, lifecycle, configuration, and implementation phases define what the infrastructure does. This is built by humans (Phase 0) and persists across generations. The Supervisor and Test Rig are NOT part of the organism.

**Genome (Prime):** The overview, model, contracts, lifecycle, and acceptance criteria together define what Prime is. An LLM reading these sections — particularly the model, contracts, Prime HTTP API, and the examples — produces a working Prime. The spec file is included in every artifact so the organism carries its own genome.

**Fitness** is an environment concern, not part of the genome. For M1, fitness has two levels:
1. **Viability** — binary. The Test Rig passes or it doesn't. Required for every generation.
2. **Reproductive fitness** — can the organism reproduce? Gen-1 must produce a viable Gen-2 (full Prime). Gen-2 must produce a viable Gen-3 (Minimal Spec echo server). This is the M1 acceptance criterion.

When selection *between* viable organisms becomes relevant (M2+), comparative fitness criteria will be defined in a separate environment spec — measured by the environment, not self-reported by the organism.

---

## Implementation Language

**Python version:** Python 3.14 free-threaded build (`python3.14t`). The free-threaded build disables the GIL (PEP 779, officially supported as of 3.14), enabling true multithreading. Docker containers use the `python:3.14t-slim` base image. All Python code MUST run inside virtual environments — host-side (`.venv/`) for the Supervisor and Test Rig, container-side (`/venv`) for artifacts.

**Type safety:** All Python code MUST be fully type-annotated. Pyright in strict mode enforced in CI — type errors fail the build. Migration path: switch to `ty` (Astral, Rust-based, 10-60x faster) when its Pydantic plugin ships. Pydantic v2 for all I/O boundary validation (manifest parsing, HTTP bodies, viability reports, generation records). Prefer `Protocol`, `TypedDict`, `Literal`, `Self` over loose types. `Any` is prohibited — use `object` and narrow with `isinstance`.

**Introspection:** All components MUST be built for runtime introspection and live debugging. Use `inspect`, `typing-inspect`, `devtools`, and `rich` for call-stack inspection, type introspection, and pretty-printing of complex objects. Generation failures MUST log full call stack and object state, not just error messages.

**Logging:** All components MUST use `structlog` for structured logging. JSON output in production (containers), key-value output in development (host). Every log line includes `timestamp`, `level`, `event`, and `component`. Generation-scoped operations include `generation` in the log context.

**Concurrency:** All I/O-bound code MUST use `asyncio`. Prime uses `asyncio` for concurrent LLM calls (e.g., generating subsystems in parallel). The Supervisor uses `asyncio` for its HTTP server and container lifecycle management. Combined with free-threaded Python, this provides cooperative concurrency for I/O and true parallelism for CPU work. The Test Rig is sequential and does not require asyncio.

**HTTP:** `aiohttp` for async HTTP servers (Supervisor) and clients (Prime → Supervisor, health checks). Native asyncio integration.

**Docker management:** `aiodocker` for async container lifecycle. Container stdout/stderr MUST be captured and attached to generation records for post-mortem debugging.

**Project tooling:** `uv` for package management, venv creation, and dependency locking (`uv.lock`). `ruff` for linting and formatting. `pytest` + `pytest-asyncio` for testing.

**Environment (Supervisor, Test Rig):** Python. These are permanent infrastructure, not generated by an LLM.

**Organism (Prime) — M1:** Python. Chosen to maximize the probability of viable offspring. LLM code generation benchmarks show Python at the top across all major models (93%+ on LeetCode-style tasks). Sharing the language with the environment stack eliminates toolchain complexity during bootstrap. The spec remains language-agnostic by design — a future generation MAY produce Prime in a different language if the spec permits it.

**Containers:** Docker. Chosen for cross-platform compatibility (macOS and Linux).

---

```yaml
spec-version: "004"
organism: "cambrian"
lineage: "genesis"
parent-spec: "CAMBRIAN-SPEC-003"
language: "python 3.14t (M1)"
```

---

*This spec is the genotype. The code an LLM generates from it is the phenotype.
Different LLMs will produce different organisms. Natural selection begins at birth:
if it doesn't pass its own acceptance criteria, it was never alive.*
