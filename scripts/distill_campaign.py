#!/usr/bin/env python3
"""Post-campaign phenotypic distiller — propose spec patches from differential code analysis.

Given a campaign's artifacts directory, this script:
  1. Loads all generation artifacts (viable and failed)
  2. Extracts code-level patterns via AST analysis (not string matching)
  3. Diffs patterns between viable vs failed, and top-ranked vs bottom-ranked viable
  4. Proposes concrete spec amendments (SHOULD/MUST rules with examples)

The output is a markdown report with proposed spec patches.

Usage:
    python scripts/distill_campaign.py <campaign_dir> [--generations-json <path>]
    python scripts/distill_campaign.py ../cambrian-artifacts/gen-0-campaigns/gen0-1775283677

Environment:
    CAMBRIAN_ARTIFACTS_ROOT  (default: ../cambrian-artifacts)
"""

from __future__ import annotations

import argparse
import ast
import json
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

_PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------


@dataclass
class GenProfile:
    """Code-level phenotype extracted from one generation's artifact."""

    generation: int
    viable: bool
    failure_stage: str  # "none" for viable
    fitness: dict[str, Any] = field(default_factory=dict)

    # AST-derived patterns (presence/absence or counts)
    uses_time_monotonic: bool = False
    uses_time_time: bool = False
    has_exponential_backoff: bool = False
    has_specific_exceptions: bool = False  # catches something other than Exception/BaseException
    has_bare_except: bool = False
    structlog_antipatterns: list[str] = field(default_factory=list)
    uses_pathlib: bool = False
    uses_fstrings: bool = False
    uses_type_annotations: bool = False
    uses_asyncio_sleep: bool = False
    uses_pydantic: bool = False
    uses_dataclasses: bool = False
    has_docstrings: bool = False
    retry_count_cap: int | None = None  # max retries if detectable
    custom_patterns: dict[str, bool] = field(default_factory=dict)

    # Raw source for pattern extraction
    source_files: dict[str, str] = field(default_factory=dict, repr=False)


@dataclass
class PatternDelta:
    """A pattern that differentiates better from worse gens."""

    pattern_name: str
    description: str
    present_in_better: int  # count of better gens with this pattern
    present_in_worse: int  # count of worse gens with this pattern
    total_better: int
    total_worse: int
    code_example: str | None = None  # extracted from the best gen that has it
    severity: str = "SHOULD"  # SHOULD or MUST

    @property
    def differential(self) -> float:
        """How much more likely this pattern is in better vs worse gens."""
        rate_better = self.present_in_better / max(self.total_better, 1)
        rate_worse = self.present_in_worse / max(self.total_worse, 1)
        return rate_better - rate_worse


@dataclass
class DistillReport:
    """Complete distillation output."""

    campaign_dir: str
    total_gens: int
    viable_count: int
    failed_count: int
    viable_vs_failed: list[PatternDelta]
    top_vs_bottom: list[PatternDelta]
    proposed_patches: list[str]


# ---------------------------------------------------------------------------
# AST-based pattern extraction
# ---------------------------------------------------------------------------


def _extract_profile(gen_dir: Path, gen_num: int, record: dict[str, Any]) -> GenProfile | None:
    """Extract a GenProfile from a generation's artifact directory."""
    src_dir = gen_dir / "src"
    if not src_dir.exists():
        return None

    viability = record.get("viability", {})
    profile = GenProfile(
        generation=gen_num,
        viable=viability.get("status") == "viable",
        failure_stage=viability.get("failure_stage", "unknown"),
        fitness=viability.get("fitness", {}),
    )

    # Collect all Python source files
    py_files: list[Path] = []
    for d in [src_dir, gen_dir / "tests"]:
        if d.exists():
            py_files.extend(d.rglob("*.py"))
    # Also check top-level .py files
    py_files.extend(gen_dir.glob("*.py"))

    for py_file in py_files:
        try:
            source = py_file.read_text()
        except OSError, UnicodeDecodeError:
            continue
        rel = str(py_file.relative_to(gen_dir))
        profile.source_files[rel] = source

        try:
            tree = ast.parse(source, filename=rel)
        except SyntaxError:
            continue

        _analyze_ast(tree, source, profile)

    return profile


def _analyze_ast(tree: ast.Module, source: str, profile: GenProfile) -> None:
    """Walk the AST and update profile with detected patterns."""
    for node in ast.walk(tree):
        # time.monotonic() vs time.time()
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
            if isinstance(node.func.value, ast.Name) and node.func.value.id == "time":
                if node.func.attr == "monotonic":
                    profile.uses_time_monotonic = True
                elif node.func.attr == "time":
                    profile.uses_time_time = True

        # asyncio.sleep usage
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
            if isinstance(node.func.value, ast.Name) and node.func.value.id == "asyncio":
                if node.func.attr == "sleep":
                    profile.uses_asyncio_sleep = True

        # Exception handling patterns
        if isinstance(node, ast.ExceptHandler):
            if node.type is None:
                profile.has_bare_except = True
            elif isinstance(node.type, ast.Name):
                if node.type.id not in ("Exception", "BaseException"):
                    profile.has_specific_exceptions = True
            elif isinstance(node.type, ast.Tuple):
                for elt in node.type.elts:
                    if isinstance(elt, ast.Name) and elt.id not in (
                        "Exception",
                        "BaseException",
                    ):
                        profile.has_specific_exceptions = True

        # Exponential backoff detection: look for `var * 2` or `var *= 2` near sleep
        if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Mult):
            if isinstance(node.right, ast.Constant) and node.right.value == 2:
                profile.has_exponential_backoff = True
        if isinstance(node, ast.AugAssign) and isinstance(node.op, ast.Mult):
            if isinstance(node.value, ast.Constant) and node.value.value == 2:
                profile.has_exponential_backoff = True

        # Pathlib usage
        if isinstance(node, ast.ImportFrom) and node.module == "pathlib":
            profile.uses_pathlib = True

        # Pydantic usage
        if isinstance(node, ast.ImportFrom) and node.module and "pydantic" in node.module:
            profile.uses_pydantic = True

        # Dataclass usage
        if isinstance(node, ast.ImportFrom) and node.module == "dataclasses":
            profile.uses_dataclasses = True

        # Type annotations (check function defs for return annotations or arg annotations)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            if node.returns is not None:
                profile.uses_type_annotations = True
            for arg in node.args.args:
                if arg.annotation is not None:
                    profile.uses_type_annotations = True
            # Docstrings
            if (
                node.body
                and isinstance(node.body[0], ast.Expr)
                and isinstance(node.body[0].value, ast.Constant)
                and isinstance(node.body[0].value.value, str)
            ):
                profile.has_docstrings = True

        # f-string usage
        if isinstance(node, ast.JoinedStr):
            profile.uses_fstrings = True

    # Structlog antipatterns: search source text (cheaper than AST for this)
    for line in source.splitlines():
        stripped = line.strip()
        # Pattern: log.xxx("event_name", event="event_name") — duplicated event
        if "event=" in stripped and any(
            stripped.startswith(f"log.{lvl}(") for lvl in ("debug", "info", "warning", "error")
        ):
            profile.structlog_antipatterns.append(stripped[:120])
        # Pattern: log.xxx("msg", event_msg="...") — non-standard kwarg
        if "event_msg=" in stripped:
            profile.structlog_antipatterns.append(stripped[:120])


# ---------------------------------------------------------------------------
# Differential analysis
# ---------------------------------------------------------------------------


def _compute_deltas(
    better: list[GenProfile],
    worse: list[GenProfile],
    label: str,
) -> list[PatternDelta]:
    """Compare pattern prevalence between two groups, return significant deltas."""
    patterns: list[tuple[str, str, str]] = [
        # (attr_name, human description, severity)
        ("uses_time_monotonic", "Uses time.monotonic() for uptime", "SHOULD"),
        ("has_exponential_backoff", "Exponential backoff on retries", "SHOULD"),
        ("has_specific_exceptions", "Catches specific exception types", "SHOULD"),
        ("uses_pydantic", "Uses Pydantic models for I/O validation", "SHOULD"),
        ("uses_pathlib", "Uses pathlib.Path for file paths", "SHOULD"),
        ("uses_type_annotations", "Type annotations on functions", "SHOULD"),
        ("has_docstrings", "Docstrings on functions/classes", "SHOULD"),
        ("uses_dataclasses", "Uses dataclasses for internal data", "SHOULD"),
    ]

    # Negative patterns (present in worse is bad)
    negative_patterns: list[tuple[str, str, str]] = [
        ("uses_time_time", "Uses time.time() (clock can jump)", "MUST NOT"),
        ("has_bare_except", "Bare except: / catches only Exception", "SHOULD NOT"),
    ]

    deltas: list[PatternDelta] = []

    for attr, desc, severity in patterns:
        in_better = sum(1 for p in better if getattr(p, attr))
        in_worse = sum(1 for p in worse if getattr(p, attr))
        delta = PatternDelta(
            pattern_name=attr,
            description=desc,
            present_in_better=in_better,
            present_in_worse=in_worse,
            total_better=len(better),
            total_worse=len(worse),
            severity=severity,
        )
        if abs(delta.differential) > 0.1:  # Only report if >10% difference
            # Try to extract a code example from the best gen that has this pattern
            delta.code_example = _find_example(better, attr)
            deltas.append(delta)

    for attr, desc, severity in negative_patterns:
        in_better = sum(1 for p in better if getattr(p, attr))
        in_worse = sum(1 for p in worse if getattr(p, attr))
        # Invert: for negative patterns, being present in worse is the signal
        delta = PatternDelta(
            pattern_name=attr,
            description=desc,
            present_in_better=in_better,
            present_in_worse=in_worse,
            total_better=len(better),
            total_worse=len(worse),
            severity=severity,
        )
        # Negative patterns: significant if MORE prevalent in worse group
        if (in_worse / max(len(worse), 1)) - (in_better / max(len(better), 1)) > 0.1:
            delta.code_example = _find_negative_example(worse, attr)
            deltas.append(delta)

    # Structlog antipatterns: special handling
    better_slap = sum(1 for p in better if p.structlog_antipatterns)
    worse_slap = sum(1 for p in worse if p.structlog_antipatterns)
    if worse_slap / max(len(worse), 1) - better_slap / max(len(better), 1) > 0.1:
        examples = []
        for p in worse:
            examples.extend(p.structlog_antipatterns[:2])
        deltas.append(
            PatternDelta(
                pattern_name="structlog_antipatterns",
                description="Structlog misuse: duplicated event= kwarg or non-standard event_msg=",
                present_in_better=better_slap,
                present_in_worse=worse_slap,
                total_better=len(better),
                total_worse=len(worse),
                code_example=examples[0] if examples else None,
                severity="MUST NOT",
            )
        )

    # Sort by differential magnitude (strongest signals first)
    deltas.sort(key=lambda d: abs(d.differential), reverse=True)
    return deltas


def _find_example(profiles: list[GenProfile], attr: str) -> str | None:
    """Find a code snippet from the first profile that has the given attribute."""
    for p in profiles:
        if not getattr(p, attr):
            continue
        # Search source files for a representative snippet
        if attr == "uses_time_monotonic":
            return _grep_snippet(p.source_files, "time.monotonic()")
        if attr == "has_exponential_backoff":
            return _grep_snippet(p.source_files, "backoff", context=3)
        if attr == "has_specific_exceptions":
            return _grep_snippet(p.source_files, "except aiohttp", context=2)
        if attr == "has_docstrings":
            return None  # Too generic to example
    return None


def _find_negative_example(profiles: list[GenProfile], attr: str) -> str | None:
    """Find a code snippet illustrating a negative pattern."""
    for p in profiles:
        if not getattr(p, attr):
            continue
        if attr == "uses_time_time":
            return _grep_snippet(p.source_files, "time.time()")
        if attr == "has_bare_except":
            return _grep_snippet(p.source_files, "except Exception", context=1)
    return None


def _grep_snippet(files: dict[str, str], needle: str, context: int = 2) -> str | None:
    """Find first occurrence of needle and return surrounding lines."""
    for filename, source in files.items():
        lines = source.splitlines()
        for i, line in enumerate(lines):
            if needle in line:
                start = max(0, i - context)
                end = min(len(lines), i + context + 1)
                snippet_lines = lines[start:end]
                return "\n".join(snippet_lines)
    return None


# ---------------------------------------------------------------------------
# Ranking viable gens by fitness
# ---------------------------------------------------------------------------


def _rank_profiles(profiles: list[GenProfile]) -> list[GenProfile]:
    """Rank viable profiles by composite fitness (higher is better).

    Composite = test_pass_rate * 0.3 + normalized_test_count * 0.2
              + spec_vector_pass_rate * 0.3 + assertion_density * 0.1
              + (1 if monotonic else 0) * 0.05 + (1 if backoff else 0) * 0.05
    """

    def score(p: GenProfile) -> float:
        f = p.fitness
        s = 0.0
        s += f.get("test_pass_rate", 0.0) * 0.3
        s += min(f.get("test_count", 0) / 80.0, 1.0) * 0.2  # normalize to ~80 tests
        s += f.get("spec_vector_pass_rate", 0.0) * 0.3
        s += min(f.get("assertion_density", 0.0) / 2.0, 1.0) * 0.1
        # Bonus for good patterns
        s += 0.05 if p.uses_time_monotonic else 0.0
        s += 0.05 if p.has_exponential_backoff else 0.0
        return s

    return sorted(profiles, key=score, reverse=True)


# ---------------------------------------------------------------------------
# Spec patch proposal
# ---------------------------------------------------------------------------


def _propose_patches(deltas: list[PatternDelta], label: str) -> list[str]:
    """Generate human-readable spec patch proposals from pattern deltas."""
    patches: list[str] = []
    for d in deltas:
        if abs(d.differential) < 0.15:
            continue  # skip marginal signals

        rate_better = d.present_in_better / max(d.total_better, 1)
        rate_worse = d.present_in_worse / max(d.total_worse, 1)

        patch = f"### {d.severity}: {d.description}\n\n"
        patch += (
            f"**Signal** ({label}): {rate_better:.0%} of better gens"
            f" vs {rate_worse:.0%} of worse gens\n\n"
        )

        if d.code_example:
            if d.severity.startswith("MUST NOT") or d.severity.startswith("SHOULD NOT"):
                patch += f"**Anti-pattern (do NOT do this):**\n```python\n{d.code_example}\n```\n\n"
            else:
                patch += f"**Example:**\n```python\n{d.code_example}\n```\n\n"

        patches.append(patch)
    return patches


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------


def distill(
    campaign_dir: Path,
    generations_json: Path | None = None,
) -> DistillReport:
    """Run the full distillation pipeline on a campaign directory."""
    if generations_json is None:
        generations_json = campaign_dir.parents[1] / "generations.json"

    if not generations_json.exists():
        sys.exit(f"Error: generations.json not found at {generations_json}")

    all_records = json.loads(generations_json.read_text())

    # Find all gen-N subdirectories in the campaign dir
    gen_dirs: dict[int, Path] = {}
    for d in sorted(campaign_dir.iterdir()):
        if d.is_dir() and d.name.startswith("gen-"):
            try:
                gen_num = int(d.name.split("-", 1)[1])
                gen_dirs[gen_num] = d
            except ValueError:
                continue

    # Also check failed/ subdirectory
    failed_dir = campaign_dir / "failed"
    if failed_dir.exists():
        for d in sorted(failed_dir.iterdir()):
            if d.is_dir() and d.name.startswith("gen-"):
                try:
                    gen_num = int(d.name.split("-", 1)[1])
                    if gen_num not in gen_dirs:
                        gen_dirs[gen_num] = d
                except ValueError:
                    continue

    if not gen_dirs:
        sys.exit(f"No gen-N directories found in {campaign_dir}")

    # Build record lookup
    record_by_gen: dict[int, dict[str, Any]] = {}
    for r in all_records:
        g = r.get("generation")
        if g is not None:
            record_by_gen[int(g)] = r

    # Extract profiles
    profiles: list[GenProfile] = []
    for gen_num, gen_path in sorted(gen_dirs.items()):
        record = record_by_gen.get(gen_num, {})
        profile = _extract_profile(gen_path, gen_num, record)
        if profile is not None:
            profiles.append(profile)

    if not profiles:
        sys.exit("No profiles could be extracted")

    viable = [p for p in profiles if p.viable]
    failed = [p for p in profiles if not p.viable]

    # Analysis 1: Viable vs Failed
    viable_vs_failed: list[PatternDelta] = []
    if viable and failed:
        viable_vs_failed = _compute_deltas(viable, failed, "viable vs failed")

    # Analysis 2: Top-ranked vs Bottom-ranked viable
    top_vs_bottom: list[PatternDelta] = []
    if len(viable) >= 4:
        ranked = _rank_profiles(viable)
        midpoint = len(ranked) // 2
        top_half = ranked[:midpoint]
        bottom_half = ranked[midpoint:]
        top_vs_bottom = _compute_deltas(top_half, bottom_half, "top-ranked vs bottom-ranked")

    # Generate spec patch proposals
    all_deltas = viable_vs_failed + top_vs_bottom
    # Deduplicate by pattern_name, keeping highest differential
    seen: dict[str, PatternDelta] = {}
    for d in all_deltas:
        existing = seen.get(d.pattern_name)
        if existing is None or abs(d.differential) > abs(existing.differential):
            seen[d.pattern_name] = d
    deduped = sorted(seen.values(), key=lambda d: abs(d.differential), reverse=True)

    proposed_patches = _propose_patches(deduped, "combined")

    return DistillReport(
        campaign_dir=str(campaign_dir),
        total_gens=len(profiles),
        viable_count=len(viable),
        failed_count=len(failed),
        viable_vs_failed=viable_vs_failed,
        top_vs_bottom=top_vs_bottom,
        proposed_patches=proposed_patches,
    )


def format_report(report: DistillReport) -> str:
    """Format a DistillReport as markdown."""
    lines: list[str] = []
    lines.append("# Phenotypic Distillation Report\n")
    lines.append(f"**Campaign:** `{report.campaign_dir}`\n")
    lines.append(
        f"**Generations:** {report.total_gens} total,"
        f" {report.viable_count} viable, {report.failed_count} failed\n"
    )
    lines.append(f"**Viability rate:** {report.viable_count / max(report.total_gens, 1):.0%}\n")
    lines.append("")

    if report.viable_vs_failed:
        lines.append("## Viable vs Failed — Differentiating Patterns\n")
        lines.append("| Pattern | Viable | Failed | Δ |")
        lines.append("|---------|--------|--------|---|")
        for d in report.viable_vs_failed:
            rv = f"{d.present_in_better}/{d.total_better}"
            rf = f"{d.present_in_worse}/{d.total_worse}"
            lines.append(f"| {d.description} | {rv} | {rf} | {d.differential:+.0%} |")
        lines.append("")

    if report.top_vs_bottom:
        lines.append("## Top-Ranked vs Bottom-Ranked Viable — Quality Patterns\n")
        lines.append("| Pattern | Top | Bottom | Δ |")
        lines.append("|---------|-----|--------|---|")
        for d in report.top_vs_bottom:
            rt = f"{d.present_in_better}/{d.total_better}"
            rb = f"{d.present_in_worse}/{d.total_worse}"
            lines.append(f"| {d.description} | {rt} | {rb} | {d.differential:+.0%} |")
        lines.append("")

    if report.proposed_patches:
        lines.append("## Proposed Spec Amendments\n")
        lines.append(
            "The following amendments would encode observed"
            " phenotypic excellence into the genome.\n"
        )
        lines.append("Review each and apply to the spec if the signal is strong enough.\n")
        for patch in report.proposed_patches:
            lines.append(patch)
    else:
        lines.append("## No Spec Amendments Proposed\n")
        lines.append("All analyzed patterns were uniform across groups (no differential signal).\n")

    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Post-campaign phenotypic distiller — propose spec patches from code analysis"
    )
    parser.add_argument(
        "campaign_dir",
        type=Path,
        help="Path to campaign directory (e.g. ../cambrian-artifacts/gen-0-campaigns/gen0-...)",
    )
    parser.add_argument(
        "--generations-json",
        type=Path,
        default=None,
        help="Path to generations.json (default: auto-detect from campaign_dir)",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=None,
        help="Write report to file (default: stdout)",
    )
    args = parser.parse_args()

    if not args.campaign_dir.exists():
        sys.exit(f"Error: campaign directory not found: {args.campaign_dir}")

    report = distill(args.campaign_dir, args.generations_json)
    text = format_report(report)

    if args.output:
        args.output.write_text(text)
        print(f"Report written to {args.output}")
    else:
        print(text)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
