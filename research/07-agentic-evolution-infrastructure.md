# Research: Agentic Evolution Infrastructure

> Raw findings from parallel research pass. Treat as inspiration, not literature review.

---

## A-Evolve (A-EVO-Lab / Amazon, 2026)

**What it does**: Open-source framework for automated agent evolution. Positions itself as "the PyTorch for agentic AI" — a universal substrate that turns any agent into a continuously self-improving agent with zero manual harness engineering. Three lines of code, pluggable everything: bring your own agent (BYOA), benchmark (BYOE), evolution algorithm (BYO-Algo). Launched by Henry Lu and collaborators at Amazon (February 2026).

**The file-system contract**: All evolvable agent state lives in a standardized workspace directory:
```
my_agent/
├── manifest.yaml           # Identity and entrypoint
├── prompts/system.md       # System prompt
├── skills/                 # SKILL.md files (dynamic library)
├── tools/                  # Tool configurations
└── memory/                 # Episodic + semantic (JSONL)
```
Evolution engines mutate these files via LLM-driven operations without needing to understand agent internals. The agent binary is untouched. The contract is the directory layout, not an API. Any agent that reads configuration from files is evolvable — no instrumentation, no hooks, no agent-side changes required.

**The evolution loop (5 phases)**: Solve → Observe → Evolve → Gate → Reload.
- *Solve*: Agent processes a batch of tasks (black-box execution)
- *Observe*: Trajectories and benchmark feedback collected into structured logs
- *Evolve*: Engine analyzes observations and mutates workspace files via LLM
- *Gate*: Mutations evaluated on holdout tasks; any regression triggers git rollback
- *Reload*: Agent reinitializes from (possibly rolled-back) workspace

Every accepted mutation receives a git tag (`evo-1`, `evo-2`, ...) providing a full audit trail. The before/after is concrete: seed workspace = generic 20-line system prompt + empty skills and memory. After evolution on MCP-Atlas: five new targeted skills, populated episodic memory, 79.4% benchmark score.

**Results** (all with Claude Opus 4.6 baseline):

| Benchmark | Score | Rank | Improvement |
|-----------|-------|------|-------------|
| MCP-Atlas | 79.4% | #1 | +3.4pp |
| SWE-bench Verified | 76.8% | ~#5 | +2.6pp |
| Terminal-Bench 2.0 | 76.5% | ~#7 | +13.0pp |
| SkillsBench | 34.9% | #2 | +15.2pp |

**Reference algorithms** (four production implementations):
- *adaptive_evolve*: Per-claim feedback analysis + meta-learning. Best for tool-calling tasks (MCP-Atlas).
- *adaptive_skill*: LLM-driven workspace mutation with bash tool access. Best for CLI/Docker tasks.
- *skillforge*: LLM-driven mutation + Evolutionary Generality Loss (EGL) gating. Best for skill discovery.
- *guided_synth*: Memory-first evolution + LLM-guided intervention synthesis. Best for general-purpose (SWE-bench).

The point: the evolution algorithm is itself a swappable component. Different strategies for different domains.

**Key insight**: You don't need to understand the agent to evolve it. The file-system contract decouples evolution infrastructure from agent architecture entirely. This inverts the typical approach — instead of designing a system that knows how to evolve itself, you design a system that externalizes its evolvable state and then evolve the externalized state. The agent is a black box; only its inputs (workspace files) are white box.

**What it lacks**:
1. *No coherence guarantees between files.* The workspace is a flat bag of files. Two mutations to interacting files (e.g., a skill referenced in the system prompt) can conflict. There is no schema that enforces cross-file consistency. This is A-Evolve's equivalent of the M2 spec fragmentation problem — except A-Evolve doesn't have a spec.
2. *Binary gating, no MAP-Elites.* A mutation is accepted or rejected wholesale based on holdout performance. A mutation that improves tool-calling by 5pp but degrades code generation by 1pp passes or fails as one thing. No dimensional tracking, no quality-diversity archive, no "best mutation for this niche." Information is lost.
3. *No Red Queen dynamics.* The holdout task set is fixed. A mutation that perfectly overfits to the holdout distribution will be promoted. There is no mechanism that makes the tests harder as the agent gets better.
4. *Single lineage, no population.* A-Evolve is a hill-climber with rollback, not a population-based evolutionary system. It may converge to a local optimum without the diversity mechanism to escape.

**Cambrian relevance**: A-Evolve is the most direct empirical validation of the core M2 bet: LLM-driven mutation of text artifacts, gated by external evaluation, produces measurable improvement across diverse benchmarks. The framework works. But A-Evolve evolves the agent's *operational state* (prompts, skills, memory), not its *specification*. The M2 proposal's thesis — that what evolves should be the genome, not the phenotype — remains uninvestigated by this work. A-Evolve is what happens if you apply evolutionary pressure to Prime's runtime context rather than to the spec Prime reads. Cambrian's inversion is still unique.

---

## "Position: Agentic Evolution is the Path to Evolving LLMs" (A-EVO-Lab, arXiv 2602.00359, Feb 2026)

**Core thesis**: Static training (pre-training, RLHF, fine-tuning) cannot keep pace with deployment environment change. Models trained on yesterday's benchmarks drift from today's tasks. Rather than continuous retraining, the paper argues for autonomous adaptation mechanisms at deployment time — treating improvement as goal-directed optimization over persistent system state, not parameter updates.

**The evolution-scaling hypothesis**: Adaptation quality scales with compute allocated to evolution, analogous to how reasoning quality scales with inference compute (chain-of-thought) and capability scales with training compute. The paper frames this as a *third scaling axis*:
- Training compute → capability
- Inference compute → reasoning
- Evolution compute → adaptation

More evolution cycles, more mutations tried, better adapted agents. The hypothesis suggests a compute-scaling law for adaptation: doubling the evolution budget should produce measurably better-adapted agents, just as doubling inference compute improves chain-of-thought performance.

**Key insight**: The paper's framing implies evolution is not a one-time optimization phase — it is a continuous process that runs in parallel with deployment. The agent that was SOTA last month is not SOTA today, not because the model degraded but because the task distribution shifted. Evolution compute is the ongoing budget for staying current.

**What it lacks**: Position paper, not an empirical paper. The evolution-scaling hypothesis is stated but not rigorously tested. There is no characterization of the compute-to-adaptation curve's shape: does it scale linearly? Log-linearly? Where is the knee? Does it saturate? These are empirical questions the paper gestures at but doesn't answer. The data from A-Evolve benchmarks shows evolution works — it doesn't prove scaling.

**Cambrian relevance**: The evolution-scaling hypothesis is a testable prediction for M2. Cambrian's Loop 2 campaign system is evolution compute. The M2 experiment (file 06, "The Experiment M2 Must Run") can be formulated as a scaling test: does a 10-campaign evolved spec outperform a 1-campaign evolved spec, which outperforms the baseline? If yes, the hypothesis is supported and more compute = better specs. If the curve flattens after 3 campaigns, we've found the knee. Either outcome is informative.

---

## A-Evolve vs AlphaEvolve: A Disambiguation

Both names contain "evolve." Both use LLMs as mutation operators. Both are discussed in this research. They are different projects with different goals.

| Dimension | AlphaEvolve (DeepMind, 2025) | A-Evolve (A-EVO-Lab/Amazon, 2026) |
|---|---|---|
| **What evolves** | Programs / algorithms (code) | Agent workspace files (prompts, skills, memory) |
| **Target artifact** | Mathematical/algorithmic programs | Agent behavior in benchmark tasks |
| **Selection mechanism** | MAP-Elites population, multi-niche | Binary holdout gate, single lineage |
| **Mutation form** | LLM produces diffs to code | LLM mutates workspace files |
| **Evaluation** | Scoring function (fixed objective) | Benchmark performance (fixed task set) |
| **Domain** | Mathematical optimization | General agentic tasks |
| **Key innovation** | Dual model (proposer + screener) | Pluggable architecture (BYOA/BYOE/BYO-Algo) |
| **Audit** | Not emphasized | Git-tagged mutations, rollback |

AlphaEvolve is closer to what Cambrian's Spec Mutator would do: an LLM proposes diffs to a structured artifact (code / spec), a scoring function evaluates results, and MAP-Elites maintains diversity. A-Evolve is closer to what an operational Cambrian agent might look like if it evolved its own runtime state between runs. The M2 proposal's "dual model" (file 06, §The Dual Model) draws on AlphaEvolve's architecture, not A-Evolve's.

---

## Cambrian Implications

### The workspace model vs the spec model

A-Evolve's workspace is a bag of independent files. Each can be mutated in isolation. This makes mutation *cheap* — edit one file, reload, test. But it sacrifices *coherence*. A skill file and a system prompt that jointly describe an approach to tool-calling can drift apart across separate mutations. There is no enforcing mechanism.

Cambrian's spec is a single structured document with internal cross-references and formal invariants (file 06, §The Identity Anchor). Mutation is *more expensive* — any change must preserve structural integrity across the whole document. But coherence is architectural: the Spec Mutator operates on one artifact, Prime reads one artifact, the invariant section cannot be touched. You pay a ceremony cost to get a coherence guarantee.

A-Evolve's Type 2 mutation (section transplant) is the closest analog to A-Evolve's file-level mutations — taking a section from one spec variant and grafting it into another. But the grafting happens within one document, not across an arbitrary number of files. The M2 proposal's composability claim ("take section 3 from Spec A and section 7 from Spec B") relies on this monolithic structure.

The workspace/spec distinction is a real trade-off, not A-Evolve doing it wrong. Different goals: A-Evolve optimizes for plug-and-play generality; Cambrian optimizes for legibility and lineage integrity.

### Gated evolution vs the Test Rig

A-Evolve's holdout gate is binary: the mutation improves benchmark performance or it doesn't. This is cheap to evaluate and simple to reason about. But it collapses the fitness landscape. A mutation that improves SWE-bench correctness by 3pp at the cost of 20% more tokens would pass if the net performance metric is positive — even if that tradeoff is bad for the campaign budget.

Cambrian's Test Rig produces a 15-dimension fitness vector (file 06, §Loop 2: Campaign). This preserves information: a generation that fails to start but passes build and unit tests contributes to different MAP-Elites cells than a generation that starts but fails health checks. The proposal explicitly uses this (§Hindsight Relabeling) — non-viable generations still update the archive on the dimensions they satisfied.

The implication: A-Evolve's gating is a *fast pre-screen*, not a full fitness evaluation. Cambrian could adopt both — a fast binary gate (did the artifact start? did it pass basic tests?) *before* the full 15-dimension evaluation. Gate on Tier 0 viability; compute the full vector only for mutations that pass. This maps to the M2 proposal's "screener model" concept, but as a deterministic pre-filter rather than a model call.

### What A-Evolve validates in the M2 proposal

Four things A-Evolve's results confirm:

1. **LLM-driven text mutation under selection pressure works.** This is the foundational M2 bet. A-Evolve proves it produces competitive benchmark performance across four diverse domains. The mechanism is not theoretical.

2. **Git-based mutation versioning is the right audit mechanism.** Both A-Evolve (evo-N tags) and M2 (spec lineage as git history) converge on git as the evolution ledger. This is not coincidental — git provides atomic commits, rollback, branching, and human-readable diffs for free. The evolution history *is* the git history.

3. **External evaluation as the fixed selection signal.** A-Evolve never lets the evolution engine evaluate its own mutations — the holdout benchmark is external and fixed. This confirms the M2 invariant: the Test Rig is immutable, Prime cannot evaluate its own viability, the Supervisor manages git (file 06, §What This Produces...). Darwin Godel Machine's failure mode — the agent touches its own evaluator — is structurally prevented in both systems.

4. **Agent/evolution separation is sound.** A-Evolve's workspace contract proves you can evolve an agent without modifying it. M2's equivalent: the spec evolves, Prime does not. Prime is the invariant constructor; the spec is the thing under selection. The constructor/genome separation is architecturally validated.

### What A-Evolve challenges in the M2 proposal

Four things A-Evolve's simplicity should prompt us to question:

1. **Single-lineage may be sufficient for early stages.** A-Evolve achieves top-tier rankings with no population, no MAP-Elites, no islands — just hill-climbing with rollback. M2's three-loop architecture with a MAP-Elites archive (file 06, §The Spec Population and MAP-Elites) adds significant complexity. A-Evolve's results suggest the population machinery may be premature for Stage 1. Start with single-lineage + rollback; add MAP-Elites when you observe local optima plateaus that require diversity to escape.

2. **The BYO-Algo pattern is worth borrowing.** M2's Stage 1 commits to three mutation types (refinement, transplant, restructuring). A-Evolve's four reference algorithms — each optimized for a different domain — suggest the mutation strategy itself should be pluggable. The first implementation should make the evolution strategy a configurable parameter, not hardcode Type 1-3.

3. **The dual-model screener may be over-engineering for Stage 1.** M2's dual model (Opus mutates, Sonnet screens) adds orchestration complexity and cost. A-Evolve achieves strong results with no screener — just run the holdout gate. For M2 Stage 1, consider running the holdout gate (Tier 0 viability on one generation) as the screener instead of a model call. The Test Rig is faster and cheaper than a Sonnet call.

4. **The flat 5-phase loop is simpler than three nested loops.** A-Evolve's Solve → Observe → Evolve → Gate → Reload beat multiple baselines. M2's Loop 3 → Loop 2 → Loop 1 nesting (file 06, §The Architecture: Three Loops) is the right *eventual* structure, but the M2 Stage 1 plan (file 06, §What to Build First) could be even flatter: run campaigns sequentially, mutate between them, measure whether the next campaign improves. Add the meta-loop when the flat structure shows convergence problems.

### What to adopt from A-Evolve

- **Git-tagged spec mutations.** The `evo-N` tagging convention is concrete and useful. Every spec mutation should produce a tagged commit. The spec lineage is navigable: `git log --oneline spec/` shows the evolutionary history; `git show evo-5` shows exactly what changed.

- **Pluggable mutation strategies.** Make the Spec Mutator's mutation type a parameter, not a hardcoded decision tree. Different tasks may require different mutation strategies.

- **Tiered gating.** A fast binary pre-screen (does the artifact build and start?) before the full 15-dimension evaluation. Cheap to run, eliminates clearly broken mutations before spending campaign budget.

- **The before/after framing for mutation evaluation.** A-Evolve's concrete before/after (20-line prompt → 5 targeted skills) is the right unit of measurement. For spec evolution, the equivalent is: before mutation = N fitness dimensions, after mutation = M fitness dimensions. Track this per mutation type to understand which mutation types are useful.

### What doesn't apply

- **BYOA (bring your own agent).** Cambrian's "agent" is Prime, which is tightly coupled to the spec format. Making Prime swappable is not a current goal. The invariant is that Prime reads a spec and produces a codebase — the interface is fixed.

- **Decomposed skills and memory as separate files.** Cambrian's spec is intentionally monolithic. Splitting it into independent skill files and memory files would break the cross-reference structure that makes M2's section transplant mutations coherent.

- **Online adaptation.** A-Evolve's Reload phase lets the agent pick up workspace changes mid-session. Cambrian is generational — each generation starts from scratch from the spec. This is a feature: clean generation boundaries allow clean fitness measurement. Online adaptation would blur what caused what.

- **Flat holdout gating as the only selection criterion.** A-Evolve's binary gate is appropriate when you have a large benchmark task pool. Cambrian's Test Rig runs deterministic infrastructure tests, not sampled benchmark tasks. The 15-dimension fitness vector is not a benchmark score — it's a structural property of the artifact. Flattening it to binary would discard the MAP-Elites information that makes the archive useful.

---

## Cross-System Patterns

1. **File-system contracts work as mutation surfaces.** A-Evolve demonstrates that externalizing evolvable state to files and mutating those files via LLM is sufficient for competitive benchmark performance. This is the simplest possible mutation surface. Cambrian's spec-as-genome is a file-system contract with one file. The simpler the surface, the easier it is to reason about what changed and why.

2. **Single-lineage + rollback is a viable starting point.** A-Evolve achieves #1 on MCP-Atlas without populations, MAP-Elites, or diversity mechanisms. Population machinery is valuable eventually but premature at Stage 1. Hill-climbing with rollback is not a degenerate case — it's a strong baseline. Don't add complexity until you observe the local optima that require it.

3. **Git is the evolution ledger.** Both A-Evolve and the M2 proposal independently converge on git as the versioning mechanism for mutations. Atomic commits, rollback, branching, and human-readable diffs are what you need from an evolution ledger. No need to build a separate versioning system.

4. **The spec/workspace distinction is Cambrian's differentiator.** A-Evolve evolves operational state (prompts, skills). AlphaEvolve evolves implementations (code). FunSearch evolves functions. Darwin Godel Machine evolves source code. Every system we've studied mutates the *phenotype* or something close to it. The M2 proposal's claim (file 06, §The Thesis): "Cambrian should invert this. The spec is the genome. What evolves is the genome." — remains uninvestigated by this work. No published system has demonstrated LLM-driven evolution of a structured specification document that is then used to construct an implementation. That's the gap Cambrian M2 is positioned to fill.

5. **Evolution as a third scaling axis changes the resource planning model.** If the evolution-scaling hypothesis holds, M2 campaigns are not a fixed cost — they're an investment that compounds. More campaigns = better specs. The M2 budget allocation question ("how many campaigns per spec variant?") is not just an engineering decision; it's a scaling decision analogous to choosing training compute. The M2 Stage 1 experiment should measure the shape of the improvement curve, not just whether improvement occurs.

---

**References**: arXiv 2602.00359 (Feb 2026). Code: github.com/A-EVO-Lab/a-evolve. Launch thread: x.com/HenryL_AI/status/2037602570433388816 (Henry Lu, Amazon, Feb 2026).
