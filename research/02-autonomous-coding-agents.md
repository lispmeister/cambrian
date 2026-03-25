# Research: Autonomous Coding Agents

> Raw findings from parallel research pass. Treat as inspiration, not literature review.

---

## The Benchmark Landscape

**SWE-bench** (Jimenez et al., 2023) is the de facto standard: resolve real GitHub issues in Python repos. Variants:
- SWE-bench Full: 2294 issues
- SWE-bench Lite: 300 issues (faster evaluation)
- SWE-bench Verified: 500 human-verified issues

**Top performers (as of early 2026)**:
- Devin 2.0: ~46% (SWE-bench Verified)
- OpenHands + Claude 3.5: ~41%
- SWE-agent + GPT-4: ~12% (original paper) → ~26% (recent runs with better models)
- Agentless (no scaffolding): ~27% — simpler is competitive

**Key observation**: Performance scales with model capability *and* scaffolding quality. The gap between raw model and scaffolded agent is often larger than the gap between model sizes.

---

## SWE-agent (Yang et al., 2024)

**What it does**: Wraps a language model with a designed Agent-Computer Interface (ACI) — a set of commands and feedback mechanisms purpose-built for code editing tasks.

**Core insight**: **ACI design matters more than model choice.** The original paper's key finding: the same model with a well-designed ACI outperforms a larger model with a poor interface. The scaffolding is the product.

**ACI design choices**:
- File viewer with sliding window (not full file dumps)
- Edit commands that show before/after context
- Error messages reformatted for LLM consumption
- Explicit "scratchpad" for planning before acting

**Failure modes documented**:
1. **Localization failure** (most common, ~40%): The agent finds the wrong file or function to edit. It fixes the right thing in the wrong place.
2. **Cascading edits**: One edit breaks tests; the agent tries to fix the broken tests; this breaks more tests; the agent spirals.
3. **Infinite loop**: Agent decides to run tests → tests fail → agent decides to run tests again → ...
4. **Overconfidence**: Agent declares success before running tests.

**Cambrian relevance**: Prime's ACI is its system prompt and output format. The system prompt currently gives Prime a lot of latitude on output structure. Tighter output contracts (like SWE-agent's ACI) would reduce localization-equivalent failures — generating code in the wrong place in the artifact structure.

---

## Devin (Cognition, 2024)

**What it does**: Full software engineering agent with persistent memory, browser, terminal, code editor, and long planning horizon.

**Architecture**: Plan-then-execute with reflection. Creates an explicit task plan before writing any code. Revisits the plan when encountering unexpected states.

**Key innovation**: **Long-horizon coherence**. Devin can maintain context over 100+ steps without losing its original objective. This is a scaffolding achievement, not just a model achievement.

**Failure modes**:
- Scope creep: Devin sometimes "improves" things it wasn't asked to change
- Plan rigidity: The initial plan is hard to abandon even when evidence suggests it's wrong
- Eval gaming: Concerns raised that benchmark performance may not reflect real-world performance

**Cambrian relevance**: Prime's generation horizon is deliberately short (one spec → one codebase). The long-horizon coherence problem Devin solves is less relevant to M1. For M2 (multi-generation evolution), plan coherence across generations becomes important — what is Cambrian "trying to achieve" across 50 generations?

---

## OpenHands / CodeAct (Wang et al., 2024)

**What it does**: Open-source agent framework. Key contribution: **CodeAct** — the insight that code execution is a better action space than JSON tool calls.

**CodeAct principle**: Instead of `{"tool": "bash", "command": "ls -la"}`, the agent writes and executes actual Python code: `import os; print(os.listdir('.'))`. Code is more expressive, composable, and self-documenting than tool call schemas.

**Results**: CodeAct agents outperform tool-call agents on multi-step tasks, with the gap widening as task complexity increases.

**Key insight**: The action space determines what the agent can express. Code is a superset of tool calls. An agent that reasons in code is reasoning in a richer language.

**Cambrian relevance**: Prime produces code as output. The *process* of generation is currently opaque. If Prime reasoned in intermediate code (generating tests before implementations, generating interfaces before classes), the process itself would be more structured and auditable. This is the CodeAct insight applied to generation.

---

## Agentless (Xia et al., 2024)

**What it does**: Deliberately removes agentic scaffolding. No tool use, no memory, no multi-step planning. Just: localize → generate → validate.

**Results**: ~27% on SWE-bench Lite — competitive with far more complex agents.

**Why it matters**: Complexity has overhead. Every additional step, tool, and feedback loop introduces failure modes. The agentless approach has fewer things that can go wrong. Its failure modes are simpler to diagnose.

**Key insight**: Simplicity isn't just elegant — it's a reliability strategy. Complex agents can perform better on average but have heavier tails (spectacular failures).

**Cambrian relevance**: M1 Prime is essentially agentless by design — one LLM call, deterministic output format, no tool use. This is correct for M1. The research validates this. M2 should add complexity incrementally and justify each addition with measurable improvement.

---

## Failure Mode Taxonomy

Synthesized from SWE-agent, OpenHands, and Devin literature:

| Failure Mode | Frequency | Description |
|---|---|---|
| Localization failure | ~40% | Agent edits the wrong file/function |
| Cascading edit spiral | ~20% | Fix breaks tests; agent tries to fix tests; repeat |
| Semantic drift | ~15% | Agent loses track of original objective over long sessions |
| Overconfidence | ~10% | Agent reports success before validation |
| Infinite loop | ~8% | Agent retries same failing action indefinitely |
| Scope creep | ~7% | Agent modifies things it wasn't asked to change |

**For Cambrian**: Prime's most likely failure modes are (1) generating files in wrong locations (localization) and (2) generating code that compiles but doesn't satisfy the spec's behavioral requirements (overconfidence with no test loop). The Test Rig catches both — but only if Prime generates something runnable at all.

---

## What's Missing from Autonomous Coding Agent Research

1. **No persistent improvement across runs**: Every agent starts fresh. Failures don't inform future attempts. Reflexion adds session-level memory; nobody has cross-session learning without fine-tuning.

2. **No spec-level abstraction**: Agents work at the code level. They don't reason about *what the code should be* — they reason about *how to write specific code*. A spec-level representation would allow more powerful mutations.

3. **Evaluation is fixed**: The benchmark is immutable. Nobody has a system where the test suite grows as the agent improves — Red Queen dynamics. Agents that pass today's tests can be trivially regressed with yesterday's bugs.

4. **Single-agent only**: All these systems run one agent. Nobody (publicly) runs populations of agents and selects over them. The evolutionary step is missing from autonomous coding research.
