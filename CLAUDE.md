# Cambrian — Project Instructions for Claude Code

## What This Is

A self-reproducing code factory. See [README.md](README.md) for project goals and [spec/CAMBRIAN-SPEC-004.md](spec/CAMBRIAN-SPEC-004.md) for the system specification.

## Key Concepts

- **Prime** is the organism — a code factory that reads a spec and generates a complete working codebase via LLM
- **The spec is the genome** — Prime reads it, never modifies it (spec mutation is a future concern)
- **The test rig is the invariant** — a fixed verification pipeline (build → test → start → health → report), no LLM
- **Containers are the keep/revert boundary** — promote = tag + record, revert = destroy + discard

## Sacred Files

DO NOT MODIFY the test rig's viability report schema or the Supervisor HTTP API contracts without explicit user approval. These are the fixed points that allow components to communicate across generations.

## Project Structure

```
spec/
  CAMBRIAN-SPEC-004.md   — System spec (contracts, schemas, lifecycle)
  BOOTSTRAP-SPEC-001.md  — Bootstrap spec (Supervisor, Test Rig, infrastructure)
  CAMBRIAN-SPEC-005.md   — Genome spec (what Prime is — consumed by LLM)
  SPEC-STYLE-GUIDE.md    — How to write specs
  diagrams/              — Architecture and sequence diagrams (.mmd, .svg, .png)
lab-journal/
  journal-*.md           — Discussion and decision logs
```

## Implementation Language

Python 3.14 free-threaded build (`python3.14t`) for everything in M1. See CAMBRIAN-SPEC-004 § Implementation Language for full details.

## Tech Stack

- `uv` — package management, venv, lockfile
- `aiohttp` — async HTTP server and client
- `aiodocker` — async Docker container management
- `pydantic` (v2) — I/O validation, serialization, schemas
- `structlog` — structured logging
- `rich` / `devtools` / `typing-inspect` — introspection and debugging
- `pyright` — strict mode type checker
- `ruff` — linter and formatter
- `pytest` + `pytest-asyncio` — testing

## Milestones

- **M1: Reproduce** — Prime reads spec → generates codebase → offspring passes test rig → offspring can do the same
- **M2+: Self-modify** — Prime mutates its own spec, tests whether the mutation produces fitter offspring

## Issue Tracking and Commit Messages

**Every piece of work MUST have a bead.** No code without a tracking issue. See [AGENTS.md](AGENTS.md) for full beads (`bd`) workflow.

### Commit message format

Every commit message MUST reference a bead ID. A `commit-msg` git hook enforces this — commits without a bead reference are rejected.

```
bd-NNN: Short description of the change

Optional longer explanation.

Co-Authored-By: Claude Opus 4.6 <noreply@anthropic.com>
```

### Workflow

1. **Before writing code:** Create or claim a bead (`bd create` or `bd update <id> --claim`)
2. **While working:** Reference the bead ID in all commits
3. **When done:** Close the bead (`bd close <id>`)
4. **Discovered new work?** Create a linked bead before context-switching

### Enforcement

- `commit-msg` hook rejects commits without `bd-NNN` in the message
- Merge commits are exempt

## References

- [CAMBRIAN-SPEC-004](spec/CAMBRIAN-SPEC-004.md) — System specification
- [BOOTSTRAP-SPEC-001](spec/BOOTSTRAP-SPEC-001.md) — Bootstrap specification
- [SPEC-STYLE-GUIDE](spec/SPEC-STYLE-GUIDE.md) — Spec writing conventions
- [Loom](https://github.com/lispmeister/loom) — Predecessor project (archived at v0.2.0)
- [Final retrospective](https://github.com/lispmeister/loom/blob/master/architecture-reviews/review-2026-03-20-001.md) — Lessons from Loom
