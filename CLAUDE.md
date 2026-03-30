# Cambrian — Project Instructions for Claude Code

## What This Is

A self-reproducing code factory. See [README.md](README.md) for project goals, [spec/CAMBRIAN-SPEC-005.md](spec/CAMBRIAN-SPEC-005.md) for the genome spec, and [spec/BOOTSTRAP-SPEC-002.md](spec/BOOTSTRAP-SPEC-002.md) for the bootstrap spec.

## Key Concepts

- **Prime** is the organism — a code factory that reads a spec and generates a complete working codebase via LLM
- **The spec is the genome** — Prime reads it, never modifies it (spec mutation is a future concern)
- **The test rig is the invariant** — a fixed verification pipeline (build → test → start → health → report), no LLM
- **Containers are the keep/revert boundary** — promote = tag + record, revert = destroy + discard

## Mental Model

These are the reasoning principles for working on this project. Read them before writing code.

**The spec is the source of truth, code is derived.** When code and spec disagree, the spec wins. When you're unsure how something should behave, read the spec — don't infer from the code. When you're adding a feature, update the spec first.

**Three trust boundaries — know which side you're on:**

| Component | Trust | Why |
|-----------|-------|-----|
| Prime (organism) | Untrusted | LLM-generated; may be defective or — in M2 — adversarially evolved |
| Test Rig | Trusted | Mechanical, no LLM, baked into Docker image; the environment's voice |
| Supervisor | Trusted | Host-side infrastructure; persists across generations |

Changes to trusted components (test rig, supervisor) need explicit user approval. The Test Rig's viability report schema and the Supervisor HTTP API are the fixed communication contracts — don't modify them without approval.

**Wire format is kebab-case, Python internals are snake_case.** All JSON fields in manifests, generation records, and API bodies use hyphens (`artifact-ref`, `spec-hash`, `created-at`). Python variables and kwargs use underscores. This is the single most common source of bugs across generations. When in doubt: if it crosses a network boundary or gets written to JSON, it's kebab-case.

**FROZEN blocks are inviolable.** Text between `<!-- BEGIN FROZEN: name -->` and `<!-- END FROZEN: name -->` must never be modified — not by Claude, not by LLM-generated code, not by spec mutations in M2. These blocks define what the organism fundamentally is. If you find yourself needing to change a FROZEN block, stop and discuss with the user.

## Common Pitfalls

Lessons learned from the pre-M2 quality review. Don't repeat these.

**Mock `images.list()`, not `images.inspect()`.** The Supervisor uses `docker.images.list()` to check whether an image exists (Docker Desktop doesn't reliably resolve `images.inspect()` for existing images). Tests that mock `images.inspect` pass accidentally but don't test the real code path.

**Path traversal: always `.resolve()` then check containment.** Any user-supplied path joined onto an artifacts root must be resolved and validated:
```python
path = (Path(artifacts_root) / user_input).resolve()
if not str(path).startswith(str(Path(artifacts_root).resolve())):
    return error_response("path escapes artifacts root")
```
Symlinks, `..` segments, and absolute-path injection all bypass naive string checks.

**`generations.update()` takes a `dict`, not `**kwargs`.** Kebab-case field names like `"artifact-ref"` are invalid Python identifiers and can't be passed as keyword arguments. Always call it as `generations.update(gen, {"artifact-ref": tag})`, never `generations.update(gen, artifact_ref=tag)`.

**Spec versions live in two places — update both.** Each spec file has a version in the frontmatter YAML block (near the top) and again in the footer YAML block (near the bottom). The `test_spec_compliance.py` test `TestSpecVersionConsistency` will catch a mismatch, but it's better not to introduce one. When bumping a spec version, grep for both occurrences.

**Unattempted pipeline stages in viability reports are absent, not zero.** The Test Rig's fail-fast pipeline skips later stages when an earlier one fails. Absent fitness metrics mean "not attempted"; zero means "attempted and measured zero". These are not the same — don't conflate them when reading reports.

## Verification Cheat Sheet

```bash
# Run all tests (178 total)
uv run pytest

# Integration tests only (spec compliance, security, lifecycle)
uv run pytest tests/ -v

# Unit tests only
uv run pytest supervisor/ test-rig/ -v

# Single file
uv run pytest tests/test_spec_compliance.py -v

# Lint and format
uv run ruff check .
uv run ruff format --check .

# Auto-fix lint
uv run ruff check --fix .
uv run ruff format .
```

Tests are fast (~0.5s for the full suite). Run them before committing. The CI contract: all 178 pass, ruff clean.

## Sacred Files

DO NOT MODIFY the test rig's viability report schema or the Supervisor HTTP API contracts without explicit user approval. These are the fixed points that allow components to communicate across generations. They are defined in CAMBRIAN-SPEC-005 and BOOTSTRAP-SPEC-002.

## Archived Specs

`spec/archive/` contains superseded specs (CAMBRIAN-SPEC-001 through 004, BOOTSTRAP-SPEC-001). These are retained for historical reference ONLY. Never reference, analyze, quote from, or generate code from archived specs. All active contracts, schemas, and implementation guidance live exclusively in CAMBRIAN-SPEC-005 and BOOTSTRAP-SPEC-002.

## Project Structure

```
spec/
  CAMBRIAN-SPEC-005.md   — Genome spec (what Prime is — consumed by LLM)
  BOOTSTRAP-SPEC-002.md  — Bootstrap spec (Supervisor, Test Rig, infrastructure)
  SPEC-STYLE-GUIDE.md    — How to write specs
  diagrams/              — Architecture and sequence diagrams (.mmd, .svg, .png)
  archive/               — Superseded specs (SPEC-001 through 004, BOOTSTRAP-SPEC-001)
lab-journal/
  journal-*.md           — Discussion and decision logs
```

## Implementation Language

Python 3.14 for M1 (free-threaded build deferred to M2). See BOOTSTRAP-SPEC-002 § Implementation Language for full details.

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

## Lab Journal

Every session that changes code, specs, or design decisions **must** have a journal entry. The journal follows Howard M. Kanare's *Writing the Laboratory Notebook* principles: permanent, replicable, self-contained records.

**Starting a new entry:**
1. Copy `lab-journal/TEMPLATE.md` to `lab-journal/journal-YYYY-MM-DD.md` (append `b`, `c`, … for multiple sessions on the same day).
2. Fill in the date and session goals before you start work.
3. Add sections as the session progresses — don't backfill.

**Ending an entry:**
Fill in the footer block at the bottom:
- **Signed / Date** — always
- **Participants & Tools** — model name (from the Co-Authored-By tag in the commit), Python version, key libraries
- **Commit / Witness** — the git commit hash(es) produced in this session + bead IDs
- **Related Specs / Beads** — active spec versions and bead IDs
- **Next journal entry** — next file name (or `journal-YYYY-MM-DD.md (use TEMPLATE.md)`)

**After each entry is committed:**
Update `lab-journal/index.md` — add one row to the TOC table with the date, filename, key topics, and milestone/bead.

**Format rules (Kanare):**
- Record immediately — no reconstructing from memory hours later.
- Each entry must stand alone: enough detail that another person (or future you) could reproduce the session.
- Failures and rollbacks are as important as successes — record both.
- Link to specs, beads, and commit hashes; don't describe what git already records.

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

## M2 Context

M1 (autonomous reproduction loop) is complete. M2 (spec mutation + selection) is next. Before starting any M2 work, read this section.

### Critical Path

`cambrian-2cf` (container isolation hardening, P1) is the prerequisite for all M2 population runs. An adversarial review found that organism and Test Rig share a filesystem volume — under selection pressure, evolved code could write a fake viability report. This must be fixed before running campaigns.

### Ready Beads (no blockers, pick up in this order)

| ID | Priority | Title | Why first |
|----|----------|-------|-----------|
| `cambrian-4do` | P1 | Implement Layer 1 spec-vector evaluation | Closes the anti-cheating loop — spec is written, ~50 lines of code |
| `cambrian-2cf` | P1 | Harden container isolation | Unblocks all M2 population runs |
| `cambrian-mw0` | P2 | 15-dimension fitness vector | Required for selection between viable organisms |
| `cambrian-evw` | P2 | Spec diff tooling + section attribution | Required for directed spec mutation |
| `cambrian-9ic` | P2 | Campaign runner | Coordinates N generations against one spec |

### Verification Layers Status

Three layers are specced in CAMBRIAN-SPEC-005 § Verification Layers. As of 2026-03-30:

| Layer | Status |
|-------|--------|
| Layer 1: FROZEN spec acceptance vectors | Specced ✓, not yet implemented in test_rig.py (`cambrian-4do`) |
| Layer 2: Dual-blind LLM examiner | Specced ✓ (BOOTSTRAP-SPEC-002 §2.10), M2 Tier 1 |
| Layer 3: Adversarial red-team | Specced ✓ (BOOTSTRAP-SPEC-002 §2.11), M2 Tier 2 |

### Budget

Default model: `claude-sonnet-4-6` (~$0.30/attempt). Ask the user before using `claude-opus-4-6`. The env var `CAMBRIAN_ESCALATION_MODEL=claude-sonnet-4-6` keeps costs down. Remaining budget is tracked in beads memories — run `bd memories budget` to check.

### M2 Deferred Items

These are known issues intentionally deferred from the pre-M2 hardening. Do not implement them as side effects of other work — open a bead first:

- Pydantic v2 models (currently using `dict[str, Any]` throughout)
- `asyncio.Lock` on generation store + spawn guard
- App-state refactor (module globals → dataclass on `app["state"]`)
- `shell=True` in test rig commands → `shlex.split()`
- Artifact-hash verification in Test Rig

## References

- [CAMBRIAN-SPEC-005](spec/CAMBRIAN-SPEC-005.md) — Genome spec (the living contract)
- [BOOTSTRAP-SPEC-002](spec/BOOTSTRAP-SPEC-002.md) — Bootstrap specification
- [SPEC-STYLE-GUIDE](spec/SPEC-STYLE-GUIDE.md) — Spec writing conventions
- [Loom](https://github.com/lispmeister/loom) — Predecessor project (archived at v0.2.0)
- [Final retrospective](https://github.com/lispmeister/loom/blob/master/architecture-reviews/review-2026-03-20-001.md) — Lessons from Loom
