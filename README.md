# Cambrian

A self-reproducing code factory. Cambrian reads a specification, calls an LLM, and produces a complete working codebase — including a new instance of itself capable of doing the same thing.

<p align="center">
  <img src="docs/images/cambrian-sea-monster.png" alt="Anomalocaris — apex predator of the Cambrian explosion" width="600">
</p>

## Status

**M2 Stage 1 running. Bayesian Optimization loop over spec mutations is operational.**

What's done:
- Phase 0: Supervisor, Test Rig, Docker image, gen-0 validated end-to-end
- M1: Gen-1 ran 44 minutes, 10 total generations, 5 promoted (gen-4, gen-6, gen-8, gen-9, gen-10), all at 100% test pass rate. 474,834 cumulative tokens.
- Pre-M2 hardening: 16-bead code review (specs, Python, Docker). Path traversal fix, field naming unification, Docker non-root user, 284 tests across multiple test files, all passing.
- Verification layers: Three-layer anti-cheating model specced and Layer 1 (FROZEN spec acceptance vectors) implemented. Dual-blind examiner and adversarial red-team specced for M2 Tiers 1–2.
- M2 Stage 1: Bayesian Optimization loop operational. Grammar-constrained spec mutations, mini-campaign screening, 15-dimension fitness vector, campaign runner, spec diff tooling all implemented and running.

Next: Run full M2 campaigns (20 BO iterations) to determine whether spec mutations improve viability rate over the baseline.

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
- **M2: Self-modify.** 🔄 In progress. Prime mutates its own spec and tests whether the mutation produces fitter offspring. BO loop operational as of 2026-04-01. Three verification layers prevent cheating: FROZEN spec vectors (implemented), dual-blind examiner, adversarial red-team.

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

# Run M2 BO loop (terminal 2)
source .env && \
  CAMBRIAN_START_GENERATION=1 \
  CAMBRIAN_BO_BUDGET=20 \
  CAMBRIAN_CAMPAIGN_LENGTH=5 \
  CAMBRIAN_MINI_CAMPAIGN_N=2 \
  CAMBRIAN_BO_INITIAL_POINTS=5 \
  CAMBRIAN_ESCALATION_MODEL=claude-sonnet-4-6 \
  uv run python scripts/run_m2.py
```

The BO loop runs until the budget is exhausted and writes `best-spec.md` if any viable spec is found. For a quick smoke test, use `CAMBRIAN_BO_BUDGET=5 CAMBRIAN_CAMPAIGN_LENGTH=2 CAMBRIAN_MINI_CAMPAIGN_N=1 CAMBRIAN_BO_INITIAL_POINTS=3`.

See [CLAUDE.md](CLAUDE.md) for development conventions and issue tracking workflow.

---

## Running M2 with Claude Code

The Supervisor and M2 loop can be orchestrated by Claude Code. Paste this at the start of a session in the `cambrian/` directory:

```
We're working on the Cambrian project — a self-reproducing code factory in M2.

Key concepts:
- Prime is the organism: reads a spec (CAMBRIAN-SPEC-005.md), calls an LLM, generates a complete working codebase each generation.
- The Supervisor (supervisor/supervisor.py) is host infrastructure: manages Docker containers, tracks generation history, handles promote/rollback via HTTP API at port 8400.
- The Test Rig is a mechanical verifier: builds the artifact, runs tests, starts the process, checks health contracts. No LLM involved.
- M2 runs via scripts/run_m2.py — a Bayesian Optimization loop that mutates the spec and tests whether mutations improve viability rate.
- cambrian-artifacts/ (sibling repo) holds generated artifacts and generation history.

Environment:
- ANTHROPIC_API_KEY is in .env — load with: source .env
- Never use pip install directly; use uv
- Supervisor starts with: uv run python -m supervisor.supervisor
- Docker base image: cambrian-base:latest (rebuild with ./docker/build.sh after any test-rig changes)
- Default model: claude-sonnet-4-6; only use claude-opus-4-6 if asked
- Check bd ready for available work before starting anything new
```

## License

[MIT](LICENSE)
