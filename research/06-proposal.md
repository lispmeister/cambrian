# Proposal: Genome Evolution for Cambrian M2

**Version**: 0.1 — first draft
**Status**: Proposal, not spec
**Date**: 2026-03-25

---

## The Thesis

Every self-improving agent system we studied mutates implementations. AlphaEvolve mutates matrix kernels. FunSearch mutates mathematical functions. Darwin Godel Machine mutates its own source code. All of them treat the specification as fixed and the implementation as the thing that evolves.

Cambrian should invert this.

**The spec is the genome. Prime is the universal constructor. What evolves is the genome.**

This isn't just a philosophical difference. It's a qualitative architectural advantage:

1. The spec is *legible*. A human can read a mutated spec and understand what it means. You cannot read mutated neural network weights or evolved machine code.

2. The spec is *composable*. You can take section 3 from Spec A and section 7 from Spec B and produce a coherent hybrid. You cannot meaningfully splice sections of bytecode.

3. The spec is *versioned*. Every spec mutation is a diff against the parent. The entire lineage of evolution is a git history of spec changes. You can observe *why* each generation was better or worse.

4. The spec is *safe to evolve*. Prime cannot modify the spec (Invariant 1). The Test Rig is invariant (Invariant 2). The container is the boundary. These aren't constraints on evolution — they're what make safe evolution possible. Darwin Godel Machine failed because it could touch its own evaluator. Cambrian structurally cannot.

---

## The Architecture: Three Loops

M2 adds a **meta-loop** around M1's generation loop. The structure is:

```
Loop 3 (Meta): Spec population evolves across campaigns
  Loop 2 (Campaign): One spec variant runs N generations
    Loop 1 (Generation): Prime(spec) → artifact → Test Rig → viability report + fitness
```

### Loop 1: Generation (unchanged from M1)

The M1 loop is sacred. It doesn't change for M2. One spec in, one artifact out, one fitness measurement. The Test Rig is the invariant evaluator — it never changes based on the generation's output.

### Loop 2: Campaign

A **campaign** is a fixed number of generations (configurable, default 5) run against a single spec variant. A campaign produces:
- Per-generation viability (pass/fail)
- Per-generation fitness vector (15 dimensions)
- A campaign summary: {viability_rate, fitness_mean, fitness_trend, failure_modes}

The **fitness trend** is new. If viability is 40% in generation 1 and 80% in generation 5, the spec is "learning-compatible" — Prime can improve with it. If viability is flat across 5 generations, the spec is confusing Prime in a way that doesn't self-correct.

### Loop 3: Meta

The meta-loop maintains a **spec population** — a set of spec variants competing for the next campaign.

At the end of each campaign, the meta-loop:
1. Scores the spec variant using campaign summary metrics
2. Updates the MAP-Elites archive
3. Selects parents for the next round of mutation
4. Generates child spec variants via the Spec Mutator
5. Schedules the next campaigns

---

## The Spec Population and MAP-Elites

The spec population is not a flat list. It's a **MAP-Elites archive** — a multi-dimensional grid where each cell holds the best-performing spec variant for that behavioral niche.

The behavior descriptor uses 4 of the 15 fitness dimensions:
1. **Viability rate**: fraction of generations that pass the Test Rig
2. **Token economy**: input + output tokens per generation (inverse — lower is better)
3. **Time to viability**: generations until first viable artifact
4. **Fitness trend**: slope of viability improvement across generations

Each cell in the 4D grid holds the spec that achieves the best *campaign fitness score* for that combination of behavioral dimensions. The archive is sparse — most cells are empty initially, filling as evolution progresses.

**Why MAP-Elites over hill-climbing**: We don't know which region of the fitness space matters for the next task. A spec that's excellent at producing fast code but poor at correctness might be exactly what we need when we move from the echo server to a compute-intensive task. The archive preserves all solutions, not just the current winner.

**Why 4 dimensions not 15**: Higher-dimensional MAP-Elites archives are exponentially sparser. 4 dimensions gives a manageable archive that covers the most important trade-offs.

---

## The Spec Mutator

The Spec Mutator is a new component — not Prime, not the Supervisor, not the Test Rig. It's a meta-Prime that takes spec variants and produces modified spec variants.

### Three mutation types

**Type 1: Refinement** (small, targeted)
- Input: One spec variant + one campaign's failure modes
- Prompt: "Here is a spec. When Prime followed this spec, it consistently failed at [stage]. What single change to the spec would address this failure mode?"
- Output: A diff — one section rewritten or one paragraph added
- Expected improvement: Immediate. Addresses a known failure mode.
- Protection needed: None. These almost always improve or maintain fitness.

**Type 2: Section transplant** (medium, structural)
- Input: Two spec variants from different MAP-Elites niches
- Prompt: "Spec A produces fast code. Spec B produces correct code. Rewrite Spec A's [section] using Spec B's approach to [concept], keeping Spec A's approach to everything else."
- Output: A hybrid spec
- Expected behavior: Uncertain. The hybrid may combine the best of both or be incoherent.
- Protection needed: The hybrid runs as a new campaign before replacing either parent. It earns its archive slot.

**Type 3: Restructuring** (large, exploratory)
- Input: One spec variant + the campaign history showing a plateau
- Prompt: "This spec has plateaued at [viability rate] for [N] campaigns. Here is the failure mode distribution. Propose a fundamentally different approach to [section] that addresses the structural failure pattern."
- Output: A section rewrite — same purpose, different approach
- Expected behavior: Initially worse, potentially much better
- Protection needed: **Speciation** — run the restructured variant in an isolated campaign track for 3 campaigns before comparing to the main population. NEAT's lesson: structural innovations need time to develop before they face full selection pressure.

### The Dual Model

Following AlphaEvolve's architecture:
- **Mutator model** (expensive, creative): claude-opus-4-6. Proposes spec mutations. Called infrequently.
- **Screener model** (fast, critical): claude-sonnet-4-6. Pre-screens proposed mutations for obvious coherence violations before running a campaign. "Does this spec still describe a coherent system?" If no, discard without running.

The screener model doesn't evaluate fitness — only coherence. It prevents wasting campaign budget on incoherent mutations.

---

## The Meta-Monitor (VSM System 4)

The Supervisor already tracks generation-level state. The meta-monitor tracks *campaign-level trends*.

**What it watches**:
- Archive diversity: Is the MAP-Elites archive growing or collapsing?
- Fitness trend: Is the archive's best-performing spec improving across campaigns?
- Failure mode distribution: Are failure modes clustering (suggests a fixable spec issue) or random (suggests implementation variance)?
- Budget burn rate: At current campaign frequency, how many campaigns before budget exhaustion?

**What it does**:
- **If archive collapsing**: Increase mutation type 3 (restructuring) frequency. Increase exploration.
- **If fitness plateauing for N campaigns**: Trigger a "lateral transfer" event — take the section with lowest contribution to failures from the best-performing archive variant and inject it into all active campaign specs.
- **If budget burn too fast**: Reduce campaign length (fewer generations per campaign) and mutation frequency.
- **If failure modes random**: Reduce type 1 (refinement) mutations — they fix specific failures that may not recur. Increase type 3 (restructuring).

**The meta-monitor is not an LLM**. It's deterministic code. Heuristics, thresholds, rules. The reason: an LLM meta-monitor could learn to game its own observations. Darwin Godel Machine all over again.

---

## The Identity Anchor (VSM System 5)

Some things must not evolve. The identity anchor is a **frozen section** in every spec variant — a section that the Spec Mutator is forbidden to modify.

The frozen content:

```
## Invariants (frozen — cannot be mutated)

1. Prime MUST NOT modify the spec file or any files outside its workspace.
2. Prime MUST NOT self-assess viability. The Test Rig is the sole arbiter of viability.
3. Prime MUST NOT perform git operations. The Supervisor manages all git operations.
4. The artifact manifest MUST be produced by Prime's deterministic manifest builder, not by the LLM.
5. Prime MUST copy the spec file to the offspring workspace unchanged.
```

These five invariants are the constitutional axioms. Every spec variant, regardless of how many mutations it accumulates, contains them verbatim. The Spec Mutator checks the output of every mutation — if any invariant text is modified, the mutation is rejected.

This is the hard boundary that prevents Cambrian from evolving into something that satisfies the metrics by undermining the evaluation mechanism.

---

## The Growing Test Corpus (Red Queen)

The Test Rig today evaluates against fixed contracts. M2 adds **test tiers**:

**Tier 0 (always active)**: Basic viability — build succeeds, tests pass, service starts, health endpoint responds. This is M1.

**Tier 1 (activates when Tier 0 viability > 80%)**: Behavioral contracts from the spec. For the echo server: correct echo responses, proper error codes, consistent behavior across 100 requests.

**Tier 2 (activates when Tier 1 viability > 80%)**: Robustness contracts. Concurrent connections, malformed inputs, restart behavior, resource cleanup.

**Tier 3 (activates when Tier 2 viability > 80%)**: Performance contracts. Response latency < 100ms, throughput > 1000 req/s, memory < 50MB.

**The Red Queen mechanism**: Each tier is unlocked by achieving the previous tier reliably. The spec must evolve to describe code that satisfies all active tiers. As tiers activate, the fitness bar rises. The spec that produces code passing Tier 3 today is a better spec than the spec that produced code passing Tier 0 six months ago — *even if the human-written baseline never improved*.

This is capability ratchet. The test suite grows with the system. Overfitting to current tests doesn't help — there are always harder tests waiting.

---

## Hindsight Relabeling of Non-Viable Generations

SOAR's insight: failed attempts demonstrate partial capabilities. Apply this to Cambrian.

When a generation is non-viable (Test Rig fails), the viability report says *why*. Current behavior: this info is passed to the next generation as context. Proposed M2 behavior: **extract sub-achievements**.

If the generation failed at the "start" stage but passed "build" and "test", then:
- Record: "This spec produced code that builds and passes unit tests (but doesn't start)"
- This generation's fitness contribution: build=1, test=1, start=0, health=0
- The spec gets partial credit in the MAP-Elites archive *for the dimensions it did satisfy*

This does two things:
1. The archive cells for "specs that produce building code" and "specs that produce tested code" get updated even from a non-viable generation
2. Prime's next generation prompt can say: "In the previous attempt, the code built and passed tests but failed to start. Focus on fixing the start-up configuration."

Failure becomes a structured stepping stone, not just a binary outcome to avoid.

---

## Neutral Drift Periods

Every 10 campaigns, trigger a **neutral drift period**: for 2 campaigns, the selection criterion is *anything that passes Tier 0 viability* — not fitness ranking. All viable specs continue regardless of fitness vector scores.

**Why**: Under constant selection pressure, the spec population converges. Neutral drift allows structurally different specs to accumulate without being eliminated. When the task changes (new spec, harder requirements), the diverse population adapts faster than a monoculture.

**Implementation**: The meta-monitor triggers neutral drift. During neutral drift, all Tier 0 viable campaign results are added to the archive regardless of where they fall in the MAP-Elites grid.

---

## What This Produces That Nobody Else Has

1. **A legible, versioned evolutionary history**: Every spec mutation is a git diff. You can read the history of how the spec improved. You can bisect to find when a capability was gained or lost.

2. **Separation of genome and constructor**: Prime doesn't evolve — the spec evolves. Prime is the invariant universal constructor. This is structurally safe in a way no existing self-modifying system is.

3. **External ground truth**: The Test Rig is immutable and runs in an isolated environment. No amount of spec evolution can corrupt the evaluation mechanism. Darwin Godel Machine's fatal flaw is impossible in this architecture.

4. **Formal behavioral niches**: MAP-Elites maintains the spec that optimizes each fitness dimension. When you need the fastest-code spec, it's in the archive. When you need the most-correct-code spec, it's there too. You don't optimize for one thing and lose another.

5. **Constitutional axioms**: The frozen invariant section means evolution can't undermine the mechanisms that make evolution safe. Self-improvement is bounded by axioms that don't evolve.

6. **Growing tests**: Red Queen dynamics mean the system gets harder to satisfy over time. Overfitting is impossible when the fit target moves.

---

## What to Build First (M2 Stage 1)

Before all of this, M1 must work. The five implementation BLOCKERs (cambrian-23g, cambrian-ddw, cambrian-uua, cambrian-l4j, cambrian-0id) must be fixed. Gen-1 must produce Gen-2. Gen-2 must produce Gen-3 (echo server).

When M1 is solid, M2 Stage 1 is:

1. **Campaign runner**: Wrap the generation loop to run N generations against one spec and produce a campaign summary. This is pure infrastructure — no mutation yet.

2. **Fitness vector computation**: Extend the Test Rig to output the full 15-dimension fitness vector, not just viability boolean. This requires implementing the metrics that are currently informational-only.

3. **Spec diff tooling**: Infrastructure to produce readable diffs between spec versions, and to apply/revert diffs. The mutation operators need this.

4. **MAP-Elites archive**: A simple data structure (initially a JSON file) storing one spec variant per niche, indexed by the 4 behavioral dimensions.

5. **Type 1 mutations only**: Start with refinement mutations. Give the Spec Mutator failure modes from a campaign and ask for targeted fixes. Measure whether the next campaign performs better.

Measure: does the Type 1 mutated spec produce a campaign with higher viability rate than the original? If yes, M2 has demonstrated value. If no, stop and investigate before adding complexity.

---

## The Experiment M2 Must Run

**Hypothesis**: A spec produced by 10 campaigns of Type 1 mutation achieves higher viability rate than the human-written baseline spec, without human intervention.

**Success criterion**: The evolved spec's campaign viability rate (averaged across 5 campaigns) exceeds the baseline spec's campaign viability rate by >20%.

**If it works**: Add Type 2 mutations (section transplant) and the MAP-Elites archive. Measure diversity.

**If it doesn't**: The Spec Mutator prompts are wrong, the failure mode extraction is insufficient, or the fundamental hypothesis is wrong. Investigate with bisection — run mutation on a known-fixable failure, manually verify the mutation addresses it, then test whether the campaign actually improves.

The research exists. The architecture is sound. The question is empirical.
