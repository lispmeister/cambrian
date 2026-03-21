# Spec Style Guide

How to write implementation specs for CAMBRIAN. These specs are the primary input to agentic coding workflows — an agent reads the spec and implements it end-to-end. A spec that is underspecified produces drift. A spec that is overspecified produces rigidity. The goal is to land in the middle: enough detail that the agent won't stray, enough flexibility that it can make local decisions.

---

## Why This Matters

The spec is the single source of truth during implementation. When context compaction happens (and it will), the agent re-reads the spec to stay on track. If the spec is vague, the agent will fill in the blanks with its own assumptions. If the spec is precise, the agent will follow the path.

A good spec is a contract between the architect and the agent. The architect decides *what* and *why*. The agent decides *how* (within the constraints the spec defines).

---

## Document Structure

Every spec should follow this skeleton. Sections can be omitted if genuinely not applicable, but the order should be preserved.

### 1. Frontmatter

```yaml
---
date: YYYY-MM-DD
author: name <email>
title: Short descriptive title
tags: [project, area, type]
---
```

Date, author, and title are required. Tags help with discovery.

### 2. Overview

2–4 paragraphs. What is being built and why. State the high-level approach in plain language. A reader should understand the shape of the solution after this section without reading anything else.

End with a summary of the execution path — the sequence of things that happen at runtime. Use a bullet list, not a diagram.

### 3. Problem Statement

What's wrong today. Be specific — name the concrete symptoms, not abstract dissatisfactions. Each problem should be something the reader can verify by looking at the current system.

### 4. Goals

Bulleted list. Each goal is a concrete, testable outcome. Not aspirations — things you can check.

Good: "Define one standard request envelope for all resolver types."
Bad: "Improve extensibility."

### 5. Non-Goals

Equally important. What this spec explicitly does NOT attempt. Prevents scope creep during implementation. Each non-goal should be something a reasonable agent might attempt if not told otherwise.

### 6. Design Principles

The rules that govern decisions throughout the spec. These are the tiebreakers — when the agent faces an ambiguous choice during implementation, it should consult these.

Each principle should be:
- A short declarative statement (the rule)
- Followed by a brief explanation (the rationale)
- Optionally followed by examples

Structure as subsections (`### Principle Name`) when principles need more than a sentence of explanation.

### 7. Model / Core Abstractions

Define the nouns of the system. What are the durable abstractions? Name them, define them, state their relationships.

This section should survive feature churn. If the abstractions are right, the implementation details can change without rewriting the spec.

Use a flat list with definitions first, then elaborate relationships. Don't bury definitions inside prose.

### 8. Contracts and Schemas

The precise data shapes that cross boundaries. This is the section that prevents the most drift.

Rules:
- Use JSON or YAML for schema examples — not prose descriptions of fields.
- Include a concrete example for every schema, not just the type definition.
- State which fields are required vs optional.
- State validation rules (e.g., "MUST be unique", "MUST be one of [...]").
- Name the direction: who sends this, who receives it.

```json
{
  "version": "v1",
  "status": "resolved|unresolved|forbidden",
  "output": { ... },
  "mutations": { ... }
}
```

Follow each schema with a **Notes** section explaining non-obvious fields.

### 9. Lifecycle / Phases

If the system has a runtime sequence, define it as named phases. Each phase gets:
- A name (one word)
- A one-line description
- What it consumes and produces
- What can go wrong

```
resolve → authorize → materialize → notify
```

Keep the phase count small (3–7). If you have more, some of your phases are implementation steps, not lifecycle phases.

### 10. Failure Modes

What happens when things go wrong. For each failure:
- The trigger condition
- The system's response
- Whether it's retryable

This section is commonly underspecified. If the agent doesn't know what to do on failure, it will either crash or invent behavior. Neither is good.

### 11. Configuration

Table format. One row per config value.

| Variable | Required | Default | Purpose |
|----------|----------|---------|---------|
| `FOO`    | Yes      | —       | What it controls |

State the precedence order if multiple sources exist (env var > config file > default).

### 12. Examples

Concrete, end-to-end examples that show the system working. Not unit-test-level — scenario-level. "Given this input, through these phases, producing this output."

Include at least two examples:
- The happy path (simplest valid case)
- An interesting edge case

Use realistic data, not `foo`/`bar`/`baz`.

### 13. Migration Plan / Implementation Phases

If the spec replaces or evolves existing behavior, define the migration as numbered phases. Each phase should:

- State what it adds or changes
- State what it preserves (backward compatibility)
- Be independently deployable and testable
- Have a clear "done" condition

Phases should be ordered by dependency, not by importance. Phase N+1 may depend on Phase N but not vice versa.

### 14. Validation / Acceptance Criteria

How to verify the implementation is correct. Two kinds:

**Mechanical checks** — things a test suite can verify:
- "GET /health returns 200"
- "Creating with a duplicate ID returns 409"

**Behavioral checks** — things that require judgment:
- "Error messages surface the correct principal and failure reason"
- "Config validation fails fast with an actionable message"

List both. The agent will implement the mechanical checks as tests. The behavioral checks inform code review.

### 15. References

Links to related specs, prior art, external documentation. Each with a one-line description of relevance.

---

## Writing Style

### Be declarative, not procedural

Good: "Resolvers MUST return a status field with one of: resolved, unresolved, forbidden."
Bad: "When you implement the resolver, make sure to add a status field."

The spec describes the system as it should be, not the steps to build it. The agent figures out the steps.

### Use RFC 2119 keywords

- **MUST** — non-negotiable requirement
- **SHOULD** — strong preference, may be overridden with justification
- **MAY** — optional, agent decides

Use these consistently. They signal priority to the agent.

### Name things once, use them everywhere

Define a term in the Model section, then use it without re-explaining. Inconsistent naming is the #1 source of agent confusion.

Bad: calling the same thing "artifact", "bundle", "package", and "output" in different sections.

### Show, don't just tell

For every abstract rule, include a concrete example. Agents follow examples more reliably than prose descriptions.

### State the negative

"Resolvers MUST NOT mutate fields outside the allowed mutation surface." Negative constraints prevent the most common drift patterns.

### Keep sections independent

Each section should be readable on its own after context compaction. Don't rely on "as described above" — name the specific section or repeat the key fact.

---

## Spec Sizing

A spec should cover one coherent unit of work. Rules of thumb:

| Scope | Spec size | Implementation time |
|-------|-----------|-------------------|
| Single feature or contract | 200–500 lines | 1 session |
| Subsystem or component | 500–1500 lines | 2–5 sessions |
| Architecture or framework | 1500–3000 lines | Multiple PRs |

If the spec exceeds 3000 lines, split it. If it's under 200 lines, it's probably underspecified.

---

## Checklist Before Handing to an Agent

- [ ] Could an agent implement this without asking clarifying questions?
- [ ] Are all data shapes defined with concrete JSON/YAML examples?
- [ ] Are failure modes covered for every external interaction?
- [ ] Are non-goals stated to prevent scope creep?
- [ ] Is every named abstraction defined exactly once?
- [ ] Are there at least two end-to-end examples?
- [ ] Does each migration phase have a clear "done" condition?
- [ ] Are RFC 2119 keywords used consistently?
- [ ] Can each section survive context compaction independently?

If any answer is no, the spec needs more work before implementation begins.

---

## Execution Workflow

Once a spec passes the checklist above, hand it to an agent with the following process. This is the standard CAMBRIAN implementation workflow.

### The Prompt

```
1. Implement the given plan end-to-end. If context compaction happens,
   make sure to re-read the plan to stay on track. Finish to completion.
   If there is a PR open for the implementation plan, do it in the same PR.
   If there is no PR already, open PR.

2. Once you finish implementing, make sure to test it. This will depend
   on the nature of the problem. If needed, run local smoke tests, spin up
   dev servers, make requests and such. Try to test as much as possible,
   without merging. State explicitly what could not be tested locally and
   what still needs staging or production verification.

3. Push your latest commits before running review so the review is always
   against the current PR head. Run codex review against the base branch:
   `codex review --base <branch_name>`. Use a 30 minute timeout on the
   tool call available to the model, not the shell `timeout` program.
   Do this in a loop and address any P0 or P1 issues that come up until
   there are none left. Ignore issues related to supporting legacy/cutover,
   unless the plan says so. We do cutover most of the time.

4. Check both inline review comments and PR issue comments dropped by
   Codex on the PR, and address them if they are valid. Ignore them if
   irrelevant. Ignore stale comments from before the latest commit unless
   they still apply. Either case, make sure that the comments are replied
   to and resolved. Make sure to wait 5 minutes if your last commit was
   recent, because it takes some time for review comment to come.

5. In the final step, make sure that CI/CD is green. Ignore the fails
   unrelated to your changes, others break stuff sometimes and don't fix
   it. Make sure whatever changes you did don't break anything. If CI/CD
   is not fully green, state explicitly which failures are unrelated
   and why.

6. Once CI/CD is green and you think that the PR is ready to merge,
   finish and give a summary with the PR link. Include the exact
   validation commands you ran and their outcomes. Also comment a final
   report on the PR.

7. Do not merge automatically unless the user explicitly asks.
```

### The Loop

1. **Write the spec.** Architect + agent collaborate until the spec passes the checklist.
2. **Hand off.** Give the agent the spec and the prompt above.
3. **Agent implements.** End-to-end, with self-review and CI verification.
4. **Architect skims.** Read the diff for code smell. If nothing is off, tell the agent to merge.
5. **Test on staging.** Find issues, file them, repeat from step 1 for each issue or new feature.

The spec is never "done" — it's the living contract that gets refined through this loop.

---

## Anti-Patterns

**The wishlist spec.** Lists desired outcomes without defining contracts or schemas. Produces code that vaguely resembles the intent but doesn't match at the boundaries.

**The implementation guide.** Tells the agent exactly which files to create, which functions to write, in which order. Produces brittle code that can't adapt to surprises. Specify *what*, not *how*.

**The novel.** Buries requirements in pages of prose. The agent skims and misses constraints. Use structure (tables, lists, schemas) for anything that must be precise.

**The spec-by-reference.** "See the existing code for how this works." The agent may not find it, may misread it, or the code may change. If it matters, state it in the spec.

**The underspecified boundary.** Defines the happy path in detail, says nothing about errors, edge cases, or configuration. The agent will invent behavior for every gap.
