# Cambrian

A self-reproducing code factory. Cambrian reads a specification, calls an LLM, and produces a complete working codebase — including a new instance of itself capable of doing the same thing.

## Status

Early design. The generative specification ([CAMBRIAN-SPEC-002](spec/CAMBRIAN-SPEC-002.md)) is drafted. No code yet.

## Goals

1. **Reproduce.** Prime reads a spec and generates a complete, working Prime from it. The generated Prime can do the same. This is M1.
2. **Verify mechanically.** A dumb test rig (no LLM) checks every generation: does it build, do tests pass, does it start, does it respond. Binary: viable or not.
3. **Stay language-agnostic.** The spec doesn't prescribe an implementation language. We'll evaluate Rust, Elixir, Python, and Mojo based on how well LLMs can generate correct code for each.
4. **Evolve the spec, not the code.** Code is disposable — regenerated from scratch each generation. The specification is the genome. This is the key insight from Loom.
5. **Self-modify (later).** Once reproduction works, Prime can mutate its own spec and test whether the mutation produces a fitter offspring. This is M2+.

## Architecture

Three components:

- **Prime** — The organism. A code factory that reads a spec, calls an LLM to generate code, and asks the Supervisor to verify the result. Contains its own source code, its spec, and its running process.
- **Supervisor** — Host-side infrastructure. Manages container lifecycle, generation history, and the promote/rollback decision. Not part of the organism.
- **Test Rig** — Runs inside an ephemeral container. Builds the artifact, runs tests, checks health. Returns a structured viability report. No LLM involved.

## Project Structure

```
spec/
  CAMBRIAN-SPEC-001.md   — Original spec (carried from Loom)
  CAMBRIAN-SPEC-002.md   — Current spec (language-agnostic, two-part)
  diagrams/              — Architecture and sequence diagrams
```

## License

[MIT](LICENSE)

---

Cambrian is the successor to [Loom](https://github.com/lispmeister/loom), which explored source-code-level self-modification in ClojureScript. Loom proved the pipeline works (72 generations, 1 autonomous promotion) and that the right level of abstraction for evolution is the specification, not the source code.
