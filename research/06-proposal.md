# Proposal: Genome Evolution for Cambrian M2

**Version**: 0.2 — revised after adversarial review (file 08)
**Status**: Proposal, not spec
**Date**: 2026-03-29 (originally 2026-03-25)

---

## The Thesis

Many self-improving agent systems mutate implementations directly. AlphaEvolve mutates matrix kernels. FunSearch mutates mathematical functions. Darwin Godel Machine mutates its own source code. Others — PromptBreeder, VisionForge, TextGrad, DSPy — evolve structured text artifacts (prompts, behavioral specifications, module signatures) via LLM. A-Evolve (Amazon, 2026) evolves agent workspace files (prompts, skills, memory) under benchmark selection pressure. Text evolution via LLM is not new. But all of these systems treat the evolved artifact as the output — the prompt IS the thing used, the code IS the thing executed.

Cambrian should add a level of indirection.

**The spec is the genome. Prime is the universal constructor. What evolves is the genome, not the phenotype.**

This isn't just a philosophical difference. The genotype-phenotype indirection is a qualitative architectural advantage over direct evolution:

1. The spec is *legible*. A human can read a mutated spec and understand what it means. You cannot read mutated neural network weights or evolved machine code.

2. The spec is *composable*. You can take section 3 from Spec A and section 7 from Spec B and produce a coherent hybrid. You cannot meaningfully splice sections of bytecode. (But see §Modularity Pressure below — composability degrades under entanglement.)

3. The spec is *versioned*. Every spec mutation is a diff against the parent. The entire lineage of evolution is a git history of spec changes. You can observe *why* each generation was better or worse.

4. The spec is *safe to evolve*. Prime cannot modify the spec (Invariant 1). The Test Rig is invariant (Invariant 2). The container is the boundary. These aren't constraints on evolution — they're what make safe evolution possible. (But see §Container Isolation below — the execution boundary must be hardened.)

5. The spec has *recursive fitness*. The offspring must read its own spec and reproduce. This self-reproduction requirement is unlike anything in prompt optimization or code evolution — it tests not just the output quality but whether the output can itself be a constructor.

What's novel is NOT "evolving text via LLM" — PromptBreeder, VisionForge, and others do that. What's novel is the *indirection*: evolving a specification that a separate constructor interprets to produce an implementation, at multi-thousand-word document scale, with recursive self-reproduction as the fitness test.

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

## The Spec Population: Staged Algorithm

*Revised after adversarial review (file 08, §4). MAP-Elites needs hundreds of evaluations to populate a 4D archive. Cambrian's evaluation budget is ~50–100 campaigns at current costs. The algorithm must match the budget.*

### Stage 1 (~20–30 campaigns): Single-Objective Bayesian Optimization

Start simple. One objective: maximize viability rate. BO is state-of-the-art for single-objective expensive optimization with small budgets. The GP surrogate models the fitness landscape cheaply; the acquisition function balances exploration and exploitation without manual scheduling.

No population, no archive, no behavioral dimensions. Just: which spec mutation most likely improves viability? A-Evolve's results (file 07) validate that single-lineage evolution is a competitive starting point.

### Stage 2 (~50–100 campaigns): BOP-Elites

When viability is reliable and we want diversity, upgrade to BOP-Elites (Kent & Branke, 2023, arXiv 2307.09326) — Bayesian Optimization of Elites. Models fitness AND behavioral descriptors with GP surrogates. Designed for "problems with expensive black-box fitness and behavior functions." Add 2 behavioral dimensions:

1. **Viability rate**: fraction of generations that pass the Test Rig
2. **Token economy**: input + output tokens per generation (inverse — lower is better)

### Stage 3 (200+ campaigns): Full MAP-Elites

Only when the budget supports it. Expand to 4 behavioral dimensions:

3. **Time to viability**: generations until first viable artifact
4. **Fitness trend**: slope of viability improvement across generations

Each cell in the 4D grid holds the spec that achieves the best campaign fitness score for that combination of behavioral dimensions.

**Why staged over MAP-Elites from day one**: The M2 success experiment is 10 campaigns. A 625-cell archive (5 bins × 4 dims) would be ~98% empty. The archive is useless until it's populated enough to provide selection diversity. Start with what works at small scale, add complexity when the data supports it.

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

### Mutation Model and Screening

*Revised after adversarial review (file 08, §5). The dual-model screener is vulnerable to Adversarial Goodhart — under selection pressure, specs will evolve to look coherent to the screener without being coherent. "One Token to Fool LLM-as-a-Judge" (arXiv 2507.08794) shows 80% false positive rates. ThetaEvolve (arXiv 2511.23473) achieved SOTA without a screener.*

- **Mutator model** (expensive, creative): claude-opus-4-6. Proposes spec mutations. Called infrequently.
- **Screening via short campaign**: Instead of an LLM judge, run a 2-generation mini-campaign. If neither generation passes Tier 0 viability, the mutation is rejected. The Test Rig is the judge — deterministic, immutable, ungameable.
- **Deterministic invariant check**: `grep` the mutated spec for the frozen invariant text. If any invariant is altered, reject without running. This is string comparison, not LLM judgment.

Budget impact: mini-campaigns cost more than a Sonnet call per-mutation but provide real fitness signal rather than surface-level coherence estimates. They also serve as the first data point for the BO surrogate model.

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

## Exploration-Exploitation Balance

*Revised after adversarial review (file 08, §8). Fixed neutral drift periods waste 20% of budget. In a 10-campaign experiment, 2 neutral drift campaigns generate no directional signal. Temperature annealing is strictly better.*

### Stage 1–2 (BO / BOP-Elites)

The acquisition function handles exploration/exploitation automatically. No neutral drift needed — the GP uncertainty estimate IS the exploration signal. When the model is uncertain about a region, it explores; when confident, it exploits.

### Stage 3 (MAP-Elites)

Replace fixed neutral drift with **CMA-MAE temperature annealing** (arXiv 2205.10752): a temperature parameter smoothly interpolates between uniform acceptance (exploration) and elitist selection (exploitation). Temperature decays over time — exploration when the archive is sparse, exploitation when it's populated. No manual scheduling, no wasted budget.

**Adaptive trigger**: If the meta-monitor detects archive diversity collapsing, increase the temperature parameter. If fitness is improving steadily, let it decay. This replaces the fixed "every 10 campaigns" schedule with a responsive mechanism.

---

## Container Isolation (New — from adversarial review, file 08 §6)

*Highest-priority fix identified by adversarial review. Under evolutionary pressure, organisms WILL find filesystem-based attacks on the Test Rig if the execution boundary is permeable.*

The organism's `entry.build`, `entry.test`, and `entry.start` execute inside the container. The Test Rig evaluates the organism's output. If both share the same filesystem with the same permissions, a mutated spec could describe code that writes a fake `viability-report.json`, modifies test-rig code between pipeline stages, or reads the Test Rig source to reverse-engineer what passes.

The frozen invariants prevent Prime from *writing* such instructions in the spec. But under evolutionary pressure, mutations to the spec will discover indirect paths — describing code that manipulates the filesystem as a side-effect of "legitimate" operations.

**Requirements**:
1. Organism commands (`entry.build`, `entry.test`, `entry.start`) run in a separate process namespace from the Test Rig
2. `/test-rig/` is not mounted in the organism's namespace (or mounted read-only at most)
3. Organism commands run as non-root user
4. The Test Rig evaluates against a fresh copy of the workspace, not the one the organism may have modified
5. The viability report is written by the Test Rig, never by the organism

This is not speculative — it's documented in evolutionary computation literature (Lehman et al., "Surprising Creativity of Digital Evolution") where organisms evolved to delete target files to achieve perfect fitness.

---

## Modularity Pressure (New — from adversarial review, file 08 §2)

*The monolithic spec will evolve toward entanglement without explicit pressure to maintain modularity.*

Clune (Royal Society, 2013): modularity evolves only when there is a connection cost. Without this pressure, systems remain monolithic and entangled. The M2 spec has no modularity pressure — no penalty for cross-section dependencies.

**Prediction**: Over many mutations, sections will develop increasing cross-references and implicit dependencies. Type 2 (transplant) mutations will produce more breakage as entanglement grows. The spec becomes less composable over time, not more.

**Mitigations**:
1. **Section-level fitness attribution**: After each mutation, diff the spec and attribute fitness changes to specific sections. Track which sections contributed positively and which degraded. This is the precondition for detecting linkage drag (beneficial mutation in one section dragging harmful changes in adjacent sections).
2. **Entanglement monitoring**: Track how many sections each mutation must touch to achieve improvement. If this number trends upward, entanglement is growing.
3. **Modularity as a fitness dimension** (Stage 2+): Add a behavioral descriptor for spec modularity — e.g., number of cross-section references, section independence score. MAP-Elites will then preserve both modular and entangled variants, allowing evolution to discover whether modularity helps.

---

## Adaptive Test Generation (New — from adversarial review, file 08 §9)

*The Red Queen tiers are a fixed curriculum. Adaptive test generation targets specific failure modes.*

Keep Tier 0–3 as immutable floors (these define what "viable" means). Add **Tier X** (adaptive):

After each failed campaign, extract the failure mode from the viability report. Use an LLM to generate 3–5 targeted test cases probing that specific failure. Add them to the next campaign's evaluation.

Inspired by Absolute Zero Reasoner (NeurIPS 2025 Spotlight, arXiv 2505.03335), which generates its own training tasks via self-play and achieves SOTA without human-curated examples. The insight: the same model that generates code can generate adversarial tests.

**Constraints**:
- Adaptive tests supplement fixed tiers, never replace them
- Adaptive tests expire after 5 campaigns (prevent accumulation of stale tests)
- Total adaptive test count capped at 10 per campaign (prevent test bloat)

**Caution**: Co-evolved tests can drift toward testing irrelevant edge cases. The fixed tiers anchor what "viable" means; adaptive tests explore failure surfaces within those tiers.

---

## Self-Referential Fitness Dimensions (New — from adversarial review, file 08 §7)

Three fitness dimensions are self-referential: `test_count`, `test_pass_rate`, `test_coverage`. The organism generates its own test suite. Under selection pressure, a spec will evolve to describe code with trivial tests (`assert True`) that maximize these metrics without testing anything meaningful.

**Mitigations**:
1. Weight self-referential dimensions lower than Test Rig dimensions in the fitness vector
2. Add test-quality signals: assertion density (meaningful assertions per test), mutation score (do tests catch injected faults?)
3. The Red Queen Tier 1 (behavioral contracts from the spec) provides external test obligations that the organism cannot trivially satisfy

---

## What This Produces That Nobody Else Has

1. **Genotype-phenotype indirection**: The spec (genotype) is not the thing that executes — Prime (the constructor) interprets it to produce the codebase (phenotype). Other systems evolve the artifact that IS the output (PromptBreeder evolves prompts used directly; AlphaEvolve evolves code executed directly). Cambrian uniquely evolves the *blueprint* that a constructor reads. This is Von Neumann's Universal Constructor architecture applied to LLM-driven evolution.

2. **Recursive self-reproduction as fitness**: The offspring must read its own spec and reproduce. This recursive viability test is absent from prompt optimization, code evolution, and all systems surveyed in files 01–07. It tests not just output quality but whether the output can itself be a constructor.

3. **A legible, versioned evolutionary history**: Every spec mutation is a git diff. You can read the history of how the spec improved. You can bisect to find when a capability was gained or lost. (Shared with A-Evolve's git-tagged mutations — file 07.)

4. **External ground truth with hardened isolation**: The Test Rig is immutable and runs in an isolated environment. The organism executes in a separate process namespace with no access to Test Rig code. No amount of spec evolution can corrupt the evaluation mechanism. (See §Container Isolation for the specific hardening requirements.)

5. **Constitutional axioms**: The frozen invariant section means evolution can't undermine the mechanisms that make evolution safe. Self-improvement is bounded by axioms that don't evolve. (But see file 08 §6 for indirect circumvention risks.)

6. **Growing tests + adaptive targeting**: Red Queen tiers mean the system gets harder to satisfy over time. Adaptive test generation (Tier X) targets specific recurring failure modes. Fixed tiers prevent overfitting; adaptive tests prevent plateaus.

---

## What to Build First (M2 Stage 1)

*Revised after adversarial review. Simplified: no MAP-Elites, no dual-model screener, no neutral drift. Start with what works at small scale.*

Before all of this, M1 must work. Gen-1 must produce Gen-2. Gen-2 must produce Gen-3 (echo server).

When M1 is solid, M2 Stage 1 is:

1. **Container isolation hardening**: Before any evolution, ensure the organism cannot influence the Test Rig via filesystem. Separate process namespace, `/test-rig/` not mounted in organism space, non-root user, viability report written only by Test Rig. This is a precondition for safe evolution.

2. **Campaign runner**: Wrap the generation loop to run N generations against one spec and produce a campaign summary. This is pure infrastructure — no mutation yet.

3. **Fitness vector computation**: Extend the Test Rig to output the full 15-dimension fitness vector, not just viability boolean. Discount self-referential dimensions (`test_count`, `test_pass_rate`, `test_coverage`) relative to external dimensions.

4. **Spec diff tooling + section attribution**: Infrastructure to produce readable diffs between spec versions, and to attribute fitness changes to specific sections. The mutation operators need this; section attribution detects linkage drag early.

5. **Single-objective BO loop**: Bayesian optimization over spec mutations, maximizing viability rate. No archive, no behavioral dimensions — just one GP surrogate predicting which mutation most likely improves viability. This replaces the MAP-Elites archive for Stage 1.

6. **Type 1 mutations with lightweight grammar constraints**: Refinement mutations that preserve the spec's heading hierarchy and required sections. Free-form LLM mutation within grammar constraints — structurally valid by construction, not by screening. Deterministic `grep` check for frozen invariant integrity.

7. **Mini-campaign screening**: Each mutation gets a 2-generation mini-campaign. If neither generation passes Tier 0, reject. If one does, run a full 5-generation campaign. The Test Rig is the judge.

Measure: does the Type 1 mutated spec produce a campaign with higher viability rate than the original? If yes, M2 has demonstrated value. If no, stop and investigate before adding complexity.

---

## The Experiment M2 Must Run

**Hypothesis**: A spec produced by 10 campaigns of Type 1 mutation achieves higher viability rate than the human-written baseline spec, without human intervention.

**Success criterion**: The evolved spec's campaign viability rate (averaged across 5 campaigns) exceeds the baseline spec's campaign viability rate by >20%.

**Secondary measurement — evolution-scaling**: Plot viability rate vs. campaign number. Is the improvement curve linear, logarithmic, or asymptotic? The A-Evolve paper's evolution-scaling hypothesis (arXiv 2602.00359) predicts improvement scales with evolution compute. Our experiment can confirm or bound this for spec evolution.

**If it works**: Add Type 2 mutations (section transplant) and upgrade from BO to BOP-Elites. Measure diversity. Monitor entanglement.

**If it doesn't**: The Spec Mutator prompts are wrong, the failure mode extraction is insufficient, or the fundamental hypothesis is wrong. Investigate with bisection — run mutation on a known-fixable failure, manually verify the mutation addresses it, then test whether the campaign actually improves.

The research exists. The architecture has been adversarially reviewed and hardened. The question is empirical.
