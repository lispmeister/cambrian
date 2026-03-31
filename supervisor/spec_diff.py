"""Spec diff tooling — structured diff between spec versions with section-level attribution.

Used by the M2 mutation loop to:
  1. Compute which sections a mutation changed (detect linkage drag).
  2. Attribute fitness deltas to specific sections across campaigns.
  3. Apply or revert mutations stored as unified diffs.

Section granularity: top-level markdown headings (## ). Sub-headings (### ) belong to
their parent ## section. This matches the spec's logical decomposition for mutation.
"""

import difflib
import hashlib
import re
from dataclasses import dataclass
from typing import Any

import structlog

log = structlog.get_logger(component="spec_diff")

# ---------------------------------------------------------------------------
# Section parsing
# ---------------------------------------------------------------------------

# Matches lines that start a top-level (## ) section.
_H2_RE = re.compile(r"^## (.+)$", re.MULTILINE)
# Matches FROZEN block markers.
_FROZEN_BEGIN_RE = re.compile(r"<!--\s*BEGIN FROZEN:\s*(\S+)\s*-->")
_FROZEN_END_RE = re.compile(r"<!--\s*END FROZEN:\s*(\S+)\s*-->")


def parse_sections(spec_text: str) -> dict[str, str]:
    """Parse a markdown spec into {section_title: content} using ## headings.

    Each section's content includes the heading line, all body text, and any
    ### sub-headings up to (but not including) the next ## heading.
    A synthetic ``__preamble__`` key holds any text before the first ## heading.
    """
    lines = spec_text.splitlines(keepends=True)
    sections: dict[str, str] = {}
    current_name: str = "__preamble__"
    current_lines: list[str] = []

    for line in lines:
        m = _H2_RE.match(line)
        if m:
            sections[current_name] = "".join(current_lines)
            current_name = m.group(1).strip()
            current_lines = [line]
        else:
            current_lines.append(line)

    sections[current_name] = "".join(current_lines)
    return sections


def frozen_section_names(spec_text: str) -> set[str]:
    """Return the set of ## section titles that are (partially) inside a FROZEN block."""
    frozen: set[str] = set()
    in_frozen = False
    for line in spec_text.splitlines():
        if _FROZEN_BEGIN_RE.search(line):
            in_frozen = True
        if in_frozen:
            m = _H2_RE.match(line)
            if m:
                frozen.add(m.group(1).strip())
        if _FROZEN_END_RE.search(line):
            in_frozen = False
    return frozen


# ---------------------------------------------------------------------------
# Diff data structures
# ---------------------------------------------------------------------------


@dataclass
class SectionChange:
    """Records how a single ## section changed between two spec versions."""

    section_name: str
    lines_added: int
    lines_removed: int
    is_frozen: bool


@dataclass
class SpecDiff:
    """Structured diff between two spec versions with section-level attribution."""

    parent_hash: str  # sha256:<hex> of parent spec text
    child_hash: str   # sha256:<hex> of child spec text
    sections_changed: list[SectionChange]
    sections_unchanged: list[str]
    total_lines_added: int
    total_lines_removed: int
    unified_diff: str  # full unified diff as a single string (for display / storage)


# ---------------------------------------------------------------------------
# Diff computation
# ---------------------------------------------------------------------------


def diff_spec(spec_a: str, spec_b: str, filename: str = "CAMBRIAN-SPEC-005.md") -> SpecDiff:
    """Compute a structured diff between two spec versions.

    Args:
        spec_a: Parent spec text.
        spec_b: Child (mutated) spec text.
        filename: Used in the unified diff header (display only).

    Returns:
        SpecDiff with per-section change counts and the full unified diff.
    """
    parent_hash = _hash_text(spec_a)
    child_hash = _hash_text(spec_b)

    lines_a = spec_a.splitlines(keepends=True)
    lines_b = spec_b.splitlines(keepends=True)

    unified = list(
        difflib.unified_diff(
            lines_a,
            lines_b,
            fromfile=f"a/{filename}",
            tofile=f"b/{filename}",
            lineterm="",
        )
    )
    unified_str = "\n".join(unified)

    # Build section→line-range mapping for spec_a (source of truth for attribution).
    section_ranges = _build_section_ranges(spec_a)
    frozen = frozen_section_names(spec_a) | frozen_section_names(spec_b)

    # Parse the unified diff and attribute each added/removed line to a section.
    added_by_section: dict[str, int] = {}
    removed_by_section: dict[str, int] = {}
    total_added = 0
    total_removed = 0

    current_a_line = 0  # 1-based line number in spec_a being processed
    for raw_line in unified:
        hunk_m = re.match(r"^@@ -(\d+)(?:,\d+)? \+(\d+)(?:,\d+)? @@", raw_line)
        if hunk_m:
            current_a_line = int(hunk_m.group(1))
            continue
        if raw_line.startswith("---") or raw_line.startswith("+++"):
            continue
        if raw_line.startswith("-"):
            sec = _section_for_line(section_ranges, current_a_line)
            removed_by_section[sec] = removed_by_section.get(sec, 0) + 1
            total_removed += 1
            current_a_line += 1
        elif raw_line.startswith("+"):
            # Addition: attribute to the same section as the surrounding context.
            sec = _section_for_line(section_ranges, current_a_line)
            added_by_section[sec] = added_by_section.get(sec, 0) + 1
            total_added += 1
        else:  # context line
            current_a_line += 1

    all_sections = set(section_ranges.keys())
    changed_names = set(added_by_section) | set(removed_by_section)
    unchanged_names = sorted(all_sections - changed_names)

    sections_changed = [
        SectionChange(
            section_name=name,
            lines_added=added_by_section.get(name, 0),
            lines_removed=removed_by_section.get(name, 0),
            is_frozen=(name in frozen),
        )
        for name in sorted(changed_names)
    ]

    return SpecDiff(
        parent_hash=parent_hash,
        child_hash=child_hash,
        sections_changed=sections_changed,
        sections_unchanged=unchanged_names,
        total_lines_added=total_added,
        total_lines_removed=total_removed,
        unified_diff=unified_str,
    )


def _build_section_ranges(spec_text: str) -> dict[str, tuple[int, int]]:
    """Return {section_name: (first_line_1based, last_line_1based)} for each ## section."""
    lines = spec_text.splitlines(keepends=True)
    ranges: dict[str, tuple[int, int]] = {}
    current_name = "__preamble__"
    current_start = 1

    for i, line in enumerate(lines, start=1):
        m = _H2_RE.match(line)
        if m:
            ranges[current_name] = (current_start, i - 1)
            current_name = m.group(1).strip()
            current_start = i

    ranges[current_name] = (current_start, len(lines))
    return ranges


def _section_for_line(section_ranges: dict[str, tuple[int, int]], line_no: int) -> str:
    """Return the section name that contains the given 1-based line number."""
    for name, (start, end) in section_ranges.items():
        if start <= line_no <= end:
            return name
    return "__unknown__"


def _hash_text(text: str) -> str:
    return f"sha256:{hashlib.sha256(text.encode()).hexdigest()}"


# ---------------------------------------------------------------------------
# Fitness attribution
# ---------------------------------------------------------------------------


def attribute_fitness_delta(
    spec_diff: SpecDiff,
    summary_before: dict[str, Any],
    summary_after: dict[str, Any],
) -> dict[str, Any]:
    """Correlate a spec mutation with a change in campaign fitness.

    Computes the delta between two CampaignSummary dicts (as produced by
    compute_campaign_summary) and attributes it to the sections that changed.

    Returns a dict with:
      viability_rate_delta:  float
      fitness_mean_deltas:   {metric: delta}   (metrics present in both summaries)
      sections_changed:      [section_name, ...]
      sections_changed_count: int
      frozen_sections_changed: [section_name, ...]  (should be empty; alert if not)
      entanglement_score:    float  (sections changed / total sections including unchanged)
      per_section:           {section_name: {lines_added, lines_removed}}
    """
    viability_delta = round(
        summary_after.get("viability_rate", 0.0) - summary_before.get("viability_rate", 0.0),
        4,
    )

    before_mean: dict[str, float] = summary_before.get("fitness_mean", {})
    after_mean: dict[str, float] = summary_after.get("fitness_mean", {})
    shared_keys = set(before_mean) & set(after_mean)
    fitness_mean_deltas = {
        k: round(after_mean[k] - before_mean[k], 4) for k in sorted(shared_keys)
    }

    changed_names = [sc.section_name for sc in spec_diff.sections_changed]
    frozen_changed = [sc.section_name for sc in spec_diff.sections_changed if sc.is_frozen]

    total_sections = len(spec_diff.sections_changed) + len(spec_diff.sections_unchanged)
    entanglement = (
        round(len(spec_diff.sections_changed) / total_sections, 4)
        if total_sections > 0
        else 0.0
    )

    per_section = {
        sc.section_name: {
            "lines_added": sc.lines_added,
            "lines_removed": sc.lines_removed,
            "is_frozen": sc.is_frozen,
        }
        for sc in spec_diff.sections_changed
    }

    return {
        "viability_rate_delta": viability_delta,
        "fitness_mean_deltas": fitness_mean_deltas,
        "sections_changed": changed_names,
        "sections_changed_count": len(changed_names),
        "frozen_sections_changed": frozen_changed,
        "entanglement_score": entanglement,
        "per_section": per_section,
    }


# ---------------------------------------------------------------------------
# Apply / revert
# ---------------------------------------------------------------------------


def apply_spec_diff(original: str, unified_diff: str) -> str:
    """Apply a unified diff to the original text, returning the modified text.

    Raises ValueError if the diff cannot be applied cleanly.
    """
    return _apply_unified_diff(original, unified_diff, reverse=False)


def revert_spec_diff(modified: str, unified_diff: str) -> str:
    """Reverse a unified diff: given the modified text, recover the original.

    Raises ValueError if the diff cannot be reversed cleanly.
    """
    return _apply_unified_diff(modified, unified_diff, reverse=True)


def _apply_unified_diff(source: str, unified_diff: str, *, reverse: bool) -> str:
    """Parse and apply a unified diff. If reverse=True, swap + and - roles."""
    if not unified_diff.strip():
        return source

    lines = source.splitlines(keepends=True)
    # Ensure the last line has a newline for clean indexing.
    result: list[str] = list(lines)

    hunks = _parse_unified_diff_hunks(unified_diff)
    if not hunks:
        return source

    offset = 0  # cumulative line shift from previous hunks
    for hunk_a_start, hunk_b_start, hunk_lines in hunks:
        # hunk_a_start is 1-based line in source (before any hunk application).
        result, offset = _apply_hunk(result, hunk_a_start, hunk_lines, offset, reverse=reverse)

    return "".join(result)


def _parse_unified_diff_hunks(
    unified_diff: str,
) -> list[tuple[int, int, list[str]]]:
    """Parse unified diff into list of (a_start, b_start, hunk_lines).

    hunk_lines: list of raw diff lines within the hunk (excluding the @@ header).
    """
    hunks: list[tuple[int, int, list[str]]] = []
    current_a_start = 0
    current_b_start = 0
    current_lines: list[str] = []
    in_hunk = False

    for line in unified_diff.splitlines():
        m = re.match(r"^@@ -(\d+)(?:,\d+)? \+(\d+)(?:,\d+)? @@", line)
        if m:
            if in_hunk:
                hunks.append((current_a_start, current_b_start, current_lines))
            current_a_start = int(m.group(1))
            current_b_start = int(m.group(2))
            current_lines = []
            in_hunk = True
        elif in_hunk and (line.startswith((" ", "-", "+")) or line == ""):
            current_lines.append(line)

    if in_hunk:
        hunks.append((current_a_start, current_b_start, current_lines))

    return hunks


def _apply_hunk(
    lines: list[str],
    a_start: int,
    hunk_lines: list[str],
    offset: int,
    *,
    reverse: bool,
) -> tuple[list[str], int]:
    """Apply one unified diff hunk to lines (mutable list).

    Returns (updated_lines, new_offset).
    a_start is 1-based; offset adjusts for prior hunk insertions/deletions.
    """
    pos = a_start - 1 + offset  # convert to 0-based with offset
    new_lines: list[str] = list(lines[:pos])
    ptr = pos  # pointer into original lines

    if reverse:
        remove_char, add_char = "+", "-"
    else:
        remove_char, add_char = "-", "+"

    added = 0
    removed = 0

    for hl in hunk_lines:
        if not hl:
            continue
        tag = hl[0]
        content = hl[1:] + ("\n" if not hl[1:].endswith("\n") else "")
        if tag == " ":  # context
            if ptr < len(lines):
                new_lines.append(lines[ptr])
                ptr += 1
        elif tag == remove_char:  # line to remove
            ptr += 1  # skip this line from source
            removed += 1
        elif tag == add_char:  # line to add
            new_lines.append(content)
            added += 1

    # Append remaining original lines.
    new_lines.extend(lines[ptr:])
    new_offset = offset + added - removed
    return new_lines, new_offset
