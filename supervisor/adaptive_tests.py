"""Adaptive test generation — Tier X co-evolved tests.

After each failed campaign, extracts the dominant failure mode and asks an LLM
to generate 3-5 targeted pytest test cases probing that exact failure. These
supplement (never replace) the fixed Tier 0-3 viability checks.

Design from adversarial review §9 and bead cambrian-3qo:
  - Tests are generated per campaign failure and stored with an expiry counter.
  - Each test expires after EXPIRE_AFTER campaigns (default 5).
  - Active tests are capped at MAX_ACTIVE (default 10) to prevent bloat.
  - Tests are stored in adaptive-tests.json in the artifacts repo.

Inspired by: Absolute Zero Reasoner (arXiv 2505.03335) — self-generated tests
as a co-evolutionary pressure orthogonal to the fixed evaluation curriculum.

Caution (adversarial review §9): co-evolved tests can drift toward irrelevant
edge cases. The fixed tiers anchor viability; adaptive tests probe failure surfaces
within those tiers only.
"""

import json
import os
import re
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import anthropic
import structlog

log = structlog.get_logger(component="adaptive_tests")

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

EXPIRE_AFTER = int(os.environ.get("CAMBRIAN_ADAPTIVE_EXPIRE_AFTER", "5"))
MAX_ACTIVE = int(os.environ.get("CAMBRIAN_ADAPTIVE_MAX_ACTIVE", "10"))
TESTS_PER_GENERATION = int(os.environ.get("CAMBRIAN_ADAPTIVE_TESTS_PER_GENERATION", "4"))
_DEFAULT_MODEL = os.environ.get(
    "CAMBRIAN_ADAPTIVE_MODEL",
    os.environ.get("CAMBRIAN_ESCALATION_MODEL", "claude-sonnet-4-6"),
)
_MAX_TOKENS = int(os.environ.get("CAMBRIAN_ADAPTIVE_MAX_TOKENS", "2048"))

# Path within the artifacts root where adaptive tests are stored.
_TESTS_FILENAME = "adaptive-tests.json"


# ---------------------------------------------------------------------------
# Storage helpers
# ---------------------------------------------------------------------------


def _tests_path(artifacts_root: str | None = None) -> Path:
    root = artifacts_root or os.environ.get("CAMBRIAN_ARTIFACTS_ROOT", "../cambrian-artifacts")
    return Path(root) / _TESTS_FILENAME


def load_tests(artifacts_root: str | None = None) -> list[dict[str, Any]]:
    """Load all adaptive tests from the artifacts repo."""
    path = _tests_path(artifacts_root)
    if not path.exists():
        return []
    return json.loads(path.read_text())


def save_tests(tests: list[dict[str, Any]], artifacts_root: str | None = None) -> None:
    """Persist the adaptive test list to the artifacts repo."""
    path = _tests_path(artifacts_root)
    path.write_text(json.dumps(tests, indent=2))


# ---------------------------------------------------------------------------
# Lifecycle helpers
# ---------------------------------------------------------------------------


def get_active_tests(
    campaign_index: int,
    artifacts_root: str | None = None,
) -> list[dict[str, Any]]:
    """Return adaptive tests that are still within their expiry window.

    Args:
        campaign_index: The 0-based index of the current campaign.

    Returns:
        Up to MAX_ACTIVE unexpired tests, newest first.
    """
    all_tests = load_tests(artifacts_root)
    active = [
        t for t in all_tests if campaign_index < t["created_at_campaign"] + t["expires_after"]
    ]
    # Newest first, then cap
    active.sort(key=lambda t: t["created_at_campaign"], reverse=True)
    return active[:MAX_ACTIVE]


def expire_old_tests(
    campaign_index: int,
    artifacts_root: str | None = None,
) -> int:
    """Remove expired tests from storage. Returns count removed."""
    all_tests = load_tests(artifacts_root)
    active = [
        t for t in all_tests if campaign_index < t["created_at_campaign"] + t["expires_after"]
    ]
    removed = len(all_tests) - len(active)
    if removed > 0:
        save_tests(active, artifacts_root)
        log.info("adaptive_tests_expired", removed=removed, remaining=len(active))
    return removed


# ---------------------------------------------------------------------------
# Test generation
# ---------------------------------------------------------------------------


async def generate_adaptive_tests(
    campaign_summary: dict[str, Any],
    spec_text: str,
    campaign_index: int,
    artifacts_root: str | None = None,
    model: str | None = None,
    n: int = TESTS_PER_GENERATION,
) -> list[dict[str, Any]]:
    """Generate n targeted test cases for the failure mode in campaign_summary.

    Skips generation if the campaign was fully viable (nothing to probe).
    Stores generated tests in the artifacts repo.

    Args:
        campaign_summary: Output of compute_campaign_summary().
        spec_text: The spec text used in this campaign.
        campaign_index: 0-based campaign counter (for expiry tracking).
        artifacts_root: Override for the artifacts root path.
        model: Override the model used for generation.
        n: Number of test cases to request (default TESTS_PER_GENERATION).

    Returns:
        List of newly generated test dicts (may be empty on skip or failure).
    """
    viability = campaign_summary.get("viability_rate", 0.0)
    if viability >= 1.0:
        log.info("adaptive_tests_skipped", reason="fully_viable", campaign=campaign_index)
        return []

    failure_dist: dict[str, int] = campaign_summary.get("failure_distribution", {})
    non_viable = {k: v for k, v in failure_dist.items() if k != "none"}
    if not non_viable:
        log.info("adaptive_tests_skipped", reason="no_failures", campaign=campaign_index)
        return []

    dominant_stage = max(non_viable, key=lambda k: non_viable[k])
    model = model or _DEFAULT_MODEL
    client = anthropic.AsyncAnthropic()

    prompt = _build_test_gen_prompt(campaign_summary, spec_text, dominant_stage, n)

    try:
        async with client.messages.stream(
            model=model,
            max_tokens=_MAX_TOKENS,
            messages=[{"role": "user", "content": prompt}],
        ) as stream:
            response = await stream.get_final_message()
    except anthropic.APIError as e:
        log.error("adaptive_tests_api_error", error=str(e))
        return []

    raw_text = response.content[0].text
    test_cases = _extract_test_cases(raw_text)
    if not test_cases:
        log.warning("adaptive_tests_no_cases_extracted", campaign=campaign_index)
        return []

    now = datetime.now(UTC).isoformat()
    new_tests = [
        {
            "test_id": f"adaptive-{campaign_index}-{i}",
            "test_code": code,
            "failure_stage": dominant_stage,
            "campaign_id": campaign_summary.get("campaign_id", "unknown"),
            "created_at_campaign": campaign_index,
            "expires_after": EXPIRE_AFTER,
            "created_at": now,
        }
        for i, code in enumerate(test_cases[:n])
    ]

    # Append to existing tests and persist
    all_tests = load_tests(artifacts_root) + new_tests
    save_tests(all_tests, artifacts_root)

    log.info(
        "adaptive_tests_generated",
        campaign=campaign_index,
        count=len(new_tests),
        failure_stage=dominant_stage,
    )
    return new_tests


# ---------------------------------------------------------------------------
# Prompt construction and response parsing
# ---------------------------------------------------------------------------


def _build_test_gen_prompt(
    campaign_summary: dict[str, Any],
    spec_text: str,
    dominant_stage: str,
    n: int,
) -> str:
    viability = campaign_summary.get("viability_rate", 0.0)
    failure_dist = campaign_summary.get("failure_distribution", {})
    failure_lines = "\n".join(
        f"  - {stage}: {count}" for stage, count in failure_dist.items() if stage != "none"
    )

    stage_guidance = {
        "manifest": (
            "The generated manifest.json was missing required fields or had wrong types. "
            "Write tests that validate manifest structure: required keys, cambrian-version=1, "
            "valid entry commands, files list non-empty."
        ),
        "build": (
            "The artifact failed to build (pip install or equivalent). "
            "Write tests that validate requirements.txt is present and well-formed, "
            "all imports resolve, no syntax errors in Python files."
        ),
        "test": (
            "The artifact's own test suite failed. "
            "Write tests that probe specific assertions: HTTP status codes, "
            "response body structure, edge cases the generated tests might miss."
        ),
        "start": (
            "The artifact's server failed to start within the timeout. "
            "Write tests that validate the server startup sequence: "
            "port binding, signal handling, startup log messages."
        ),
        "health": (
            "The health check failed: contracts or spec vectors did not pass. "
            "Write tests that validate the /health and /stats endpoints "
            "return the exact schema the spec requires."
        ),
    }.get(dominant_stage, "Write tests that probe the failure mode directly.")

    return f"""You are writing pytest test cases to probe a specific failure mode in a \
code generation system.

## Failure Context
- Viability rate: {viability:.1%}
- Dominant failure stage: {dominant_stage}
- Failure distribution:
{failure_lines}

## What to write
{stage_guidance}

Write exactly {n} pytest test functions. Each test should:
1. Be standalone (import what it needs, mock external calls)
2. Test ONE specific behaviour that could cause a `{dominant_stage}` stage failure
3. Use standard pytest assertions (not `assert True`)
4. Be runnable against a minimal HTTP server on localhost:8401

## Output format
Output ONLY the test functions as raw Python code, separated by blank lines.
No imports at the top level — each test should include its own imports.
No class wrapper. Start each function with `def test_`.

Example format:
def test_health_returns_200():
    import urllib.request
    resp = urllib.request.urlopen("http://localhost:8401/health")
    assert resp.status == 200

Now write {n} test functions for the `{dominant_stage}` failure mode:"""


def _extract_test_cases(response_text: str) -> list[str]:
    """Extract individual test functions from LLM output.

    Splits on `def test_` boundaries. Returns a list of function strings,
    each starting with `def test_`.
    """
    text = response_text.strip()

    # Strip markdown code fences if present
    if text.startswith("```"):
        lines = text.splitlines()
        inner: list[str] = []
        in_block = False
        for line in lines:
            if line.startswith("```") and not in_block:
                in_block = True
                continue
            if line.startswith("```") and in_block:
                break
            if in_block:
                inner.append(line)
        text = "\n".join(inner).strip()

    # Split on def test_ boundaries
    parts = re.split(r"(?=^def test_)", text, flags=re.MULTILINE)
    cases = []
    for part in parts:
        stripped = part.strip()
        if stripped.startswith("def test_"):
            cases.append(stripped)
    return cases
