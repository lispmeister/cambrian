# Lab Journal — Summary: What We've Learned (2026-03-21 to 2026-04-01)

This entry distills the key insights, recurring patterns, and hard-won lessons from 21 lab journal sessions spanning 11 days of development.

---

## 1. Project Arc

| Phase | Dates | What happened |
|-------|-------|---------------|
| Design | Mar 21-24 | Language choice, tooling, specs (CAMBRIAN-SPEC-005, BOOTSTRAP-SPEC-002), Hyperagents research |
| Infrastructure | Mar 24d-26 | Supervisor, Test Rig, Dockerfile, gen-0 test artifact, gen-1 Prime (human-written) |
| First autonomous run | Mar 27 | Gen-1 ran autonomously for the first time; 6 offspring, all failed (Python 3.14 quoting) |
| M1 achieved | Mar 29 | 10 generations, 5 promoted. First successful self-reproduction chain |
| Pre-M2 hardening | Mar 30-30c | 87 integration tests, path traversal fix, container isolation, Layer 1 spec vectors, anti-cheating design |
| M2 infrastructure | Mar 31-31c | Fitness vector, campaign runner, spec diff, grammar, mutator, BO loop, entanglement monitor, adaptive tests |
| Integration audit + fix | Mar 31d-31g | Two full code audits, 28 bugs found and fixed, streaming fix, spec compliance audit |
| M2 prep continues | Apr 01 | 5 P1 spec compliance bugs fixed |

**Current state:** M1 complete, M2 infrastructure built, 284 tests pass, 164+ beads closed. First M2 run has not yet succeeded (streaming bug blocked it; now fixed along with 5 more P1 bugs).

---

## 2. Architectural Insights

### The spec is the genome, code is the phenotype

This isn't metaphor -- it's the literal execution model. Prime reads the spec, generates code, and the offspring is a fresh interpretation. Gen-2 does not inherit Gen-1's source code; it inherits the spec. This means bugs in the spec propagate indefinitely, while bugs in the code get a fresh chance each generation.

**Corollary:** Spec bugs are far more expensive than code bugs. A missing streaming rule caused failures across 6 generations before we patched the spec (journal-2026-03-27).

### Three trust boundaries

| Component | Trust level | Why |
|-----------|-------------|-----|
| Prime (organism) | Untrusted | LLM-generated; may cheat under selection pressure |
| Test Rig | Trusted | Mechanical, no LLM, baked into Docker image |
| Supervisor | Trusted | Host-side infrastructure |

This boundary drives every security decision: isolated output mounts, process group kills, FROZEN spec blocks, pre-reading spec vectors before organism code runs.

### The feedback stack

Four innovations, designed in sequence (journal-2026-03-23), that transformed the retry loop from Loom's 1.4% viability rate to Cambrian's ~60%:

```
Contracts    -- organism declares what it promises
Diagnostics  -- environment explains why it failed
Informed retry -- organism sees its own failed code
Fitness vector -- environment measures how good it is
```

Without this stack: blind retry. With it: targeted repair.

### Lamarckian retries, Darwinian generations

Within a generation, retries are Lamarckian (informed by failure context + source code of the failed attempt). Across generations (especially in M2), evolution is Darwinian (clean-room regeneration from a mutated spec). Two evolutionary mechanisms at different timescales, both in one system.

---

## 3. Recurring Bug Patterns

### Pattern 1: The streaming requirement

**Appeared 3 times** in different codebases (journal-2026-03-27 gen-1, journal-2026-03-29 gen-2, journal-2026-03-31g prime_runner.py). Every LLM call site eventually hit the Anthropic API's "streaming required for operations > 10 minutes" error.

**Root cause:** The spec rule existed but new code kept missing it. Each time it was code written from intent rather than from the spec's LLM Integration section.

**Lesson:** Any code that calls the Anthropic API must be cross-checked against the spec's streaming rule. This is the single most frequently recurring bug in the project.

### Pattern 2: Code written from intent, not from spec

`prime_runner.py` is the clearest case. It was the M2 bridge between the BO loop and the Supervisor, written to "do what gen-1's generate.py does." But it was never cross-checked against CAMBRIAN-SPEC-005's LLM Integration, Configuration, and Generation Loop sections. Result: 5 P1 bugs discovered in a single compliance audit (journal-2026-03-31g):

- `CAMBRIAN_TOKEN_BUDGET` misused as per-call `max_tokens`
- `CAMBRIAN_ESCALATION_MODEL` never read
- `pip` instead of `uv pip` in build command
- No streaming
- No model escalation on retries

**Lesson:** Every new module that touches a spec-defined interface needs a spec cross-check pass before merging. "It works like the other module" is not sufficient -- the other module may also be wrong.

### Pattern 3: Python 3.14 string literal strictness

**Appeared across 6 consecutive failed generations** (journal-2026-03-27). The LLM consistently generated string literals with unescaped newlines -- legal in Python <= 3.11, SyntaxError in 3.14. Even after retry feedback explaining the error, Sonnet 4.6 repeated the mistake. Opus 4.6 solved it correctly (4/4 in a focused probe).

**Root cause:** LLM training data is overwhelmingly pre-3.12 Python. The model has weak signal for this constraint.

**Fix:** Added explicit Python 3.14 rules to the SYSTEM_PROMPT and the genome spec. Also changed the file delimiter from `</file>` to `</file:end>` to prevent nested-tag parsing corruption (an independent bug discovered during the same investigation).

### Pattern 4: kebab-case vs snake_case at boundaries

**Wire format is kebab-case, Python internals are snake_case.** This is documented as "the single most common source of bugs" in CLAUDE.md. `artifact-ref` not `artifact_ref`. `created-at` not `created_at`. `generations.update()` takes a `dict`, not `**kwargs`, because Python kwargs can't have hyphens.

### Pattern 5: Spec versions live in two places

Every spec file has a version in frontmatter YAML and footer YAML. They must match. Every version bump has introduced a mismatch at least once (journal-2026-03-24e, journal-2026-03-30). The `TestSpecVersionConsistency` integration test now catches this.

### Pattern 6: Mock the right method

Docker Desktop on macOS: `images.inspect()` returns 404 even when the image exists. The code uses `images.list()` with RepoTags matching. Tests that mocked `images.inspect()` passed accidentally but didn't test the real code path. Caught during code review (journal-2026-03-24e, journal-2026-03-30).

---

## 4. Key Decisions and Their Rationale

### Python for M1

LLM code generation accuracy: Python ~93% vs Rust ~85% vs Elixir ~70%. Prime is I/O-bound (waiting on LLM API calls), so performance is irrelevant. Shared stack with Supervisor and Test Rig. Once self-hosting works, the organism can generate offspring in any language. (journal-2026-03-21)

### Separate artifacts repo

Generated artifacts live in `cambrian-artifacts/`, not as branches in the main project repo. This keeps the project repo clean and avoids git state conflicts between generation management and development work. (journal-2026-03-24c)

### Supervisor owns all git operations

Prime runs inside a Docker container. Giving it git access would require mounting the host repo -- a security violation. The Supervisor creates branches, commits artifacts, promotes/rolls back. Prime only writes files to `/workspace` and calls HTTP endpoints. (journal-2026-03-24b)

### GP-EI over MAP-Elites for Stage 1

MAP-Elites needs 200+ evaluations to meaningfully fill a grid. Stage 1 budget is ~20-30 campaigns. Single-objective Bayesian Optimization (Gaussian Process + Expected Improvement) is more sample-efficient at this scale. MAP-Elites deferred to Stage 2+. (journal-2026-03-29, adversarial review)

### Deterministic grammar over LLM screening

The original dual-model design (one LLM generates mutations, another screens them) is gameable -- an evolved organism could produce code that specifically satisfies the screener. Replaced with `spec_grammar.py`: required sections, port number, FROZEN block byte comparison, no LLM involved. (journal-2026-03-31b, adversarial review)

### Model escalation policy

Sonnet 4.6 at ~$0.30/attempt for first tries; escalate to Opus 4.6 on retries. Opus is reliably better at Python 3.14 edge cases but 5x more expensive and frequently overloaded (HTTP 529). Environment variable `CAMBRIAN_ESCALATION_MODEL` controls this. (journal-2026-03-27, journal-2026-03-24d)

---

## 5. Anti-Cheating: The Verification Layer Stack

Under M2 selection pressure, organisms will evolve to satisfy fitness criteria by any means, including cheating. Three layers of defense, each progressively harder to game:

| Layer | Mechanism | Who controls it | Activation |
|-------|-----------|-----------------|------------|
| 0: Self-Tests | Organism's own pytest suite | Organism | Always |
| 1: Spec Vectors | 5 HTTP tests in FROZEN block of spec | Nobody (immutable) | Always |
| 2: Dual-Blind Examiner | Independent LLM generates tests from spec alone | Environment | M2 Tier 1 |
| 3: Adversarial Red-Team | Independent LLM reads spec + source, tries to break it | Environment | M2 Tier 2 |

Additionally: container isolation (randomized `/output` mount prevents fake viability reports), process group kills (prevents daemonized children surviving termination), and spec vector pre-reading (prevents organism from modifying the spec before evaluation). (journal-2026-03-30, journal-2026-03-30c)

---

## 6. Test Coverage Evolution

| Date | Tests | What was added |
|------|-------|----------------|
| Mar 24d | 34 | Gen-1 unit tests |
| Mar 26 | 72 | Supervisor + Test Rig unit tests |
| Mar 30 | 178 | 87 integration tests (spec compliance, security, lifecycle) |
| Mar 30c | 197 | Layer 1 spec vectors, container isolation |
| Mar 31 | 232 | Fitness vector, campaign runner, spec diff |
| Mar 31b | 260 | Grammar, mutator, screener, BO loop |
| Mar 31c | 278 | Entanglement monitor, adaptive tests |
| Mar 31f | 284 | Bug sprint fixes |
| Apr 01 | 284 | P1 bug fixes (no new tests needed) |

**Notable gap:** `prime_runner.py` (the M2 LLM bridge) has zero unit tests. It is the most-changed module in M2 infrastructure and housed 5 P1 bugs.

---

## 7. Budget and Economics

- **Remaining budget:** ~$22 (as of journal-2026-03-24d)
- **Cost per generation attempt:** ~$0.30 (Sonnet), ~$1.50 (Opus)
- **M1 autonomous run (10 gens):** ~$1.50 total, ~44 minutes
- **Policy:** Default to Sonnet. Ask user before using Opus. Env var `CAMBRIAN_ESCALATION_MODEL=claude-sonnet-4-6` overrides the default Opus escalation.

---

## 8. What Remains for M2

### Ready to run (all infrastructure built)

The M2 entry point exists (`scripts/run_m2.py`). The BO loop, campaign runner, spec mutator, grammar validator, fitness vector, entanglement monitor, and adaptive test generator are all implemented and tested. 159/160 beads closed; the remaining epic is `cambrian-dzf` (M2 Stage 1: Spec Evolution MVP).

### Open P1/P2 bugs (from compliance audit)

5 P1 bugs fixed today. 6 P2 bugs remain (supervisor state machine edge cases). 5 P3 features remain (parse repair, informed retry, contracts extraction, startup git init, full history).

### Not yet attempted

- A successful M2 campaign run (streaming bug blocked the first attempt; now fixed)
- Type 2/3 spec mutations (only Type 1 refinement exists)
- MAP-Elites archive (Stage 2+)
- Meta-Monitor (budget tracking, graceful shutdown)
- Test Tiers 1-3 (progressive difficulty)
- Verification Layers 2-3 (dual-blind examiner, adversarial red-team)

---

## 9. Process Lessons

1. **Audit before running.** Both the Mar 31 deep audits and the Mar 31g compliance audit found critical bugs that would have wasted M2 campaign budget. The cost of a thorough read-through is always less than the cost of debugging a failed run.

2. **The spec is the single source of truth.** When code and spec disagree, the spec wins. When you're adding a feature, update the spec first. When something fails, check the spec before debugging the code.

3. **Integration tests are the highest-ROI tests.** The 87 integration tests (spec compliance, security, lifecycle) caught more bugs per line of test code than any unit test. They verify cross-component contracts, not individual functions.

4. **Two audits are better than one.** The first audit (journal-2026-03-31d) found 13 bugs. The second audit of the same codebase (journal-2026-03-31e) found 13 more, including 4 P1 blockers the first audit missed.

5. **Record failures.** Every failed generation in the journal has taught us something. The Python 3.14 quoting investigation (6 failed generations, 1 probe script, 1 delimiter redesign) was the most valuable debugging session of the entire project.

6. **Beads enforce accountability.** Every piece of work has a tracking issue. The commit-msg hook rejects commits without bead references. This overhead pays for itself in session recovery -- after compaction or a new conversation, `bd ready` immediately shows what needs doing.

---

**Signed:** lispmeister + AI assistant
**Date:** 2026-04-01

**Participants & Tools:** Claude Sonnet 4.6 (plan mode: Opus 4.6), Python 3.14.3
**Commit / Witness:** (summary entry -- no code changes)
**Related Specs / Beads:** CAMBRIAN-SPEC-005 v0.14.1, BOOTSTRAP-SPEC-002 v0.10.0

**Next journal entry:** journal-2026-04-01b.md (use TEMPLATE.md)
