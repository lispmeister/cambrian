# Cambrian

A self-reproducing code factory. Cambrian reads a specification, calls an LLM, and produces a complete working codebase — including a new instance of itself capable of doing the same thing.

<p align="center">
  <img src="docs/images/cambrian-sea-monster.png" alt="Anomalocaris — apex predator of the Cambrian explosion" width="600">
</p>

## Status

**Design phase — no running code yet.** The system spec is at [CAMBRIAN-SPEC-004](spec/CAMBRIAN-SPEC-004.md), the bootstrap plan at [BOOTSTRAP-SPEC-001](spec/BOOTSTRAP-SPEC-001.md). Next step: build the Supervisor and Test Rig, then generate Gen-1 Prime.

## The Idea

Code is disposable. The specification is the genome.

An LLM-powered organism ("Prime") reads a spec and regenerates the entire codebase from scratch each generation. No diffs, no accumulated cruft, no path dependence. A mechanical test rig — no LLM involved — decides if the result is viable. If it is, the offspring replaces the parent. If not, it's discarded.

This came from [Loom](https://github.com/lispmeister/loom), which tried source-code-level self-modification in ClojureScript. Loom proved the pipeline works (72 generations, 1 autonomous promotion) and that editing existing code is the wrong abstraction. Cambrian applies that lesson: evolve the genotype (spec), regenerate the phenotype (code) from scratch.

## Architecture

Three components:

- **Prime** — The organism. Reads the spec, calls an LLM, produces a complete codebase, asks the Supervisor to verify it. Contains its own source, its spec, and its running process.
- **Supervisor** — Host infrastructure. Manages Docker containers, tracks generation history, executes promote/rollback. Not part of the organism — it persists across generations.
- **Test Rig** — Mechanical verification. Builds the artifact, runs tests, starts the process, checks health. Returns a binary viability verdict. No LLM involved.

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

## Milestones

- **M1: Reproduce.** Prime reads a spec, generates a working codebase, passes the test rig. The generated Prime can do the same. This is the immediate goal.
- **M2: Self-modify.** Prime mutates its own spec and tests whether the mutation produces fitter offspring.

## Tech Stack

Everything is Python 3.14t (free-threaded, GIL disabled) for M1.

| Component | Key Libraries |
|-----------|--------------|
| Async I/O | `aiohttp`, `aiodocker`, `asyncio` |
| Validation | `pydantic` v2 (all I/O boundaries) |
| Logging | `structlog` (JSON in containers, key-value in dev) |
| Type checking | `pyright` strict mode, zero errors in CI |
| Tooling | `uv`, `ruff`, `pytest` + `pytest-asyncio` |
| Introspection | `rich`, `devtools`, `typing-inspect` |

## Project Structure

```
spec/
  CAMBRIAN-SPEC-004.md     — System spec (contracts, schemas, lifecycle)
  BOOTSTRAP-SPEC-001.md    — Bootstrap spec (Supervisor, Test Rig, Docker)
  SPEC-STYLE-GUIDE.md      — How to write specs
  diagrams/                — Architecture and sequence diagrams (.mmd)
lab-journal/               — Discussion and decision logs
scripts/
  setup-dev.sh             — Developer environment setup
  setup-claude.sh          — Claude Code plugins and skills setup
```

## Getting Started

```bash
git clone https://github.com/lispmeister/cambrian.git
cd cambrian
./scripts/setup-dev.sh
```

See [CLAUDE.md](CLAUDE.md) for development conventions and issue tracking workflow.

## License

[MIT](LICENSE)
