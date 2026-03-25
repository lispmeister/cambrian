# Research: Evolutionary and Genetic Code Systems

> Raw findings from parallel research pass. Treat as inspiration, not literature review.

---

## The Fundamental Question

Biological evolution doesn't optimize — it explores. Fitness landscapes are deceptive: local optima are everywhere, the global optimum may require passing *through* low-fitness states to reach it. Every successful evolutionary computing system wrestles with this.

---

## Tierra (Ray, 1991)

**What it does**: Digital ecosystem. Self-replicating programs compete for CPU time and memory in a shared address space. No explicit fitness function — survival *is* the fitness function. Programs that replicate faster survive.

**What emerged spontaneously**:
- Parasites: programs that couldn't self-replicate but could "borrow" other programs' replication code
- Hyper-parasites: programs that stole CPU time from parasites
- Symbiotes: programs that cooperated for mutual benefit
- Length polymorphism: shorter programs had advantages in memory-constrained environments

**Key insight**: **Endogenous fitness**. The environment evaluates fitness automatically; no external function needed. Complexity emerged from competition, not from design.

**What it lacks**: No way to direct the evolution toward useful goals. The system explores freely but you can't ask it to "make a better HTTP server." Fully endogenous fitness is scientifically interesting but practically useless for software engineering.

**Cambrian relevance**: Tier's endogenous fitness is the theoretical extreme. Cambrian's Test Rig is an explicit fitness function — the pragmatic opposite. But Tierra's lesson is that *competition between variants* produces more complex adaptations than any single-organism optimization. Multiple simultaneous spec variants competing to produce viable code is Tierra at the spec level.

---

## Avida (Ofria & Wilke, 2004)

**What it does**: Tierra with directed evolution. Digital organisms evolve in a controlled environment where complex behaviors (like logic operations) are rewarded with extra CPU cycles. Organisms that evolve EQU (equivalence logic) reproduce faster.

**Key result**: Complex behaviors emerge via stepping stones. EQU is too complex to evolve in one step. The organisms first evolve simpler logic (AND, OR, NOT), which accidentally also helps them evolve XOR, which helps them evolve EQU. The pathway was not designed — it was discovered.

**Lesson**: **Stepping stones matter more than the destination**. You cannot jump directly to the target fitness from scratch. Evolution needs intermediate forms that are *viable enough to survive* while also being *stepping stones toward the target*.

**Cambrian relevance**: The M1 target (echo server) seems trivial. But the stepping stone is M2: Prime evolves to produce better Prime, which produces better code, which eventually handles harder specs. The intermediate generations must be viable (pass Test Rig) to survive long enough to accumulate improvements. Viability as a binary filter creates the selection pressure; the Test Rig defines the stepping stones.

---

## NEAT (Stanley & Miikkulainen, 2002) — Neuroevolution of Augmenting Topologies

**What it does**: Evolves neural network topology and weights simultaneously. Key innovation: **speciation** to protect innovation.

**The speciation problem**: New mutations to network topology are almost always bad initially. A random new connection degrades performance. In a standard evolutionary algorithm, the new mutation gets eliminated immediately — before it has a chance to be refined. Innovation gets killed before it can develop.

**NEAT's solution**: Organisms with similar topologies compete only against each other, not the whole population. A "new" topology is given time to improve within its niche before facing the full population. This protects structural innovations during the period when they're still worse than incumbents.

**Result**: NEAT evolved networks for complex tasks (pole balancing, game playing) that couldn't be evolved without speciation — because the intermediate forms were too unfit to survive under naive selection.

**Cambrian relevance**: Spec mutations come in two types:
1. **Refinements**: Clarifying existing sections — immediate improvement, no protection needed
2. **Restructurings**: Changing the overall approach — initially likely to produce worse results before better ones

Without speciation-equivalent, Cambrian M2 will over-exploit refinements and never try restructurings. A restructuring (e.g., "change from synchronous to async architecture") requires many generations to pay off. It needs a protected niche.

---

## PushGP / Autoconstructive Evolution (Spector et al.)

**What it does**: Programs evolve in a stack-based language (Push). The interesting variant: **autoconstructive evolution** — each organism contains its own reproduction code. Organisms can evolve their own mutation operators.

**Key insight**: The mutation operator is itself evolvable. A program that figures out how to mutate itself well has a selection advantage. This produces *diversity of diversity* — some organisms explore broadly, others refine narrowly.

**Observation**: Autoconstructive systems produce more diverse and more capable populations than systems with a fixed mutation operator. The fixed mutation operator is a hyperparameter that nobody's values are right for all problems.

**Cambrian relevance**: Right now, Prime is the mutation operator (it writes the spec mutation prompts in M2). What if the *prompt for mutating the spec* could itself evolve? A second-order loop: spec → code, and mutation-prompt → better-mutation-prompt. This is STOP (see research/01) applied specifically to the mutation step.

---

## ELM (Lehman et al., 2022) — Evolution through Large Models

**What it does**: Combines LLM code generation with MAP-Elites quality-diversity search. The LLM proposes code *diffs*; MAP-Elites maintains a diverse archive of solutions, each representing a different behavioral niche.

**Architecture**:
1. Select a parent program from the archive
2. Ask LLM: "Here is program P. Write a variant that does something different/better"
3. Evaluate the variant on all metrics
4. Add to archive if it represents a new niche or improves an existing one
5. Repeat

**Results**: On open-ended locomotion tasks (Sodarace), ELM found qualitatively different solutions that hill-climbing approaches missed entirely. Diversity was structural, not just parametric.

**Key insight**: The LLM's training distribution includes vast diversity. When you ask it to produce "something different," it draws on that diversity. The MAP-Elites archive prevents convergence by maintaining one representative per behavioral niche — diversity is *structurally maintained*, not hoped for.

**Cambrian relevance**: This is the closest existing system to what Cambrian M2 should do. Replace "locomotion" with "code quality metrics from the fitness vector." The MAP-Elites archive stores one spec variant per behavioral niche (e.g., one spec that minimizes tokens, one that maximizes test coverage, one that minimizes latency). Each niche is a dimension of the fitness vector.

---

## SOAR (2024) — Soar into Abstract Representations

**What it does**: Hindsight relabeling for code generation. When an agent fails a task, the system *relabels* the partial solution as if it were the goal — "you failed to write an HTTP server, but you wrote a TCP listener; let's record that as a success."

**Results**: 52% on ARC-AGI (abstract reasoning tasks). Hindsight relabeling turned failures into stepping stones. The system learned to solve complex tasks by accumulating simpler capabilities discovered accidentally.

**Connection to Avida**: This is Avida's stepping-stone dynamics implemented in LLM systems. You don't need to evolve EQU directly — you evolve the components of EQU and compose them.

**What's novel**: Traditional RL needs a reward signal. SOAR constructs its own training signal retrospectively from failed trajectories. Every failure becomes a successful demonstration of *something* — just not the original target.

**Cambrian relevance**: Non-viable generations aren't just failures — they're partial successes. A generation that passes 3/5 test stages is a demonstration of "build + test + start works." If subsequent generations build on that foundation (rather than starting fresh), viability rates improve faster. The viability report already gives us the components — we just need to use them as stepping stones.

---

## MAP-Elites (Mouret & Clune, 2015)

**What it does**: Quality-diversity algorithm. Maintains a grid where each cell represents a behavioral niche (defined by a behavior descriptor). Each cell holds the single best solution for that niche. Selection draws from the archive, not from a single population.

**Key properties**:
- **Illumination**: Maps the entire space of solutions, not just the best
- **Quality-diversity**: Maximizes both quality *and* diversity — doesn't collapse to a single solution
- **No population size pressure**: Archive can grow arbitrarily; selection is from archive, not survival

**Why it beats standard evolutionary algorithms on deceptive problems**: The archive maintains solutions to *all* niches, including ones that aren't currently best. When the landscape shifts (a previously useless niche becomes important), solutions are already there.

**Cambrian relevance**: The fitness vector has 15 dimensions. MAP-Elites naturally maps to: maintain one spec variant per high-fitness region of the 15D fitness space. This isn't a population — it's a *map* of the spec space. When you need "the spec that produces the fastest code," you look it up. When you need "the spec that produces the most robust code," different cell.

---

## Neutral Drift Theory (Kimura, 1968 + modern extensions)

**What it is**: In biology, most mutations are neither beneficial nor harmful — they're neutral. Neutral mutations accumulate without selection pressure. This neutral drift creates *genetic diversity* that can be recruited when the environment changes.

**Applied to code evolution**: Programs that are functionally equivalent but structurally different are "neutral" with respect to the fitness function. They're equally fit — but if the fitness function changes (new task, new platform), one might suddenly outperform the other.

**Experiment (Wilke et al.)**: Two populations: one under constant selection pressure, one with relaxed pressure (neutral drift allowed). When the environment changed, the neutral-drift population adapted faster because it had accumulated structural diversity.

**Cambrian relevance**: The fitness vector should *not* always be maximized. Periodically relaxing pressure on some dimensions allows neutral drift — spec variants that aren't the current best but explore structurally different approaches. When the task changes (moving from echo server to something harder), diverse variants adapt faster than a monoculture of optimized variants.

---

## Cross-System Patterns

1. **Stepping stones are the mechanism**: Evolution never jumps directly to complex behavior. Cambrian needs intermediate viable states between current Prime and the target capability.

2. **Competition creates complexity**: Tierra and NEAT both show that competition between variants produces more complex adaptations than single-organism optimization. Multiple spec variants in competition is qualitatively different from sequential attempts.

3. **Protect structural innovation (NEAT)**: New architectural approaches are initially worse. Need a mechanism to let them develop before facing full selection pressure. This is the most commonly missing feature in code evolution systems.

4. **Hindsight is a resource (SOAR)**: Failed attempts contain successful demonstrations of sub-capabilities. Don't discard failures — mine them.

5. **Diversity is structural, not random (MAP-Elites)**: Temperature and randomness don't maintain diversity under selection. Structural mechanisms (niches, islands, archives) do.

6. **Neutral drift is productive (Kimura)**: Constant maximum selection pressure produces monocultures. Brittle under distribution shift. Relaxed periods produce resilience.
