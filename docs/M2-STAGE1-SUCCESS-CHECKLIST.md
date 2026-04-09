# M2 Stage 1 Success Checklist

Purpose: define a defensible go/no-go decision for whether spec mutation improves outcomes over the current baseline.

## 1) Pre-Run Setup (must complete before measurement)

- [ ] Freeze baseline spec and record hash (`CAMBRIAN-SPEC-005 v0.18.0` + spec hash)
- [ ] Record code version (`git rev-parse HEAD`)
- [ ] Use one fixed runtime configuration for the whole comparison window:
  - model
  - `CAMBRIAN_BO_BUDGET`
  - `CAMBRIAN_CAMPAIGN_LENGTH`
  - `CAMBRIAN_MINI_CAMPAIGN_N`
  - `CAMBRIAN_BO_INITIAL_POINTS`
  - Docker/Test Rig image tag
- [ ] Confirm security invariants are active:
  - no `ANTHROPIC_API_KEY` in Test Rig container
  - `NetworkMode=none`
  - `SecurityOpt=["no-new-privileges:true"]`
  - `CapDrop=["ALL"]`

## 2) Required Sample Size and Structure

- [ ] Run at least **30 full campaigns** total:
  - 10 baseline campaigns
  - 20 mutated campaigns
- [ ] Interleave runs where possible (reduce time-drift bias)
- [ ] Keep all run knobs fixed across baseline vs mutation comparison

## 3) Primary Go/No-Go Criteria

Primary metric: campaign `viability_rate`.

- **GO** if mutated median `viability_rate` is >= baseline median + 0.10 (10 percentage points)
- **NO-GO** if improvement is < 0.05 (5 points)
- **INCONCLUSIVE** if in [0.05, 0.10): run additional campaigns and reassess

Hard fail conditions (automatic NO-GO):

- Any security/compliance regression in enforced invariants
- Spec/wire-format regression that breaks compliance tests
- Non-monotonic generation numbering or invalid BO resume behavior

## 4) Stability Confirmation

- [ ] Select top 3 winning mutated spec hashes
- [ ] Re-run each in fresh campaigns
- [ ] At least 2 of 3 must reproduce improvement trend (not one-off outliers)

## 5) Secondary Metrics (track, do not initially gate)

- `total_duration_ms` (viable runs)
- `test_count`
- `test_pass_rate`
- `spec_vector_pass_rate`
- `baseline_contract_pass_rate`
- failure-stage distribution (`manifest`, `build`, `test`, `start`, `health`)

## 6) Data and Reporting Requirements

- [ ] Use `../cambrian-artifacts/generations.json` as canonical run-history source
- [ ] Store per-campaign summary rows in `docs/templates/m2-stage1-results-template.md`
- [ ] Produce final comparison table (baseline vs mutation medians/means + deltas)
- [ ] Journal evidence in `lab-journal/` including:
  - hypothesis vs measured impact
  - campaign counts
  - spec hashes
  - commit hash

## 7) Minimum Commands (reference)

```bash
# Optional: collect test count snapshot for report
uv run pytest --collect-only -q

# Generate campaign + aggregate tables from generations.json
uv run python scripts/summarize_m2_results.py \
  --baseline-campaign-id <baseline-campaign-id> \
  --markdown-out /tmp/m2-summary.md \
  --json-out /tmp/m2-summary.json

# Run targeted compliance/security checks before and after campaign batches
uv run pytest tests/test_spec_compliance.py tests/test_security.py
```

## Decision Record

- Date:
- Decision: GO / INCONCLUSIVE / NO-GO
- Baseline median viability:
- Mutated median viability:
- Delta:
- Notes:
