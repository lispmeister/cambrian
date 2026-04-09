"""
Probe: does Opus 4.6 generate syntactically valid Python 3.14 for test_generate.py?

Tests the hypothesis that Sonnet 4.6's failure to produce valid Python 3.14 string
literals in test_generate.py is a model-capability issue that a stronger model fixes.

Usage:
    python scripts/probe_opus_quoting.py [--runs N]

Runs the generation N times (default: 3) and reports pass/fail for each attempt.
"""

from __future__ import annotations

import ast
import asyncio
import re
from argparse import ArgumentParser

import anthropic

# ---------------------------------------------------------------------------
# The exact SYSTEM_PROMPT from gen-1-new/src/generate.py (v0.10.4)
# This is what offspring Prime instances receive; we use it verbatim.
# ---------------------------------------------------------------------------

SYSTEM_PROMPT = """\
You are a code generator. You produce complete, working Python codebases from specifications.

Rules:
- Output ONLY <file path="...">content</file> blocks. One block per file.
- Every file needed to build, test, and run the project must be in a <file> block.
- Include a requirements.txt with all dependencies.
- Include a test suite that exercises all functionality.
- The code must work in Python 3.14 inside a Docker container with a venv at /venv.
- Do NOT include manifest.json — it is generated separately.
- Do NOT include the spec file — it is copied separately.
- Python 3.14 STRICT: string literals MUST NOT contain unescaped newlines. Use triple
  quotes (\"\"\" or ''') for multi-line strings. Use \\n for embedded newlines in
  single-line strings. A bare newline inside "..." or '...' is a SyntaxError.
- Test strings that embed XML-like content (e.g. <file> blocks) MUST use raw strings
  (r"...") or triple-quoted strings to avoid escaping issues.
"""

# ---------------------------------------------------------------------------
# The source under test: parse_files() from generate.py
# Providing this gives the LLM the exact function signature and behaviour to test.
# ---------------------------------------------------------------------------

PARSE_FILES_SOURCE = '''\
FILE_PATTERN = re.compile(r\'<file path="([^"]+)">(.*?)</file>\', re.DOTALL)


def parse_files(response: str) -> dict[str, str]:
    """Extract files from <file path="...">content</file> blocks.

    Returns a dict mapping file paths to their contents.
    Strips leading/trailing newlines and normalises line endings to LF.
    Returns an empty dict if no file blocks are found (malformed response).
    """
    matches = FILE_PATTERN.findall(response)
    if not matches:
        return {}
    return {path: content.replace("\\r\\n", "\\n").strip("\\n") for path, content in matches}
'''

# ---------------------------------------------------------------------------
# Prompt
# ---------------------------------------------------------------------------

USER_PROMPT = f"""\
# Task

Generate ONLY the file `tests/test_generate.py`.

This file must contain a pytest test suite for the `parse_files()` function shown below.

## Function under test

```python
{PARSE_FILES_SOURCE}
```

## Requirements

- Import `parse_files` from `src.generate`.
- Test: single `<file>` block (one-liner content).
- Test: multiple `<file>` blocks in one response.
- Test: multi-line file content inside a `<file>` block (content spans multiple lines).
- Test: leading/trailing newlines stripped from content.
- Test: CRLF line endings normalised to LF.
- Test: text outside `<file>` blocks is ignored.
- Test: empty string input returns empty dict.
- Test: input with no `<file>` blocks returns empty dict.

## Critical constraint

Test fixture strings that contain `<file>` tags with multi-line content MUST use
triple-quoted strings or string concatenation with \\n escapes — never a bare newline
inside a single- or double-quoted string literal.

Output a single `<file path="tests/test_generate.py">...</file>` block.
"""

FILE_PATTERN = re.compile(r'<file path="([^"]+)">(.*?)</file>', re.DOTALL)

MODEL = "claude-opus-4-6"


async def run_once(client: anthropic.AsyncAnthropic, run_index: int) -> dict:
    print(f"\n{'=' * 60}")
    print(f"Run {run_index + 1} — {MODEL}")
    print("=" * 60)

    for attempt in range(3):
        try:
            async with client.messages.stream(
                model=MODEL,
                max_tokens=8192,
                system=SYSTEM_PROMPT,
                messages=[{"role": "user", "content": USER_PROMPT}],
            ) as stream:
                message = await stream.get_final_message()
            break
        except anthropic.APIStatusError as exc:
            if attempt < 2:
                print(f"  API error (attempt {attempt + 1}/3): {exc.message} — retrying")
                await asyncio.sleep(5)
            else:
                print(f"  API error: {exc.message} — giving up")
                return {
                    "run": run_index + 1,
                    "passed": False,
                    "error": f"API error: {exc.message}",
                    "code": None,
                }

    text = next((b.text for b in message.content if b.type == "text"), "")
    input_tokens = message.usage.input_tokens
    output_tokens = message.usage.output_tokens
    print(f"Tokens: {input_tokens} in / {output_tokens} out")

    matches = FILE_PATTERN.findall(text)
    if not matches:
        print("FAIL — no <file> blocks found in response")
        print("\nRaw response (first 500 chars):")
        print(text[:500])
        return {"run": run_index + 1, "passed": False, "error": "no file blocks", "code": None}

    # The LLM uses <file> blocks as its own output format AND the test fixtures contain
    # <file> blocks as data.  The non-greedy regex will match inner <file>...</file> pairs
    # first, fragmenting the outer test_generate.py block.  Find it greedily by scanning
    # for the outermost <file path="tests/test_generate.py"> ... </file> span.
    target_code = None
    target_match = re.search(
        r'<file path="tests/test_generate\.py">(.*?)</file>',
        text,
        re.DOTALL,
    )
    if target_match:
        # This may still be truncated by a nested </file>.  Use a greedy pass to get
        # everything between the opening tag and the LAST </file> in the response.
        open_tag = '<file path="tests/test_generate.py">'
        start_idx = text.find(open_tag)
        if start_idx != -1:
            body_start = start_idx + len(open_tag)
            # Find the last </file> that plausibly closes this block (greedy)
            end_idx = text.rfind("</file>")
            if end_idx > body_start:
                target_code = text[body_start:end_idx].replace("\r\n", "\n").strip("\n")

    if target_code is None:
        print("FAIL — tests/test_generate.py not found in response")
        return {"run": run_index + 1, "passed": False, "error": "target file absent", "code": None}

    print(f"\nFile: tests/test_generate.py  ({len(target_code.splitlines())} lines)")
    try:
        ast.parse(target_code)
        print("  Syntax: OK")
        passed = True
        error = None
    except SyntaxError as exc:
        passed = False
        error = f"SyntaxError at line {exc.lineno}: {exc.msg}"
        print(f"  Syntax: FAIL — {error}")
        lines = target_code.splitlines()
        if exc.lineno:
            start = max(0, exc.lineno - 3)
            end = min(len(lines), exc.lineno + 2)
            print("  Context:")
            for i, line in enumerate(lines[start:end], start=start + 1):
                marker = " >>>" if i == exc.lineno else "    "
                print(f"  {marker} {i:3d}  {line}")

    return {
        "run": run_index + 1,
        "passed": passed,
        "error": error,
        "code": target_code,
    }


async def main(runs: int) -> None:
    client = anthropic.AsyncAnthropic()

    outcomes = []
    for i in range(runs):
        result = await run_once(client, i)
        outcomes.append(result)

    print(f"\n{'=' * 60}")
    print("SUMMARY")
    print("=" * 60)
    passed = sum(1 for r in outcomes if r["passed"])
    print(f"Passed: {passed}/{runs}")
    for r in outcomes:
        status = "PASS" if r["passed"] else f"FAIL ({r['error']})"
        print(f"  Run {r['run']}: {status}")

    if passed < runs:
        print("\nConclusion: model still produces invalid Python 3.14 syntax.")
        print("Consider: fixture files, stronger prompt, or escalation policy.")
    else:
        print("\nConclusion: model generates valid syntax. Escalation to Opus is a viable fix.")


if __name__ == "__main__":
    parser = ArgumentParser(description=__doc__)
    parser.add_argument("--runs", type=int, default=3, help="Number of generation attempts")
    args = parser.parse_args()
    asyncio.run(main(args.runs))
