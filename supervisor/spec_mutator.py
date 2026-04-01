"""Type 1 spec mutator — grammar-constrained refinement mutations.

Implements Type 1 (Refinement) from CAMBRIAN-SPEC-005 §Spec Mutation:
  Input:  one spec variant + one campaign's failure mode distribution
  Output: one section rewritten or one paragraph added
  Guard:  deterministic grammar validation (FROZEN check + required sections)

The LLM is used only for creative text generation. All structural constraints
are enforced deterministically by spec_grammar.validate_mutation(), replacing
the gameable LLM screener described in adversarial review §5.

Usage:
    mutated = await type1_mutate(spec_text, campaign_summary)
    violations = validate_grammar(original, mutated)  # deterministic check
"""

import os
from typing import Any

import anthropic
import structlog

from .spec_grammar import validate_mutation

log = structlog.get_logger(component="spec_mutator")

# Default model for creative mutations.
# Use Opus for quality; fall back to Sonnet via env var if budget is tight.
_DEFAULT_MUTATION_MODEL = os.environ.get(
    "CAMBRIAN_MUTATION_MODEL",
    os.environ.get("CAMBRIAN_ESCALATION_MODEL", "claude-opus-4-6"),
)

# Maximum tokens for the LLM to generate the mutated spec.
_MAX_TOKENS = int(os.environ.get("CAMBRIAN_MUTATION_MAX_TOKENS", "32768"))

# Maximum mutation attempts before giving up.
_MAX_ATTEMPTS = int(os.environ.get("CAMBRIAN_MUTATION_MAX_ATTEMPTS", "3"))


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


async def type1_mutate(
    spec_text: str,
    campaign_summary: dict[str, Any],
    target_section: str | None = None,
    model: str | None = None,
) -> str | None:
    """Generate one Type 1 (refinement) mutation of spec_text.

    Args:
        spec_text: The current spec to mutate.
        campaign_summary: CampaignSummary from the most recent campaign against
            this spec. Used to identify failure modes to address.
        target_section: If given, instruct the LLM to focus on this section.
            If None, the LLM selects the most impactful section.
        model: Override the mutation model. Defaults to CAMBRIAN_MUTATION_MODEL.

    Returns:
        Mutated spec text (complete spec, not a diff), or None if all attempts
        produced grammatically invalid mutations.
    """
    model = model or _DEFAULT_MUTATION_MODEL
    client = anthropic.AsyncAnthropic()

    prompt = _build_type1_prompt(spec_text, campaign_summary, target_section)

    for attempt in range(1, _MAX_ATTEMPTS + 1):
        log.info(
            "type1_mutate_attempt",
            attempt=attempt,
            model=model,
            target_section=target_section,
        )
        try:
            async with client.messages.stream(
                model=model,
                max_tokens=_MAX_TOKENS,
                messages=[{"role": "user", "content": prompt}],
            ) as stream:
                response = await stream.get_final_message()
        except anthropic.APIError as e:
            log.error("type1_mutate_api_error", attempt=attempt, error=str(e))
            continue

        mutated = _extract_spec_from_response(response.content[0].text)
        if mutated is None:
            log.warning("type1_mutate_no_spec_in_response", attempt=attempt)
            continue

        violations = validate_mutation(spec_text, mutated)
        fatal = [v for v in violations if v.fatal]
        if fatal:
            log.warning(
                "type1_mutate_grammar_violation",
                attempt=attempt,
                violations=[v.rule for v in fatal],
            )
            continue

        log.info("type1_mutate_success", attempt=attempt, model=model)
        return mutated

    log.error("type1_mutate_all_attempts_failed", max_attempts=_MAX_ATTEMPTS)
    return None


def propose_target_section(
    campaign_summary: dict[str, Any],
    evolvable: list[str],
) -> str | None:
    """Propose the section most likely to benefit from a Type 1 mutation.

    Uses failure_distribution to find the failure stage, then maps stage names
    to the spec sections most likely responsible.

    Returns the section name, or None if no obvious target.
    """
    failure_dist: dict[str, int] = campaign_summary.get("failure_distribution", {})
    if not failure_dist:
        return None

    # Remove 'none' (viable generations have no failure stage)
    non_viable = {k: v for k, v in failure_dist.items() if k != "none"}
    if not non_viable:
        return None

    # Find the most common failure stage
    dominant_stage = max(non_viable, key=lambda k: non_viable[k])

    # Map failure stage → most likely spec section to improve
    stage_to_section: dict[str, str] = {
        "manifest": "Artifact Manifest",
        "build": "Implementation Requirements",
        "test": "Acceptance Criteria",
        "start": "Implementation Requirements",
        "health": "Contracts",
    }

    candidate = stage_to_section.get(dominant_stage)
    if candidate and candidate in evolvable:
        return candidate

    # Fallback: pick any evolvable section not mapped
    return evolvable[0] if evolvable else None


# ---------------------------------------------------------------------------
# Prompt construction
# ---------------------------------------------------------------------------


def _build_type1_prompt(
    spec_text: str,
    campaign_summary: dict[str, Any],
    target_section: str | None,
) -> str:
    failure_dist = campaign_summary.get("failure_distribution", {})
    viability_rate = campaign_summary.get("viability_rate", 0.0)
    fitness_trend = campaign_summary.get("fitness_trend", 0.0)

    non_none = {k: v for k, v in failure_dist.items() if k != "none"}
    failure_summary = (
        ", ".join(f"{stage}: {count} failures" for stage, count in non_none.items())
        or "no specific failure stage identified"
    )

    if target_section:
        section_instruction = f"Focus your change on the section titled '## {target_section}'."
    else:
        section_instruction = (
            "Identify the single section where a small, targeted change"
            " would most improve Prime's success rate."
        )

    trend_word = (
        "improving" if fitness_trend > 0.05 else "declining" if fitness_trend < -0.05 else "flat"
    )

    return f"""You are helping evolve a software specification to improve the success rate \
of an AI-driven code generation system called Prime.

## Current Performance
- Viability rate: {viability_rate:.1%} (fraction of generation attempts that pass all tests)
- Fitness trend: {trend_word}
- Failure distribution: {failure_summary}

## Your Task
Make ONE small, targeted change to the specification below.
The change should directly address the failure modes described above.

Rules:
1. Output the COMPLETE specification — do not truncate or summarize.
2. Change ONLY ONE section (or add/modify ONE paragraph in one section).
3. Do NOT modify any text between <!-- BEGIN FROZEN --> and <!-- END FROZEN --> markers.
4. Do not change the ## heading names of any section.
5. Keep all MUST/MAY/SHALL keywords; do not weaken normative requirements.
6. {section_instruction}

## Specification to Mutate

{spec_text}

## Output Format

Output only the complete mutated specification, with no preamble or explanation.
Start immediately with the first line of the spec."""


# ---------------------------------------------------------------------------
# Response parsing
# ---------------------------------------------------------------------------


def _extract_spec_from_response(response_text: str) -> str | None:
    """Extract the spec from the LLM response.

    The LLM should output the spec directly. Strip any markdown code fences
    if the model wrapped the output in one.
    """
    text = response_text.strip()
    if not text:
        return None

    # Strip markdown code fence if present
    if text.startswith("```"):
        lines = text.splitlines()
        # Drop first line (```markdown or ```) and last ``` line
        inner_lines = []
        in_block = False
        for line in lines:
            if line.startswith("```") and not in_block:
                in_block = True
                continue
            if line.startswith("```") and in_block:
                break
            if in_block:
                inner_lines.append(line)
        text = "\n".join(inner_lines).strip()

    if not text:
        return None

    return text
