"""Entanglement monitor — track spec modularity over evolution.

Monitors whether successive mutations are touching more and more sections
simultaneously (entanglement) or fewer (decomposition/modularisation).

Rising entanglement is the warning sign from adversarial review §2 (Clune
modularity, Royal Society 2013): without connection-cost pressure a monolithic
genome drifts toward increasing cross-section dependencies, making Type 2
transplant mutations progressively harder.

This module is purely computational — no LLM, no I/O. It consumes a list of
SpecDiff objects (from spec_diff.diff_spec) and produces metrics.

Reference: cambrian-evw (spec_diff), adversarial review §2.
"""

import re
from dataclasses import dataclass

from .spec_diff import SpecDiff, parse_sections

# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------


@dataclass
class SectionIndependenceScore:
    """Modularity metric for a single section."""

    section_name: str
    mutations_touching: int       # times this section appeared in a SpecDiff
    mutations_touching_alone: int # times it was the ONLY section changed
    independence_score: float     # mutations_alone / mutations_touching (0–1)
    cross_refs_from: int          # ## SectionName references found in this section's text


@dataclass
class EntanglementReport:
    """Entanglement summary for a sequence of mutations."""

    mutation_count: int
    mean_sections_per_mutation: float
    max_sections_per_mutation: int
    entanglement_trend: float      # linear slope of entanglement_score over mutations
    is_entangling: bool            # trend > ENTANGLEMENT_ALERT_THRESHOLD
    section_scores: list[SectionIndependenceScore]
    cross_ref_matrix: dict[str, list[str]]  # {section: [sections it references]}


# Threshold above which we alert: entanglement_score growing at this rate per mutation.
ENTANGLEMENT_ALERT_THRESHOLD = 0.02


# ---------------------------------------------------------------------------
# Core computation
# ---------------------------------------------------------------------------


def compute_entanglement_report(
    diffs: list[SpecDiff],
    spec_text: str | None = None,
) -> EntanglementReport:
    """Compute an EntanglementReport from a sequence of SpecDiff objects.

    Args:
        diffs: Ordered list of SpecDiff objects (earliest first).
        spec_text: Current spec text. If provided, cross-reference analysis
            is performed. If None, cross_ref_matrix will be empty.

    Returns:
        EntanglementReport with trend, section independence scores, and
        an optional cross-reference matrix.
    """
    if not diffs:
        return EntanglementReport(
            mutation_count=0,
            mean_sections_per_mutation=0.0,
            max_sections_per_mutation=0,
            entanglement_trend=0.0,
            is_entangling=False,
            section_scores=[],
            cross_ref_matrix={},
        )

    # Per-mutation section counts
    sections_per_mutation: list[int] = [len(d.sections_changed) for d in diffs]
    all_section_names: set[str] = set()
    for d in diffs:
        for sc in d.sections_changed:
            all_section_names.add(sc.section_name)
        all_section_names.update(d.sections_unchanged)

    mean_count = sum(sections_per_mutation) / len(sections_per_mutation)
    max_count = max(sections_per_mutation)

    # Entanglement trend: slope of (sections_changed / total_sections) over time
    total_sections = len(all_section_names) or 1
    entanglement_series = [n / total_sections for n in sections_per_mutation]
    trend = _linear_slope(entanglement_series)

    # Section independence scores
    touching: dict[str, int] = {}
    touching_alone: dict[str, int] = {}
    for d in diffs:
        changed = [sc.section_name for sc in d.sections_changed]
        for name in changed:
            touching[name] = touching.get(name, 0) + 1
            if len(changed) == 1:
                touching_alone[name] = touching_alone.get(name, 0) + 1

    # Cross-reference analysis (static coupling)
    cross_ref_matrix: dict[str, list[str]] = {}
    cross_refs_from: dict[str, int] = {}
    if spec_text is not None:
        cross_ref_matrix, cross_refs_from = _compute_cross_refs(spec_text, all_section_names)

    section_scores = []
    for name in sorted(all_section_names):
        t = touching.get(name, 0)
        ta = touching_alone.get(name, 0)
        score = round(ta / t, 4) if t > 0 else 1.0
        section_scores.append(
            SectionIndependenceScore(
                section_name=name,
                mutations_touching=t,
                mutations_touching_alone=ta,
                independence_score=score,
                cross_refs_from=cross_refs_from.get(name, 0),
            )
        )

    return EntanglementReport(
        mutation_count=len(diffs),
        mean_sections_per_mutation=round(mean_count, 4),
        max_sections_per_mutation=max_count,
        entanglement_trend=round(trend, 6),
        is_entangling=trend > ENTANGLEMENT_ALERT_THRESHOLD,
        section_scores=section_scores,
        cross_ref_matrix=cross_ref_matrix,
    )


def entanglement_alert(report: EntanglementReport) -> str | None:
    """Return a human-readable alert string if entanglement is increasing, else None."""
    if not report.is_entangling:
        return None
    most_coupled = sorted(
        report.section_scores, key=lambda s: s.independence_score
    )[:3]
    coupled_names = ", ".join(s.section_name for s in most_coupled if s.mutations_touching > 0)
    return (
        f"Entanglement rising: trend={report.entanglement_trend:+.4f}/mutation "
        f"(threshold={ENTANGLEMENT_ALERT_THRESHOLD}). "
        f"Most coupled sections: {coupled_names or 'none identified'}. "
        f"Consider section decomposition (DSPy-style typed modules)."
    )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _compute_cross_refs(
    spec_text: str, section_names: set[str]
) -> tuple[dict[str, list[str]], dict[str, int]]:
    """Find explicit cross-section references in the spec text.

    For each section, count how many other section names it mentions verbatim.
    Returns (matrix: {section: [referenced sections]}, counts: {section: int}).
    """
    sections = parse_sections(spec_text)
    matrix: dict[str, list[str]] = {}
    counts: dict[str, int] = {}

    for src_name, content in sections.items():
        if src_name == "__preamble__":
            continue
        refs: list[str] = []
        for tgt_name in section_names:
            if tgt_name == src_name:
                continue
            # Match "## TargetSection" reference in content (as a heading or link)
            pattern = re.compile(re.escape(tgt_name))
            if pattern.search(content):
                refs.append(tgt_name)
        matrix[src_name] = sorted(refs)
        counts[src_name] = len(refs)

    return matrix, counts


def _linear_slope(values: list[float]) -> float:
    """Least-squares slope of a sequence of values. Returns 0.0 for < 2 points."""
    n = len(values)
    if n < 2:
        return 0.0
    xs = list(range(n))
    sum_x = sum(xs)
    sum_y = sum(values)
    sum_xy = sum(x * y for x, y in zip(xs, values))
    sum_x2 = sum(x * x for x in xs)
    denom = n * sum_x2 - sum_x * sum_x
    return 0.0 if denom == 0 else (n * sum_xy - sum_x * sum_y) / denom
