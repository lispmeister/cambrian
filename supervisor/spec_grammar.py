"""Spec grammar definition and validation for M2 mutation constraints.

Defines the lightweight formal grammar for CAMBRIAN-SPEC-005:
  - Required sections (MUST be present after any mutation)
  - Frozen sections (MUST be byte-for-byte identical between parent and child)
  - Keyword vocabulary (MUST/MAY/SHALL usage)
  - Structural constraints (port, heading hierarchy)

Used by the Type 1 mutator to reject structurally invalid mutations before they
enter a campaign — replacing the gameable LLM screener with deterministic checks.

Reference: CAMBRIAN-SPEC-005 §Mutation Constraints, adversarial review §3.
"""

import re
from dataclasses import dataclass, field

# ---------------------------------------------------------------------------
# Grammar constants
# ---------------------------------------------------------------------------

# Sections that MUST be present in any valid spec variant.
# These are the sections that Prime reads to do its job. Removing one makes the
# spec incoherent for a fresh LLM generation.
REQUIRED_SECTIONS: frozenset[str] = frozenset(
    [
        "What This Document Is",
        "Invariants",
        "Glossary",
        "Problem Statement",
        "Goals",
        "Non-Goals",
        "Design Principles",
        "What Prime Does",
        "Contracts",
        "The Generation Loop",
        "Failure Handling",
        "LLM Integration",
        "Implementation Requirements",
        "Acceptance Criteria",
        "Verification Layers",
    ]
)

# Sections that MUST be byte-for-byte identical across mutations.
# These are enforced by FROZEN markers in the spec itself; this set enables
# fast deterministic checking without re-parsing FROZEN blocks every time.
FROZEN_SECTION_NAMES: frozenset[str] = frozenset(
    [
        "Invariants",  # identity-anchor FROZEN block
    ]
)

# The server port Prime MUST expose. Any valid spec must reference this port.
REQUIRED_PORT: int = 8401

# Keywords that define normative requirements. A valid spec must use them.
NORMATIVE_KEYWORDS: frozenset[str] = frozenset(["MUST", "MAY", "SHALL"])


# ---------------------------------------------------------------------------
# Violation data structure
# ---------------------------------------------------------------------------


@dataclass
class GrammarViolation:
    """Describes one constraint violation in a spec or mutation."""

    rule: str  # short machine-readable rule identifier
    message: str  # human-readable explanation
    fatal: bool = True  # if True, the mutation must be rejected outright


# ---------------------------------------------------------------------------
# Spec validation
# ---------------------------------------------------------------------------


def validate_spec(spec_text: str) -> list[GrammarViolation]:
    """Validate a spec against the grammar.

    Returns a (possibly empty) list of GrammarViolations. Any fatal violation
    means the spec should not be used as a mutation candidate or campaign input.

    Checks:
      - All REQUIRED_SECTIONS are present as ## headings
      - Required port (8401) is referenced
      - At least one normative keyword (MUST/MAY/SHALL) is present
      - No duplicate ## headings
      - FROZEN markers are paired (BEGIN/END)
    """
    violations: list[GrammarViolation] = []

    headings = _extract_h2_headings(spec_text)
    heading_set = set(headings)

    # Required sections
    for section in REQUIRED_SECTIONS:
        if section not in heading_set:
            violations.append(
                GrammarViolation(
                    rule="missing_required_section",
                    message=f"Required section '## {section}' is absent",
                )
            )

    # Required port
    if str(REQUIRED_PORT) not in spec_text:
        violations.append(
            GrammarViolation(
                rule="missing_port",
                message=f"Required port {REQUIRED_PORT} not found in spec",
            )
        )

    # Normative keywords
    if not any(kw in spec_text for kw in NORMATIVE_KEYWORDS):
        violations.append(
            GrammarViolation(
                rule="no_normative_keywords",
                message="Spec contains no MUST/MAY/SHALL keywords",
                fatal=False,
            )
        )

    # Duplicate headings
    seen: set[str] = set()
    for heading in headings:
        if heading in seen:
            violations.append(
                GrammarViolation(
                    rule="duplicate_heading",
                    message=f"Duplicate heading '## {heading}'",
                )
            )
        seen.add(heading)

    # Paired FROZEN markers
    begin_matches = re.findall(r"<!--\s*BEGIN FROZEN:\s*(\S+)\s*-->", spec_text)
    end_matches = re.findall(r"<!--\s*END FROZEN:\s*(\S+)\s*-->", spec_text)
    if set(begin_matches) != set(end_matches):
        violations.append(
            GrammarViolation(
                rule="unpaired_frozen_markers",
                message=(
                    f"Mismatched FROZEN markers: "
                    f"BEGIN={sorted(begin_matches)} END={sorted(end_matches)}"
                ),
            )
        )

    return violations


def validate_mutation(parent_spec: str, child_spec: str) -> list[GrammarViolation]:
    """Validate a mutation from parent_spec to child_spec.

    Checks all validate_spec rules on the child, plus:
      - FROZEN sections must be byte-for-byte identical between parent and child
      - No new duplicate headings introduced

    This is the deterministic screener that replaces the gameable LLM screener.
    """
    violations = validate_spec(child_spec)

    # FROZEN section integrity: byte-for-byte comparison
    parent_frozen = _extract_frozen_blocks(parent_spec)
    child_frozen = _extract_frozen_blocks(child_spec)

    for name, parent_content in parent_frozen.items():
        child_content = child_frozen.get(name)
        if child_content is None:
            violations.append(
                GrammarViolation(
                    rule="frozen_block_removed",
                    message=f"FROZEN block '{name}' was removed in mutation",
                )
            )
        elif parent_content != child_content:
            violations.append(
                GrammarViolation(
                    rule="frozen_block_modified",
                    message=f"FROZEN block '{name}' was modified (byte-for-byte comparison failed)",
                )
            )

    for name in child_frozen:
        if name not in parent_frozen:
            violations.append(
                GrammarViolation(
                    rule="frozen_block_added",
                    message=f"New FROZEN block '{name}' added in mutation (must be approved)",
                    fatal=False,
                )
            )

    return violations


def is_valid_spec(spec_text: str) -> bool:
    """Return True if the spec has no fatal grammar violations."""
    return not any(v.fatal for v in validate_spec(spec_text))


def is_valid_mutation(parent_spec: str, child_spec: str) -> bool:
    """Return True if the mutation has no fatal grammar violations."""
    return not any(v.fatal for v in validate_mutation(parent_spec, child_spec))


# ---------------------------------------------------------------------------
# Section extraction helpers
# ---------------------------------------------------------------------------


def evolvable_sections(spec_text: str) -> list[str]:
    """Return the list of ## section names that may be mutated.

    Evolvable = all ## sections EXCEPT those in FROZEN_SECTION_NAMES.
    """
    all_sections = _extract_h2_headings(spec_text)
    return [s for s in all_sections if s not in FROZEN_SECTION_NAMES]


def _extract_h2_headings(spec_text: str) -> list[str]:
    """Return all ## heading names in order of appearance."""
    return re.findall(r"^## (.+)$", spec_text, re.MULTILINE)


def _extract_frozen_blocks(spec_text: str) -> dict[str, str]:
    """Extract {block_name: content} for all FROZEN blocks (markers excluded)."""
    blocks: dict[str, str] = {}
    pattern = re.compile(
        r"<!--\s*BEGIN FROZEN:\s*(\S+)\s*-->(.*?)<!--\s*END FROZEN:\s*\1\s*-->",
        re.DOTALL,
    )
    for m in pattern.finditer(spec_text):
        blocks[m.group(1)] = m.group(2)
    return blocks
