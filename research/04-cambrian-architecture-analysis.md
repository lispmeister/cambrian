# Research: Cambrian Architecture Analysis

> Raw findings from parallel research pass. Treat as inspiration, not literature review.

---

## Von Neumann Universal Constructor — The Theoretical Basis

Von Neumann's 1948 proof: a universal constructor U, given a description D of any machine M, can build M and produce a copy of D alongside it. The key is the dual use of D:
1. As instructions (build M from D)
2. As data (copy D into the offspring)

Cambrian maps to this exactly:

| Von Neumann | Cambrian |
|---|---|
| Universal constructor U | Prime |
| Description D | CAMBRIAN-SPEC-005.md |
| Built machine M | Generated codebase (artifact) |
| Copied description | Spec copied to offspring workspace |
| Offspring | Gen-N Prime |

**The critical insight**: The spec is simultaneously the *program* and the *data*. Prime executes the spec (as a program to follow) and copies the spec (as data for the next generation). Von Neumann showed this is sufficient for self-reproduction. Cambrian has implemented it.

**What Von Neumann doesn't tell us**: He proved *reproduction* is possible. He said nothing about *improvement*. For evolution, you need variation + selection + inheritance. Cambrian M1 has inheritance. M2 needs variation and selection.

---

## The Three Sacred Invariants

These aren't implementation choices — they're the architectural axioms that make Cambrian's evolutionary potential coherent:

**Invariant 1: Prime MUST NOT modify the spec**
- Why: If the constructor modifies its own description, the description no longer accurately describes the constructor. Von Neumann's proof breaks. You have undefined behavior.
- M2 implication: Spec mutation happens *outside* of Prime's generation loop. A separate meta-process proposes spec mutations and selects among them. Prime is the executor, not the mutator.

**Invariant 2: Prime MUST NOT self-assess viability**
- Why: Darwin Godel Machine showed that self-assessment collapses under selection pressure. The evaluator gets corrupted. External evaluation is the only safe mode.
- M2 implication: The fitness vector is computed entirely by the Test Rig. Prime never sees its own score during generation — only through the viability report *after* the fact.

**Invariant 3: Prime MUST NOT perform git operations**
- Why: Git history is the lineage record. If Prime could rewrite git history, it could hide its ancestry, break reproducibility, and corrupt the lineage graph.
- M2 implication: All promotion/rollback decisions belong to the Supervisor. The Supervisor is the keeper of lineage.

---

## The Fitness Vector — 15 Dimensions

Current dimensions (from CAMBRIAN-SPEC-005 § Meta-Strategy):

| # | Dimension | Category |
|---|---|---|
| 1 | Time to first viable generation | Speed |
| 2 | Generations to stability | Speed |
| 3 | Test Rig pass rate | Correctness |
| 4 | Contract satisfaction rate | Correctness |
| 5 | Behavioral correctness | Correctness |
| 6 | Input token budget | Economy |
| 7 | Output token budget | Economy |
| 8 | API call count | Economy |
| 9 | Wall-clock time per generation | Economy |
| 10 | Cost per generation | Economy |
| 11 | Crash rate in production | Robustness |
| 12 | Error recovery rate | Robustness |
| 13 | Spec compliance score | Correctness |
| 14 | Code quality score | Correctness |
| 15 | Dependency count | Economy |

**M1 status**: Informational only. The fitness vector is computed and logged but doesn't drive selection. All selection in M1 is binary: viable/non-viable.

**M2 opportunity**: These 15 dimensions become the MAP-Elites behavior descriptor. Each cell in the 15D archive holds the spec variant that achieves the best balance of that niche's defining dimensions.

**Gap identified**: The fitness vector has no *robustness under distribution shift* dimension. A spec might produce great code for the echo server task but terrible code for any other task. This generalization dimension is missing and is what separates a locally optimal spec from a genuinely better one.

---

## The Loom Retrospective — What Not to Repeat

Loom (predecessor project, archived at v0.2.0) achieved a 1.4% viability rate using blind retry. Every generation started from scratch with no information from previous failures.

**Why blind retry fails**:
1. If viability is 1.4%, the expected number of attempts to get one success is ~71. At $0.30/attempt, that's ~$21 per successful generation.
2. Rare successes don't compound — each success produces an offspring that also has 1.4% viability. You don't accumulate improvements.
3. Failures contain information that blind retry discards. The 98.6% failure rate is signal, not noise.

**What informed retry does differently**:
- The viability report from failed generations becomes context for the next generation
- Each generation Prime knows what went wrong in the parent and why
- Over generations, the failure rate should decrease monotonically (if the spec and implementation are working correctly)

**M2 implication**: The failure *rate over generations* is the key metric for evaluating whether M2's spec mutation is working. If mutation + selection is improving the spec, viability rates should increase. If they're flat or decreasing, the selection mechanism is broken.

---

## M2 Vision — The Meta-Strategy Layer

CAMBRIAN-SPEC-005 § Meta-Strategy describes M2 as "spec mutation and selection." The current spec says:

> In M2, Prime will be asked to produce not just a codebase but a modified version of its own genome. The modified genome will be tested: does the next generation produce more viable offspring? Better fitness vectors? More compact representations?

**What this means concretely**:

The M2 generation loop is a *second-order loop* around the M1 generation loop:

```
M1 loop (current):
  for each generation:
    Prime(spec) → artifact → Test Rig → viability

M2 loop (proposed):
  for each spec variant:
    run N generations of M1 loop with this spec variant
    measure fitness vector across those N generations
    select winning spec variant
    mutate winning spec variant → new candidates
    repeat
```

**Key insight**: M2 doesn't replace M1. It wraps it. M2 evaluates specs by running M1 and observing fitness. The Test Rig invariant is preserved — M2's selection pressure comes from M1's observable outcomes.

---

## Current Implementation Gaps (BLOCKERs Before M2)

Five open beads representing things the code must fix before M2 is possible:

| Bead | Issue |
|---|---|
| cambrian-23g | Supervisor container timeout missing — containers can hang forever |
| cambrian-ddw | Test Rig build/test timeouts swapped (120/300 → should be 300/120) |
| cambrian-uua | Test Rig manifest validation missing — required fields not checked |
| cambrian-l4j | Test Rig contract evaluation missing — contracts in manifest not evaluated |
| cambrian-0id | Test Rig diagnostics incomplete — exit_code and failures[] not populated |

**Why these block M2**: M2 requires running many M1 iterations automatically. If containers can hang forever (cambrian-23g), the M2 loop hangs. If contracts aren't evaluated (cambrian-l4j), M2 has no way to measure "did the spec produce code that satisfies its contracts" — which is the core M2 fitness signal.

---

## Risks for M2

**Risk 1: Fitness vector fidelity**
The fitness vector measures what the Test Rig can measure, not what we care about. Goodhart's Law: when a measure becomes a target, it ceases to be a good measure. A spec could optimize for fast test completion by generating trivially simple code. We need fitness dimensions that are hard to game.

**Risk 2: Context window explosion**
Each generation adds to the history. By generation 50, the prompt includes 50 viability reports, 50 spec versions, 50 fitness vectors. This is unworkable. M2 needs a compression strategy: summarize distant history, keep recent generations verbatim.

**Risk 3: Budget**
At $0.30/attempt × N attempts per spec variant × M spec variants per generation, M2 costs scale quadratically. A 10-spec, 10-attempt M2 loop costs $30 per M2 generation. Budget discipline is a first-class concern.

**Risk 4: Reward hacking (Darwin Godel Machine lesson)**
Any metric that M2's selection pressures can game, will be gamed. The Test Rig must be harder to corrupt than the benefit of corrupting it. Container isolation is the current protection — it must be maintained.

**Risk 5: Spec drift**
Over many generations, if spec mutations are too large or too frequent, the spec may drift away from "describes Prime" toward "describes something that happens to pass the current test suite." The spec must remain *interpretable as a description of Prime* — not just fitness-optimized text.

---

## The Question M2 Must Answer

**Can a system improve its own specifications faster than human engineers can improve them manually?**

If yes, Cambrian M2 is useful. If the LLM-driven spec mutation + Test Rig selection produces specs that generate better code than human-written specs, the self-improvement loop is real.

If no — if hand-tuned specs always outperform evolved specs — then M2 is interesting as an experiment but not as a product.

M1's job is to establish the baseline: human-written spec + Gen-1 Prime → viable Gen-2 Prime. The baseline generation quality is the number M2 must beat.
