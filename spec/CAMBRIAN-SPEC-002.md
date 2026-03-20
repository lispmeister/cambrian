# CAMBRIAN-SPEC-002

**Version:** 002 | **Ancestor:** CAMBRIAN-SPEC-001 | **Lineage:** genesis | **Organism:** Cambrian

> This document has two audiences.
> **Part A** is for humans building the environment — the infrastructure that births and tests organisms.
> **Part B** is the genome — an LLM reading it produces a working code factory that can reproduce itself.

---

## Fixed-Point Contract: Artifact Manifest

The manifest is the interface between organism and environment. Both the test rig and Prime conform to it. It does not change across generations.

Every artifact must include a `manifest.json` at its root:

```json
{
  "cambrian-version": 1,
  "generation": 1,
  "parent-generation": 0,
  "spec-hash": "sha256:...",
  "artifact-hash": "sha256:...",
  "producer-model": "...",
  "token-usage": {"input": 0, "output": 0},
  "files": ["..."],
  "created_at": "ISO-8601",
  "entry": {
    "build": "...",
    "test": "...",
    "start": "...",
    "health": "http://localhost:8401/health"
  }
}
```

**Fields:**

| Field | Purpose |
|-------|---------|
| `cambrian-version` | Manifest schema version. Currently `1`. |
| `generation` | Generation number of this artifact. |
| `parent-generation` | Generation that produced this artifact. `0` for bootstrap. |
| `spec-hash` | SHA-256 of the spec used to generate this artifact. |
| `artifact-hash` | SHA-256 of the artifact contents (excluding manifest). |
| `producer-model` | LLM model ID that generated the code. |
| `token-usage` | Input and output tokens consumed during generation. |
| `files` | List of all files in the artifact. |
| `created_at` | ISO-8601 timestamp. |
| `entry.build` | Shell command to build the artifact. |
| `entry.test` | Shell command to run the test suite. |
| `entry.start` | Shell command to start Prime. |
| `entry.health` | URL the test rig hits to verify Prime is alive. |

The test rig reads `entry` to know how to build, test, and start the organism. This decouples the test rig from the implementation language.

---

## Part A — Environment

Part A is for humans. It defines the infrastructure that persists across generations: the Supervisor, the test rig, and the one-time bootstrap process.

### A.1 Supervisor

A host-side process that manages container lifecycle and generation history. Not part of the organism. Persists across generations.

**Responsibilities:**
- Create, start, stop, destroy containers
- Maintain generation history (append-only JSON)
- Expose HTTP API for spawn/promote/rollback
- Serve a dashboard for human observation

**HTTP API:**

| Method | Path       | Body                          | Response                       |
|--------|------------|-------------------------------|--------------------------------|
| GET    | /          | —                             | HTML dashboard                 |
| GET    | /stats     | —                             | `{generation, status, uptime}` |
| GET    | /versions  | —                             | `[Generation...]`              |
| POST   | /spawn     | `{spec-hash, generation, artifact-path}` | `{ok, container-id, generation}` |
| POST   | /promote   | `{generation}`                | `{ok, generation}`             |
| POST   | /rollback  | `{generation}`                | `{ok, generation}`             |

**Generation Record:**

```json
{
  "generation": 1,
  "parent": 0,
  "spec-hash": "sha256:...",
  "artifact-hash": "sha256:...",
  "outcome": "promoted|failed|timeout|in-progress",
  "created": "ISO-8601",
  "completed": "ISO-8601",
  "container-id": "...",
  "viability": {}
}
```

**Version control:** Git. Generation artifacts are committed to `gen-N` branches. Promotion merges to `main` and creates an annotated tag `gen-N`. Rollback discards the branch. Tags provide immutable, retrievable history.

### A.2 Bootstrap

The bootstrap is a one-time process run interactively via Claude Code. A human and an LLM collaborate to produce the first viable artifact. There is no separate bootstrap system to build.

```
Stage 0: Human + Claude Code build the supervisor and test rig
Stage 1: Human + Claude Code generate the first Prime from this spec
Stage 2: The generated Prime reads this spec and generates another Prime
Accept:  Stage 2 output passes the same test rig as Stage 1
```

After Stage 2 succeeds, Prime is self-hosted. It reproduces by reading its own spec and calling an LLM.

### A.3 Test Rig

A fixed verification pipeline. No LLM, no agentic loop. Persists across generations as the invariant viability check.

The test rig reads `manifest.json` from the artifact to discover build, test, and start commands.

**Pipeline:**

```
read-manifest → build → unit-test → start → health-check → report
```

1. **Read manifest** — Parse `/workspace/manifest.json`. Fail if missing or malformed.
2. **Build** — Run `entry.build`. Fail if non-zero exit.
3. **Unit test** — Run `entry.test`. Record: tests run, passed, failed.
4. **Start** — Run `entry.start`. Fail if Prime doesn't bind its HTTP port within 30s.
5. **Health check** — `GET entry.health` returns 200. `GET /stats` returns valid JSON.
6. **Report** — Write structured viability report to `/workspace/viability-report.json`. Exit 0 if viable, exit 1 if non-viable. Supervisor reads the file after the container exits.

**Viability Report** (`/workspace/viability-report.json`):

```json
{
  "generation": 1,
  "status": "viable|non-viable",
  "failure_stage": "none|manifest|build|test|start|health",
  "checks": {
    "manifest": {"passed": true},
    "build": {"passed": true, "duration_ms": 0},
    "test": {"passed": true, "tests_run": 0, "tests_passed": 0, "duration_ms": 0},
    "start": {"passed": true, "duration_ms": 0},
    "health": {"passed": true, "duration_ms": 0}
  },
  "completed_at": "ISO-8601"
}
```

The test rig does not change when the organism changes.

### A.4 Fitness (environment-owned, M2+)

Fitness is an environment concern, not part of the genome. For M1, viability is binary: the test rig passes or it doesn't.

When selection between viable organisms becomes relevant (M2+), fitness will be defined here — measured by the environment, not self-reported by the organism. Candidate metrics: test coverage, birth cost (tokens), startup time, task benchmark performance.

---

## Part B — Prime Spec (the genome)

Everything below defines what Prime is. An LLM reading this section — together with the fixed-point manifest contract above — produces a working Prime.

### B.1 What It Is

A code factory. Prime reads a specification, calls an LLM, and produces a complete working codebase — including a new Prime capable of doing the same thing. Three things live inside every running Prime container:

1. **Executing code** — the running process
2. **Source code** — the git repository that produced (1)
3. **Spec** — this document, which produced (2)

Prime can inspect all three at runtime.

### B.2 Core Loop

```
read-spec → generate → verify → promote|rollback → repeat
```

1. **Read spec** — Load this document (or a mutated descendant) from the local filesystem.
2. **Generate** — Call an LLM with the spec as context. The LLM produces a complete codebase. Prime writes it to a fresh workspace, creates a `manifest.json`, and commits to a `gen-N` git branch.
3. **Verify** — Hand the artifact to the Supervisor, which spawns a container running the test rig. Wait for the viability report.
4. **Promote or rollback** — If viable: tell Supervisor to promote (merge to main, tag, record). If not: tell Supervisor to rollback (discard branch, record).

### B.3 Generation

Prime generates code by sending the spec to an LLM with instructions to produce a complete, self-contained codebase. The generation prompt includes:

- The full spec (this document)
- Generation history (what worked, what failed, why)

The LLM output is a set of files that constitute a complete Prime organism. Prime writes these files to a fresh workspace and creates a `manifest.json` conforming to the fixed-point contract.

**Minimum artifact contents:**

- Source code sufficient to build and run Prime
- A test suite (minimum: tests for the core loop and HTTP endpoints)
- The spec file (the organism carries its own genome)
- `manifest.json` (conforming to the fixed-point contract)

### B.4 HTTP API

Prime exposes a minimal HTTP interface for observation by the test rig and Supervisor.

| Method | Path     | Response                              |
|--------|----------|---------------------------------------|
| GET    | /health  | `200 OK`                              |
| GET    | /stats   | `{generation, status, tokens, uptime}`|

### B.5 Failure Modes

Generation may fail. Prime handles failure without modifying the spec (spec mutation is M2+).

| Failure | Response |
|---------|----------|
| LLM produces unparseable output | Record failed attempt. Retry up to 3 times with same prompt. |
| Artifact builds but tests fail | Record as non-viable. Retry generation (new LLM call, not same output). |
| Supervisor unreachable | Wait and retry with exponential backoff. Do not proceed without verification. |
| LLM API rate-limited | Respect `retry-after` header. Pause and retry. |
| All retries exhausted | Record generation as failed. Stop. Do not modify the spec. |

Each attempt is a separate generation record in the audit trail. Retries do not reuse previous LLM output — each is a fresh generation call.

### B.6 Audit Trail

All records are append-only. Never overwritten, never deleted.

- **Generation history** — one record per generation attempt (JSON, one file)

### B.7 Container Model

Prime runs inside a container. The container provides:

- Filesystem isolation
- Network access (for LLM API calls and Supervisor communication)
- A mounted workspace at `/workspace` containing source, spec, and manifest
- Environment variables for configuration

Prime communicates with the Supervisor over HTTP. The Supervisor runs on the host.

### B.8 Configuration

| Variable              | Required | Default | Purpose              |
|-----------------------|----------|---------|----------------------|
| `ANTHROPIC_API_KEY`   | Yes      | —       | LLM access           |
| `CAMBRIAN_MODEL`      | No       | *best available* | Generation model |
| `CAMBRIAN_MAX_GENS`   | No       | 5       | Generation cap       |
| `CAMBRIAN_MAX_RETRIES`| No       | 3       | Retries per generation |
| `CAMBRIAN_TOKEN_BUDGET` | No    | 0       | Token cap (0=none)   |
| `CAMBRIAN_SUPERVISOR_URL` | No  | `http://host:8400` | Supervisor endpoint |

### B.9 Design Laws

1. **Containers are the keep/revert boundary.** Promote = merge + tag. Revert = destroy + discard.
2. **The spec is sacred.** Prime reads it, never modifies it. Spec mutation is a future concern.
3. **Git is the version system.** Every generation is a branch. Every promotion is a tag. History is immutable.
4. **Append-only audit trail.** Every generation attempt is recorded. Nothing is overwritten.
5. **Reproduce, then improve.** M1 is reproduction. Self-modification comes later.

### B.10 Acceptance Criteria (M1)

A valid Prime produced from this spec must:

1. Start and bind its HTTP port
2. Respond to `GET /health` with 200
3. Respond to `GET /stats` with valid JSON including generation number
4. Read this spec from its local filesystem
5. Call an LLM to generate a complete codebase from the spec
6. Package the output as an artifact with a valid `manifest.json`
7. Request the Supervisor to spawn a container for verification
8. Receive the viability report and decide promote/rollback
9. **The generated organism passes criteria 1–8**

Criterion 9 is the reproductive test. If the offspring can reproduce, M1 is complete.

---

## Implementation Language

The spec is language-agnostic. The implementation language is not prescribed. We will evaluate candidates based on how well LLMs can generate correct, idiomatic code for the full Prime codebase in a single generation pass.

Current candidate list: **Rust, Elixir, Python, Mojo**.

---

## Spec Metadata

```yaml
spec-version: "002"
organism: "cambrian"
lineage: "genesis"
parent-spec: "CAMBRIAN-SPEC-001"
language: "TBD"
```

---

*This spec is the genotype. The code an LLM generates from it is the phenotype.
Different LLMs will produce different organisms. Natural selection begins at birth:
if it doesn't pass its own acceptance criteria, it was never alive.*
