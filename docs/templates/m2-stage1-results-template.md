# M2 Stage 1 Results Template

Use this template to capture campaign-level evidence for baseline vs mutation comparisons.

## Run Metadata

- Date range:
- Operator:
- Git commit hash:
- Baseline spec version/hash:
- Mutation spec version/hash (or "multiple"):
- Supervisor/Test Rig image tag:
- Model:
- `CAMBRIAN_BO_BUDGET`:
- `CAMBRIAN_CAMPAIGN_LENGTH`:
- `CAMBRIAN_MINI_CAMPAIGN_N`:
- `CAMBRIAN_BO_INITIAL_POINTS`:

## Campaign Rows

| Group | Campaign ID | Spec Hash | Start Gen | End Gen | N | Viability Rate | Median Total Duration (ms) | Mean Test Count | Mean Test Pass Rate | Mean Spec Vector Pass Rate | Mean Baseline Contract Pass Rate | Dominant Failure Stage | Notes |
|-------|-------------|-----------|-----------|---------|---|----------------|----------------------------|-----------------|---------------------|----------------------------|----------------------------------|------------------------|-------|
| baseline |  |  |  |  |  |  |  |  |  |  |  |  |  |
| mutation |  |  |  |  |  |  |  |  |  |  |  |  |  |

## Aggregated Comparison

| Metric | Baseline (median/mean) | Mutation (median/mean) | Delta | Threshold | Pass? |
|--------|-------------------------|-------------------------|-------|-----------|-------|
| Viability rate (median) |  |  |  | >= +0.10 GO |  |
| Viability rate (mean) |  |  |  | Informational |  |
| Total duration (median, viable) |  |  |  | Informational |  |
| Test pass rate (mean) |  |  |  | Informational |  |
| Spec vector pass rate (mean) |  |  |  | Informational |  |
| Baseline contract pass rate (mean) |  |  |  | Informational |  |

## Stability Re-Run (Top 3 Winners)

| Spec Hash | Original Campaign ID | Re-Run Campaign ID | Original Viability | Re-Run Viability | Reproduced Improvement? |
|-----------|----------------------|--------------------|--------------------|------------------|-------------------------|
|  |  |  |  |  |  |
|  |  |  |  |  |  |
|  |  |  |  |  |  |

Rule of thumb: at least 2/3 winners should reproduce.

## Invariant Checks (Must Pass)

- [ ] `tests/test_spec_compliance.py` passes
- [ ] `tests/test_security.py` passes
- [ ] No container security invariant violations observed
- [ ] Generation numbers remain monotonic
- [ ] BO resume behavior is correct (no duplicate base-spec observation)

## Final Decision

- Decision: GO / INCONCLUSIVE / NO-GO
- Rationale:
- Follow-up actions:
