# Cambrian

A self-reproducing code factory. Cambrian reads a specification, calls an LLM, and produces a complete working codebase — including a new instance of itself capable of doing the same thing.

<p align="center">
  <img src="docs/images/cambrian-sea-monster.png" alt="Anomalocaris — apex predator of the Cambrian explosion" width="600">
</p>

## Status

**M1 complete. Pre-M2 quality hardening done. Anti-cheating verification layers specced.**

What's done:
- Phase 0: Supervisor, Test Rig, Docker image, gen-0 validated end-to-end
- M1: Gen-1 ran 44 minutes, 10 total generations, 5 promoted (gen-4, gen-6, gen-8, gen-9, gen-10), all at 100% test pass rate. 474,834 cumulative tokens.
- Pre-M2 hardening: 16-bead code review (specs, Python, Docker). Path traversal fix, field naming unification, Docker non-root user, 87 integration tests across 3 test files. 178 total tests, all passing.
- Verification layers: Three-layer anti-cheating model specced — FROZEN acceptance vectors (M1), dual-blind LLM examiner (M2), adversarial red-team (M2). Prevents generations from gaming their own fitness.

Next: Implement spec vector evaluation in Test Rig, then M2 — container isolation, campaign runner, fitness vectors, spec mutation. See `research/06-proposal.md`.

## The Idea

Code is disposable. The specification is the genome.

An LLM-powered organism ("Prime") reads a spec and regenerates the entire codebase from scratch each generation. No diffs, no accumulated cruft, no path dependence. A mechanical test rig — no LLM involved — decides if the result is viable. If it is, the offspring replaces the parent. If not, it's discarded.

This came from [Loom](https://github.com/lispmeister/loom), which tried source-code-level self-modification in ClojureScript. Loom proved the pipeline works (72 generations, 1 autonomous promotion) and that editing existing code is the wrong abstraction. Cambrian applies that lesson: evolve the genotype (spec), regenerate the phenotype (code) from scratch.

## Architecture

Three components:

- **Prime** — The organism. Reads the spec, calls an LLM, produces a complete codebase, asks the Supervisor to verify it. Contains its own source, its spec, and its running process.
- **Supervisor** — Host infrastructure. Manages Docker containers, tracks generation history, executes promote/rollback. In M2, orchestrates dual-blind and red-team verification. Not part of the organism — it persists across generations.
- **Test Rig** — Mechanical verification. Builds the artifact, runs tests, starts the process, checks health contracts and FROZEN spec acceptance vectors. Returns a binary viability verdict. No LLM involved.

```
  ┌───────────┐       ┌──────────────┐       ┌───────────┐
  │   Prime   │──────▶│  Supervisor  │──────▶│ Test Rig  │
  │ (organism)│  API  │    (host)    │ spawn │(container)│
  └───────────┘       └──────────────┘       └───────────┘
       │                     │                      │
       │ reads spec          │ manages lifecycle    │ builds, tests,
       │ calls LLM           │ tracks history       │ health-checks
       │ writes artifact     │ promotes/rolls back  │ writes report
```

## Repos

| Repo | Purpose |
|------|---------|
| [cambrian](https://github.com/lispmeister/cambrian) | Specs, Supervisor, Test Rig, Docker, lab journal |
| [cambrian-artifacts](https://github.com/lispmeister/cambrian-artifacts) | Generated artifacts (gen-0, gen-1, ...) and generation history |

## Milestones

- **M1: Reproduce.** ✓ Prime reads a spec, generates a working codebase, passes the test rig. The generated Prime can do the same. Completed 2026-03-29: 5 viable offspring, 474k tokens.
- **Pre-M2 Hardening.** ✓ 3-phase code review, 87 integration tests, anti-cheating verification layers specced. Completed 2026-03-30.
- **M2: Self-modify.** Prime mutates its own spec and tests whether the mutation produces fitter offspring. Three verification layers prevent cheating: FROZEN spec vectors, dual-blind examiner, adversarial red-team.

## Tech Stack

Everything is Python 3.14 for M1 (free-threaded build deferred to M2).

| Component | Key Libraries |
|-----------|--------------|
| Async I/O | `aiohttp`, `aiodocker`, `asyncio` |
| Validation | `pydantic` v2 (all I/O boundaries) |
| Logging | `structlog` (JSON in containers, key-value in dev) |
| Type checking | `pyright` strict mode |
| Tooling | `uv`, `ruff`, `pytest` + `pytest-asyncio` + `pytest-aiohttp` |

## Project Structure

```
spec/
  CAMBRIAN-SPEC-005.md     — Genome spec (what Prime is — consumed by LLM)
  BOOTSTRAP-SPEC-002.md    — Bootstrap spec (Supervisor, Test Rig, Docker)
  SPEC-STYLE-GUIDE.md      — How to write specs
  archive/                 — Superseded specs (historical reference only)
supervisor/                — Host-side Supervisor (aiohttp server)
test-rig/                  — Mechanical verification pipeline
tests/                     — Integration tests (spec compliance, security, lifecycle)
docker/                    — Dockerfile and build script for cambrian-base
lab-journal/               — Discussion and decision logs
```

## Quick Start

```bash
# Clone both repos side by side
git clone https://github.com/lispmeister/cambrian.git
git clone https://github.com/lispmeister/cambrian-artifacts.git

# Create .env with your API key
echo "ANTHROPIC_API_KEY=sk-ant-..." > cambrian/.env

# Build Docker base image
cd cambrian
./docker/build.sh

# Start Supervisor (terminal 1)
source .env
uv run python -m supervisor.supervisor

# Run Gen-1 (terminal 2) — mounts the artifacts ROOT, not the gen-1 subdir
source .env
docker run --rm \
  -v "$(pwd)/../cambrian-artifacts:/workspace:rw" \
  --workdir /workspace/gen-1 \
  --entrypoint /bin/bash \
  -e ANTHROPIC_API_KEY="$ANTHROPIC_API_KEY" \
  -e CAMBRIAN_SUPERVISOR_URL="http://host.docker.internal:host-gateway" \
  -e CAMBRIAN_GENERATION=1 \
  -e CAMBRIAN_WORKSPACE=/workspace \
  -e CAMBRIAN_ESCALATION_MODEL=claude-sonnet-4-6 \
  cambrian-base:latest \
  -c "pip install -r requirements.txt -q && python -m src.prime"
```

> **Mount note:** The volume must be the artifacts root (`cambrian-artifacts/`), not `gen-1/`. Prime writes offspring into sibling directories (`gen-2/`, `gen-3/`, ...) and the Supervisor reads them from there.

See [CLAUDE.md](CLAUDE.md) for development conventions and issue tracking workflow.

---

## Running a Generation Loop with Claude Code

The Supervisor and Docker command above can be orchestrated by Claude Code. Two prompts follow — paste the first into a new session to orient Claude, then use the second to kick off a run.

### Prompt 1 — Project context

Paste this at the start of a new Claude Code session in the `cambrian/` directory:

```
We're working on the Cambrian project — a self-reproducing code factory.

Key concepts:
- Prime is the organism: it reads a spec (CAMBRIAN-SPEC-005.md), calls an LLM, and generates a complete working codebase each generation.
- The Supervisor (supervisor/supervisor.py) is host infrastructure: it manages Docker containers, tracks generation history at ../cambrian-artifacts/generations.json, and handles promote/rollback via HTTP API at port 8400.
- The Test Rig is a mechanical verifier: it builds the artifact, runs tests, starts the process, and checks health contracts. No LLM involved.
- cambrian-artifacts/ (sibling repo) holds the generated artifacts: gen-1/, gen-2/, etc., plus generations.json.

Repos:
- cambrian/ — specs, Supervisor, Test Rig, Docker
- cambrian-artifacts/ — generated artifacts, generation history

Environment:
- ANTHROPIC_API_KEY is in .env — load with: source .env
- Never use pip install directly; use uv
- Supervisor starts with: uv run python -m supervisor.supervisor
- The Docker base image is cambrian-base:latest (build with ./docker/build.sh if missing)
- Use claude-sonnet-4-6 as the default model (budget-conscious); only use claude-opus-4-6 if asked
- Check bd ready for available work before starting anything new
```

### Prompt 2 — Start a generation run

Once Claude has context, use this to kick off a run:

```
Start a generation run from Gen-1.

1. Check that the Supervisor is running (curl http://localhost:8400/health). If not, start it: source .env && uv run python -m supervisor.supervisor &
2. Check that cambrian-base:latest exists (docker images cambrian-base). If not, build it: ./docker/build.sh
3. Run Gen-1 in a container:

source .env && docker run --rm \
  -v "$(realpath ../cambrian-artifacts):/workspace:rw" \
  --workdir /workspace/gen-1 \
  --entrypoint /bin/bash \
  -e ANTHROPIC_API_KEY="$ANTHROPIC_API_KEY" \
  -e CAMBRIAN_SUPERVISOR_URL=http://host.docker.internal:8400 \
  -e CAMBRIAN_GENERATION=1 \
  -e CAMBRIAN_WORKSPACE=/workspace \
  -e CAMBRIAN_ESCALATION_MODEL=claude-sonnet-4-6 \
  cambrian-base:latest \
  -c "pip install -r requirements.txt -q && python -m src.prime"

4. Stream the container logs and report each generation outcome (promoted/failed, test count, tokens).
5. When the loop ends, summarize: total generations, promoted count, cumulative tokens, and update cambrian-iy3 (or create a new bead if iy3 is already closed).
```

## License

[MIT](LICENSE)
