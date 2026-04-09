#!/usr/bin/env python3
"""Summarize M2 campaign results from generations.json.

This helper reads generation records, computes per-campaign metrics, and emits
markdown tables that can be pasted into docs/templates/m2-stage1-results-template.md.

Examples:
  uv run python scripts/summarize_m2_results.py \
    --baseline-campaign-id gen0-1775283677

  uv run python scripts/summarize_m2_results.py \
    --baseline-campaign-id campaign-a --baseline-campaign-id campaign-b \
    --mutation-campaign-id campaign-c --mutation-campaign-id campaign-d \
    --markdown-out /tmp/m2-summary.md --json-out /tmp/m2-summary.json
"""

from __future__ import annotations

import argparse
import json
import os
import statistics
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

_PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))


@dataclass
class CampaignMetrics:
    group: str
    campaign_id: str
    spec_hash: str
    start_gen: int
    end_gen: int
    n: int
    viability_rate: float
    median_total_duration_ms: float | None
    mean_test_count: float | None
    mean_test_pass_rate: float | None
    mean_spec_vector_pass_rate: float | None
    mean_baseline_contract_pass_rate: float | None
    dominant_failure_stage: str


def _mean(values: list[float]) -> float | None:
    if not values:
        return None
    return float(sum(values) / len(values))


def _fmt_num(value: float | None, digits: int = 4) -> str:
    if value is None:
        return ""
    return f"{value:.{digits}f}"


def _load_records(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        raise FileNotFoundError(f"generations file not found: {path}")
    data = json.loads(path.read_text())
    if not isinstance(data, list):
        raise ValueError(f"expected list in {path}, got {type(data).__name__}")
    return [r for r in data if isinstance(r, dict)]


def _group_records_by_campaign(records: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    groups: dict[str, list[dict[str, Any]]] = {}
    for r in records:
        cid = r.get("campaign-id")
        if not isinstance(cid, str) or not cid:
            continue
        groups.setdefault(cid, []).append(r)
    for cid in groups:
        groups[cid].sort(key=lambda r: int(r.get("generation", 0)))
    return groups


def _campaign_metrics(
    group: str, campaign_id: str, records: list[dict[str, Any]]
) -> CampaignMetrics:
    from supervisor.campaign import compute_campaign_summary

    summary = compute_campaign_summary(records)

    generations = [
        int(r.get("generation", 0)) for r in records if isinstance(r.get("generation"), int)
    ]
    start_gen = min(generations) if generations else 0
    end_gen = max(generations) if generations else 0

    spec_hashes = sorted(
        {str(r.get("spec-hash")) for r in records if isinstance(r.get("spec-hash"), str)}
    )
    spec_hash = spec_hashes[0] if len(spec_hashes) == 1 else "multiple"

    total_duration_values: list[float] = []
    test_count_values: list[float] = []
    test_pass_rate_values: list[float] = []
    spec_vector_pass_rate_values: list[float] = []
    baseline_contract_pass_rate_values: list[float] = []

    failure_distribution: dict[str, int] = summary.get("failure_distribution", {})
    dominant_failure_stage = "none"
    if failure_distribution:
        dominant_failure_stage = max(
            sorted(failure_distribution.items()),
            key=lambda kv: kv[1],
        )[0]

    for r in records:
        viability = r.get("viability") if isinstance(r.get("viability"), dict) else {}
        fitness = viability.get("fitness") if isinstance(viability.get("fitness"), dict) else {}

        status = viability.get("status")
        total_duration = fitness.get("total_duration_ms")
        if status == "viable" and isinstance(total_duration, (int, float)):
            total_duration_values.append(float(total_duration))

        for key, bucket in (
            ("test_count", test_count_values),
            ("test_pass_rate", test_pass_rate_values),
            ("spec_vector_pass_rate", spec_vector_pass_rate_values),
            ("baseline_contract_pass_rate", baseline_contract_pass_rate_values),
        ):
            value = fitness.get(key)
            if isinstance(value, (int, float)):
                bucket.append(float(value))

    return CampaignMetrics(
        group=group,
        campaign_id=campaign_id,
        spec_hash=spec_hash,
        start_gen=start_gen,
        end_gen=end_gen,
        n=len(records),
        viability_rate=float(summary.get("viability_rate", 0.0)),
        median_total_duration_ms=(
            float(statistics.median(total_duration_values)) if total_duration_values else None
        ),
        mean_test_count=_mean(test_count_values),
        mean_test_pass_rate=_mean(test_pass_rate_values),
        mean_spec_vector_pass_rate=_mean(spec_vector_pass_rate_values),
        mean_baseline_contract_pass_rate=_mean(baseline_contract_pass_rate_values),
        dominant_failure_stage=dominant_failure_stage,
    )


def _aggregate_group(campaigns: list[CampaignMetrics]) -> dict[str, float | None]:
    if not campaigns:
        return {
            "viability_rate_median": None,
            "viability_rate_mean": None,
            "duration_median": None,
            "test_pass_rate_mean": None,
            "spec_vector_pass_rate_mean": None,
            "baseline_contract_pass_rate_mean": None,
        }

    def collect(name: str) -> list[float]:
        out: list[float] = []
        for c in campaigns:
            value = getattr(c, name)
            if isinstance(value, (int, float)):
                out.append(float(value))
        return out

    viab = collect("viability_rate")
    duration = collect("median_total_duration_ms")
    test_pass_rate = collect("mean_test_pass_rate")
    spec_vector_pass_rate = collect("mean_spec_vector_pass_rate")
    baseline_contract = collect("mean_baseline_contract_pass_rate")

    return {
        "viability_rate_median": (float(statistics.median(viab)) if viab else None),
        "viability_rate_mean": _mean(viab),
        "duration_median": (float(statistics.median(duration)) if duration else None),
        "test_pass_rate_mean": _mean(test_pass_rate),
        "spec_vector_pass_rate_mean": _mean(spec_vector_pass_rate),
        "baseline_contract_pass_rate_mean": _mean(baseline_contract),
    }


def _render_markdown(
    baseline_rows: list[CampaignMetrics],
    mutation_rows: list[CampaignMetrics],
    baseline_agg: dict[str, float | None],
    mutation_agg: dict[str, float | None],
) -> str:
    lines: list[str] = []

    def metric_delta(a: float | None, b: float | None) -> float | None:
        if isinstance(a, (int, float)) and isinstance(b, (int, float)):
            return float(a - b)
        return None

    lines.append("## Campaign Rows")
    lines.append("")
    lines.append(
        "| Group | Campaign ID | Spec Hash | Start Gen | End Gen | N | Viability Rate | "
        "Median Total Duration (ms) | Mean Test Count | Mean Test Pass Rate | "
        "Mean Spec Vector Pass Rate | Mean Baseline Contract Pass Rate | Dominant Failure Stage |"
    )
    lines.append(
        "|-------|-------------|-----------|-----------|---------|---|----------------|"
        "----------------------------|-----------------|---------------------|"
        "----------------------------|----------------------------------|"
        "------------------------|"
    )

    for row in baseline_rows + mutation_rows:
        lines.append(
            f"| {row.group} | {row.campaign_id} | {row.spec_hash} | "
            f"{row.start_gen} | {row.end_gen} | {row.n} | "
            f"{_fmt_num(row.viability_rate)} | {_fmt_num(row.median_total_duration_ms, 1)} | "
            f"{_fmt_num(row.mean_test_count, 2)} | {_fmt_num(row.mean_test_pass_rate)} | "
            f"{_fmt_num(row.mean_spec_vector_pass_rate)} | "
            f"{_fmt_num(row.mean_baseline_contract_pass_rate)} | "
            f"{row.dominant_failure_stage} |"
        )

    b_viab_med = baseline_agg.get("viability_rate_median")
    m_viab_med = mutation_agg.get("viability_rate_median")
    viab_med_delta = metric_delta(m_viab_med, b_viab_med)

    viab_mean_delta = metric_delta(
        mutation_agg.get("viability_rate_mean"),
        baseline_agg.get("viability_rate_mean"),
    )
    duration_delta = metric_delta(
        mutation_agg.get("duration_median"),
        baseline_agg.get("duration_median"),
    )
    test_pass_delta = metric_delta(
        mutation_agg.get("test_pass_rate_mean"),
        baseline_agg.get("test_pass_rate_mean"),
    )
    spec_vector_delta = metric_delta(
        mutation_agg.get("spec_vector_pass_rate_mean"),
        baseline_agg.get("spec_vector_pass_rate_mean"),
    )
    baseline_contract_delta = metric_delta(
        mutation_agg.get("baseline_contract_pass_rate_mean"),
        baseline_agg.get("baseline_contract_pass_rate_mean"),
    )

    lines.append("")
    lines.append("## Aggregated Comparison")
    lines.append("")
    lines.append(
        "| Metric | Baseline (median/mean) | Mutation (median/mean) | Delta | Threshold | Pass? |"
    )
    lines.append(
        "|--------|-------------------------|-------------------------|-------|-----------|-------|"
    )
    lines.append(
        "| Viability rate (median) | "
        f"{_fmt_num(b_viab_med)} | "
        f"{_fmt_num(m_viab_med)} | "
        f"{_fmt_num(viab_med_delta)} | >= +0.10 GO | "
        f"{'yes' if isinstance(viab_med_delta, float) and viab_med_delta >= 0.10 else 'no'} |"
    )
    lines.append(
        "| Viability rate (mean) | "
        f"{_fmt_num(baseline_agg.get('viability_rate_mean'))} | "
        f"{_fmt_num(mutation_agg.get('viability_rate_mean'))} | "
        f"{_fmt_num(viab_mean_delta)} | Informational |  |"
    )
    lines.append(
        "| Total duration (median, viable) | "
        f"{_fmt_num(baseline_agg.get('duration_median'), 1)} | "
        f"{_fmt_num(mutation_agg.get('duration_median'), 1)} | "
        f"{_fmt_num(duration_delta, 1)} | Informational |  |"
    )
    lines.append(
        "| Test pass rate (mean) | "
        f"{_fmt_num(baseline_agg.get('test_pass_rate_mean'))} | "
        f"{_fmt_num(mutation_agg.get('test_pass_rate_mean'))} | "
        f"{_fmt_num(test_pass_delta)} | Informational |  |"
    )
    lines.append(
        "| Spec vector pass rate (mean) | "
        f"{_fmt_num(baseline_agg.get('spec_vector_pass_rate_mean'))} | "
        f"{_fmt_num(mutation_agg.get('spec_vector_pass_rate_mean'))} | "
        f"{_fmt_num(spec_vector_delta)} | Informational |  |"
    )
    lines.append(
        "| Baseline contract pass rate (mean) | "
        f"{_fmt_num(baseline_agg.get('baseline_contract_pass_rate_mean'))} | "
        f"{_fmt_num(mutation_agg.get('baseline_contract_pass_rate_mean'))} | "
        f"{_fmt_num(baseline_contract_delta)} | Informational |  |"
    )

    return "\n".join(lines) + "\n"


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--generations-json",
        type=Path,
        default=Path(os.environ.get("CAMBRIAN_ARTIFACTS_ROOT", "../cambrian-artifacts"))
        / "generations.json",
        help=(
            "Path to generations.json "
            "(default: $CAMBRIAN_ARTIFACTS_ROOT/generations.json "
            "or ../cambrian-artifacts/generations.json)"
        ),
    )
    parser.add_argument(
        "--baseline-campaign-id",
        action="append",
        default=[],
        help="Campaign ID to classify as baseline (repeatable)",
    )
    parser.add_argument(
        "--mutation-campaign-id",
        action="append",
        default=[],
        help="Campaign ID to classify as mutation (repeatable)",
    )
    parser.add_argument(
        "--markdown-out",
        type=Path,
        help="Optional output path for markdown summary",
    )
    parser.add_argument(
        "--json-out",
        type=Path,
        help="Optional output path for machine-readable summary",
    )
    return parser.parse_args()


def main() -> int:
    args = _parse_args()

    records = _load_records(args.generations_json)
    by_campaign = _group_records_by_campaign(records)
    if not by_campaign:
        raise SystemExit("No campaign-id records found in generations.json")

    baseline_ids = set(args.baseline_campaign_id)
    mutation_ids = set(args.mutation_campaign_id)

    if not baseline_ids:
        raise SystemExit("At least one --baseline-campaign-id is required")

    missing = sorted((baseline_ids | mutation_ids) - set(by_campaign.keys()))
    if missing:
        raise SystemExit(f"Unknown campaign ids: {', '.join(missing)}")

    if not mutation_ids:
        mutation_ids = set(by_campaign.keys()) - baseline_ids

    overlap = baseline_ids & mutation_ids
    if overlap:
        raise SystemExit(f"Campaign ids cannot be in both groups: {', '.join(sorted(overlap))}")

    baseline_rows = [
        _campaign_metrics("baseline", cid, by_campaign[cid]) for cid in sorted(baseline_ids)
    ]
    mutation_rows = [
        _campaign_metrics("mutation", cid, by_campaign[cid]) for cid in sorted(mutation_ids)
    ]

    baseline_agg = _aggregate_group(baseline_rows)
    mutation_agg = _aggregate_group(mutation_rows)

    markdown = _render_markdown(baseline_rows, mutation_rows, baseline_agg, mutation_agg)
    print(markdown)

    if args.markdown_out:
        args.markdown_out.parent.mkdir(parents=True, exist_ok=True)
        args.markdown_out.write_text(markdown)

    if args.json_out:
        payload = {
            "baseline_campaigns": [c.__dict__ for c in baseline_rows],
            "mutation_campaigns": [c.__dict__ for c in mutation_rows],
            "baseline_aggregate": baseline_agg,
            "mutation_aggregate": mutation_agg,
        }
        args.json_out.parent.mkdir(parents=True, exist_ok=True)
        args.json_out.write_text(json.dumps(payload, indent=2) + "\n")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
