# CAMBRIAN-SPEC-002

**Version:** 002 | **Ancestor:** CAMBRIAN-SPEC-001 | **Lineage:** genesis | **Organism:** Cambrian

> This document is the genome. An LLM reading Part B produces a working
> code factory that can reproduce itself. Part A is scaffolding for bootstrap.

---

## Part A — Scaffold (temporary)

Part A exists to birth the first Prime. It will be shed once Prime is self-hosted.

### A.1 Supervisor

A host-side process that manages container lifecycle. Not part of the organism.

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

### A.2 Bootstrap

The bootstrap is a minimal agentic loop that:

1. Reads this spec (Part B)
2. Calls an LLM with the spec as context
3. Instructs the LLM to generate a complete Prime codebase
4. Writes the output to a workspace directory
5. Hands the workspace to the Supervisor for viability testing

The bootstrap is disposable. Once a viable Prime exists, Prime replaces the bootstrap's role.

### A.3 Test Rig

A fixed verification pipeline that runs inside a Lab container. No LLM, no agentic loop.

**Pipeline:**

```
build → unit-test → start → health-check → report
```

1. **Build** — Compile/install the artifact. Fail if build errors.
2. **Unit test** — Run the test suite. Record: tests run, passed, failed.
3. **Start** — Launch Prime. Fail if it doesn't bind its HTTP port within 30s.
4. **Health check** — `GET /health` returns 200. `GET /stats` returns valid JSON.
5. **Report** — Write structured viability report to `/workspace/viability-report.json`. Exit 0 if viable, exit 1 if non-viable. Supervisor reads the file after the container exits.

**Viability Report** (`/workspace/viability-report.json`):

```json
{
  "generation": 1,
  "status": "viable|non-viable",
  "failure_stage": "none|build|test|start|health",
  "checks": {
    "build": {"passed": true, "duration_ms": 0},
    "test": {"passed": true, "tests_run": 0, "tests_passed": 0, "duration_ms": 0},
    "start": {"passed": true, "duration_ms": 0},
    "health": {"passed": true, "duration_ms": 0}
  },
  "completed_at": "ISO-8601"
}
```

The test rig is the invariant across generations. It does not change when the organism changes.

### A.4 Self-Hosting Stages

```
Stage 0: Human writes bootstrap + test rig + supervisor
Stage 1: Bootstrap reads spec → generates Prime (first organism)
Stage 2: Prime reads spec → generates Prime (self-hosted)
Accept:  Stage 2 output passes the same test rig as Stage 1
```

After Stage 2 succeeds, the bootstrap is no longer needed. Prime reproduces by reading its own spec and calling an LLM.

---

## Part B — Prime Spec (the genome)

Everything below defines what Prime is. An LLM reading this section produces a working Prime.

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
2. **Generate** — Call an LLM with the spec as context. The LLM produces a complete codebase. Prime writes it to a workspace directory and commits to a `gen-N` branch.
3. **Verify** — Hand the artifact to the Supervisor, which spawns a Lab container running the test rig. Wait for the viability report.
4. **Promote or rollback** — If viable: tell Supervisor to promote (tag, record). If not: tell Supervisor to rollback (discard, record).

### B.3 Generation

Prime generates code by sending the spec to an LLM with instructions to produce a complete, self-contained codebase. The generation prompt includes:

- The full spec (this document)
- Generation history (what worked, what failed, why)
- The current fitness record

The LLM output is a set of files that constitute a complete Prime organism. Prime writes these files to a fresh workspace, runs any build steps, and packages the result as an artifact.

**Artifact:** A directory containing everything needed to run Prime — source code, build output, the spec, and a manifest.

**Manifest:**

```json
{
  "generation": 1,
  "spec-hash": "sha256:...",
  "artifact-hash": "sha256:...",
  "producer-model": "...",
  "token-usage": {"input": 0, "output": 0},
  "files": ["..."],
  "created_at": "ISO-8601"
}
```

### B.4 HTTP API

Prime exposes a minimal HTTP interface for observation and interaction.

| Method | Path     | Response                              |
|--------|----------|---------------------------------------|
| GET    | /health  | `200 OK`                              |
| GET    | /stats   | `{generation, status, tokens, uptime}`|
| POST   | /chat    | SSE stream (user interaction)         |

### B.5 Fitness

A generation is viable if the test rig passes. Among viable generations, fitness is measured by:

| Metric | Why |
|--------|-----|
| Test pass rate | More tests passing = more verified behavior |
| Birth cost (tokens) | Lower cost = more offspring affordable |
| Startup time | Faster = healthier |

**Fitness score** (v0): `(tests_passed × 10) − (birth_tokens / 1000) − (startup_ms / 100)`

The fitness function will evolve. What matters now is that it exists and is recorded.

**Safety invariant:** Test count must not decrease from parent generation.

### B.6 Audit Trail

All records are append-only. Never overwritten, never deleted.

- **Generation history** — one record per generation attempt (JSON, one file)
- **Fitness log** — one record per viable generation (JSONL, append-only)

### B.7 Container Model

Prime runs inside a container. The container provides:

- Filesystem isolation
- Network access (for LLM API calls and Supervisor communication)
- A mounted workspace at `/workspace` containing source + spec
- Environment variables for configuration

Prime communicates with the Supervisor over HTTP. The Supervisor runs on the host.

### B.8 Configuration

| Variable              | Required | Default | Purpose              |
|-----------------------|----------|---------|----------------------|
| `ANTHROPIC_API_KEY`   | Yes      | —       | LLM access           |
| `CAMBRIAN_MODEL`      | No       | *best available* | Generation model |
| `CAMBRIAN_MAX_GENS`   | No       | 5       | Generation cap       |
| `CAMBRIAN_TOKEN_BUDGET` | No    | 0       | Token cap (0=none)   |
| `CAMBRIAN_SUPERVISOR_URL` | No  | `http://host:8400` | Supervisor endpoint |

### B.9 Design Laws

1. **Containers are the keep/revert boundary.** Promote = tag + record. Revert = destroy + discard.
2. **The spec is sacred.** Prime reads it, never modifies it. Spec mutation is a future concern.
3. **Two-stage verification.** Mechanical tests (test rig) AND viability checks. Both must pass.
4. **Append-only audit trail.** Every generation attempt is recorded. Nothing is overwritten.
5. **Reproduce, then improve.** M1 is reproduction. Self-modification comes later.

### B.10 Acceptance Criteria (M1)

A valid Prime produced from this spec must:

1. Start and bind its HTTP port
2. Respond to `GET /health` with 200
3. Respond to `GET /stats` with valid JSON including generation number
4. Accept `POST /chat` for human interaction
5. Read this spec from its local filesystem
6. Call an LLM to generate a complete codebase from the spec
7. Package the output as an artifact with manifest
8. Request the Supervisor to spawn a Lab for verification
9. Receive the viability report and decide promote/rollback
10. **The generated organism passes criteria 1–9**

Criterion 10 is the reproductive test. If the offspring can reproduce, M1 is complete.

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
