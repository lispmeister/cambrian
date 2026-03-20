# Cambrian — Project Instructions for Claude Code

## What This Is

A self-reproducing code factory. See [README.md](README.md) for project goals and [spec/CAMBRIAN-SPEC-002.md](spec/CAMBRIAN-SPEC-002.md) for the generative specification.

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
  CAMBRIAN-SPEC-001.md   — Original spec (carried from Loom, historical)
  CAMBRIAN-SPEC-002.md   — Current spec (the genome)
  diagrams/              — Architecture and sequence diagrams (.mmd, .svg, .png)
```

## Implementation Language

Not yet decided. The spec is language-agnostic. Candidates: Rust, Elixir, Python, Mojo. Do not assume a language unless code already exists.

## Milestones

- **M1: Reproduce** — Prime reads spec → generates codebase → offspring passes test rig → offspring can do the same
- **M2+: Self-modify** — Prime mutates its own spec, tests whether the mutation produces fitter offspring

## References

- [CAMBRIAN-SPEC-002](spec/CAMBRIAN-SPEC-002.md) — The generative specification
- [Loom](https://github.com/lispmeister/loom) — Predecessor project (source-code-level evolution, archived at v0.2.0)
- [Final retrospective](https://github.com/lispmeister/loom/blob/master/architecture-reviews/review-2026-03-20-001.md) — Lessons from Loom

## Issue Tracking

See [AGENTS.md](AGENTS.md) for full beads (`bd`) workflow instructions. Use `bd` for ALL task tracking.
