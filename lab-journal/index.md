# Cambrian Lab Journal — Master Index

This is the central Table of Contents for the entire lab journal, following Howard M. Kanare’s guidelines in *Writing the Laboratory Notebook*.  
It makes the notebook navigable years later and serves as the front-matter every good notebook requires.

**Last updated:** 2026-04-09
**Total entries:** 40 (plus this index)
**How to maintain:** Add a new row every time you create a journal file. Keep the table sorted chronologically.

| Date          | File                                      | Key Topics                                                                 | Milestone / Bead / Phase                  |
|---------------|-------------------------------------------|----------------------------------------------------------------------------|-------------------------------------------|
| 2026-03-21   | [journal-2026-03-21.md](journal-2026-03-21.md) | Bootstrap spec, language decision, tooling setup                          | Project bootstrap                         |
| 2026-03-23   | [journal-2026-03-23.md](journal-2026-03-23.md) | README + lab journal updates for SPEC-005                                 | SPEC-005 baseline                         |
| 2026-03-24   | [journal-2026-03-24.md](journal-2026-03-24.md) | Hyperagents analysis, M2 design intent                                    | M2 early design                           |
| 2026-03-24b  | [journal-2026-03-24b.md](journal-2026-03-24b.md) | Fix 10 spec issues blocking Phase 0 implementation                       | Phase 0 blockers resolved                 |
| 2026-03-24c  | [journal-2026-03-24c.md](journal-2026-03-24c.md) | Spec updates for Phase 0 — Python 3.14, artifacts repo                   | Phase 0 tooling finalized                 |
| 2026-03-24d  | [journal-2026-03-24d.md](journal-2026-03-24d.md) | Phase 0 + Stage 1 lessons learned                                         | Phase 0 / Stage 1 review                  |
| 2026-03-24e  | [journal-2026-03-24e.md](journal-2026-03-24e.md) | README update, CLAUDE.md Python version fix                               | Tooling & documentation                   |
| 2026-03-26   | [journal-2026-03-26.md](journal-2026-03-26.md) | Fix `/stats` generation semantics (v0.10.3)                               | Stats subsystem fix                       |
| 2026-03-27   | [journal-2026-03-27.md](journal-2026-03-27.md) | Afternoon session — first successful autonomous loop                      | First autonomous loop                     |
| 2026-03-29   | [journal-2026-03-29.md](journal-2026-03-29.md) | M1 first autonomous loop                                                  | M1 milestone achieved                     |
| 2026-03-30   | [journal-2026-03-30.md](journal-2026-03-30.md) | Pre-M2 quality hardening: spec & code review, 87 new integration tests, path-traversal fix, anti-cheating verification layers (M1/M2) | M1 complete → Pre-M2 hardening            |
| 2026-03-30b  | [journal-2026-03-30b.md](journal-2026-03-30b.md) | CLAUDE.md agent instructions (+108 lines), Docker pip → uv switch | cambrian-yq8, cambrian-luv                |
| 2026-03-30c  | [journal-2026-03-30c.md](journal-2026-03-30c.md) | Layer 1 spec-vector evaluation in Test Rig; container isolation hardening (separate /output mount, process group kill); Kanare journal compliance | cambrian-4do, cambrian-2cf |
| 2026-03-31   | [journal-2026-03-31.md](journal-2026-03-31.md) | M2 infrastructure: test-quality fitness dims + discount weights; campaign runner; spec diff + section attribution | cambrian-mw0, cambrian-9ic, cambrian-evw |
| 2026-03-31b  | [journal-2026-03-31b.md](journal-2026-03-31b.md) | Spec grammar + mutation validation; Type 1 mutator (LLM+grammar retry); mini-campaign screener; BO loop (GP-EI) | cambrian-2e5, cambrian-7cc, cambrian-3sb, cambrian-yy5 |
| 2026-03-31c  | [journal-2026-03-31c.md](journal-2026-03-31c.md) | Entanglement monitor (Clune modularity metric); adaptive Tier X test generation (Absolute Zero Reasoner-inspired) | cambrian-v3w, cambrian-3qo |
| 2026-03-31d  | [journal-2026-03-31d.md](journal-2026-03-31d.md) | Spec vs code audit: 7 undocumented modules, GP-EI vs MAP-Elites, 14 missing env vars, dual-model→grammar | Pre-M2 spec alignment |
| 2026-03-31e  | [journal-2026-03-31e.md](journal-2026-03-31e.md) | Deep integration audit: campaign promote/rollback, artifact-hash, dead code (adaptive_tests, entanglement), 13 new beads | Pre-M2 integration gaps |
| 2026-03-31f  | [journal-2026-03-31f.md](journal-2026-03-31f.md) | P1/P2/P3 bug sprint: 13 beads closed; artifact-hash, promote/rollback, adaptive_tests, entanglement, file locking, git lock, completed timestamp | 159/160 beads closed; M2-ready |
| 2026-03-31g  | [journal-2026-03-31g.md](journal-2026-03-31g.md) | M2 first run attempt; streaming bug fix (3 files); full spec compliance audit: 11 Tier 1 bugs, 5 Tier 2 gaps, 17 new beads | cambrian-9kn, cambrian-i8o; spec audit |
| 2026-04-01   | [journal-2026-04-01.md](journal-2026-04-01.md) | Fix 5 P1 spec bugs: token budget semantics, model escalation, uv pip, rollback tag suffixing, scoped git add | cambrian-2sg, cambrian-5tym, cambrian-7djj, cambrian-pom1, cambrian-h4j3 |
| 2026-04-01   | [journal-2026-04-01-summary.md](journal-2026-04-01-summary.md) | **Summary**: project arc, architectural insights, recurring bug patterns, key decisions, anti-cheating layers, test evolution, budget, process lessons | All sessions Mar 21 - Apr 01 |
| 2026-04-01b  | [journal-2026-04-01b.md](journal-2026-04-01b.md) | Fix 11 P2/P3 spec compliance bugs; fix 3 M2 launch blockers (token limit, stale Docker image, /venv permissions); first viable M2 generation (gen 15) | cambrian-dzf M2 Stage 1 |
| 2026-04-01c  | [journal-2026-04-01c.md](journal-2026-04-01c.md) | Gen-16 diagnosis + spec path fix (v0.14.2); **full viability analysis** of all 16 gens — failure classification, Python brittleness assessment, recommendations for next run | cambrian-u3ig |
| 2026-04-02   | [journal-2026-04-02.md](journal-2026-04-02.md) | Smoke-import check + parser vectors; campaign gens 17–19 (0/3 viable); manifest validation gate for entry.start; structlog + test-import spec guidance; spec v0.14.3 | cambrian-tbnt, cambrian-jaxs, cambrian-0p3w, cambrian-x44c, cambrian-an2x |
| 2026-04-03   | [journal-2026-04-03.md](journal-2026-04-03.md) | Campaign gens 20–29 (0/10 viable); prime_runner.py hardcoded wrong start cmd; system prompt enriched; Test Rig diagnostics feedback to next gen | cambrian-7g1a, cambrian-hyuy, cambrian-jpoq, cambrian-xmtq |
| 2026-04-03b  | [journal-2026-04-03b.md](journal-2026-04-03b.md) | System analysis: spec/code gaps, security findings, new beads | cambrian-mthk, cambrian-x27f, cambrian-g6ne, cambrian-1iiz |
| 2026-04-03c  | [journal-2026-04-03c.md](journal-2026-04-03c.md) | P0–P2 fix sweep: path traversal, manifest validation, contract substitution, spec alignment | cambrian-mthk, cambrian-x27f, cambrian-g6ne, cambrian-1iiz |
| 2026-04-03d  | [journal-2026-04-03d.md](journal-2026-04-03d.md) | Cleared un-awaited coroutine warnings in tests | cambrian-8why |
| 2026-04-03e  | [journal-2026-04-03e.md](journal-2026-04-03e.md) | Test Rig structlog adoption | cambrian-ad3n |
| 2026-04-03f  | [journal-2026-04-03f.md](journal-2026-04-03f.md) | Run history snapshot from generations.json | N/A |
| 2026-04-03g  | [journal-2026-04-03g.md](journal-2026-04-03g.md) | Gen0 confidence campaign (N=1) | cambrian-2b6k |
| 2026-04-03h  | [journal-2026-04-03h.md](journal-2026-04-03h.md) | Gen0 confidence campaign repeat (N=1) | N/A |
| 2026-04-03i  | [journal-2026-04-03i.md](journal-2026-04-03i.md) | Gen0 confidence campaign failed (N=1) | N/A |
| 2026-04-03j  | [journal-2026-04-03j.md](journal-2026-04-03j.md) | Cost estimation script added | cambrian-qhb6 |
| 2026-04-03k  | [journal-2026-04-03k.md](journal-2026-04-03k.md) | Gen0 confidence campaign (Opus) | N/A |
| 2026-04-03l  | [journal-2026-04-03l.md](journal-2026-04-03l.md) | Preserve failed gen0 artifacts | cambrian-8d6x |
| 2026-04-03m  | [journal-2026-04-03m.md](journal-2026-04-03m.md) | Gen0 confidence campaign (Opus, rerun) | cambrian-8d6x |
| 2026-04-03n  | [journal-2026-04-03n.md](journal-2026-04-03n.md) | Opus vs Sonnet quality comparison | N/A |
| 2026-04-04   | [journal-2026-04-04.md](journal-2026-04-04.md) | Confidence campaign gens 36–38; structlog missing from Docker image; container retention for diagnostics | cambrian-7krd, cambrian-p0z0, cambrian-u38m |
| 2026-04-04b  | [journal-2026-04-04b.md](journal-2026-04-04b.md) | 10-gen campaign (8/10 viable); differential code quality analysis; model cost table; spec v0.14.4 (monotonic uptime, backoff example) | CAMBRIAN-SPEC-005 v0.14.4 |
| 2026-04-04c  | [journal-2026-04-04c.md](journal-2026-04-04c.md) | Phenotypic distiller: AST-based post-campaign analysis, auto-propose spec patches from differential code patterns | cambrian-suxw |
| 2026-04-09   | [journal-2026-04-09.md](journal-2026-04-09.md) | Rig hardening: structlog lint gate, syntax check, code exemplars in system prompt, failed_context threading, Docker pre-install | cambrian-2sd9 |

---

**Attachments / Supporting materials** (add rows as needed)  
None yet — when you add plots, screenshots, or logs, create an `attachments/` subfolder and link them here with date and description.

**Related project files** (for quick reference)
- [CAMBRIAN-SPEC-005](../spec/CAMBRIAN-SPEC-005.md)
- [BOOTSTRAP-SPEC-002](../spec/BOOTSTRAP-SPEC-002.md)
- [AGENTS.md](../AGENTS.md), [CLAUDE.md](../CLAUDE.md)

**Archival note:** Every quarter, generate a PDF snapshot of the entire `lab-journal/` folder (including this index) and tag a GitHub release for immutable long-term storage.
