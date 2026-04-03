"""Prime runner — generate one artifact from a spec, ready for /spawn.

M2 bridge: the campaign runner calls generate_artifact() to invoke an LLM,
parse the response into files, write them to the artifacts repo, and return
the artifact path. The caller then POSTs /spawn with that path.

This replaces the need for a running Prime container in M2 campaign mode.
The generation logic mirrors gen-10 Prime (generate.py + manifest.py) but
is self-contained here so M2 is not coupled to a specific Prime generation.
"""

import hashlib
import json
import os
import re
import shutil
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import anthropic
import structlog

log = structlog.get_logger(component="prime_runner")

_DEFAULT_MODEL = os.environ.get("CAMBRIAN_MODEL", "claude-sonnet-4-6")
_ESCALATION_MODEL = os.environ.get("CAMBRIAN_ESCALATION_MODEL", "claude-opus-4-6")
_PER_CALL_MAX_TOKENS = 32768  # Fixed per-call limit; CAMBRIAN_TOKEN_BUDGET is cumulative (deferred)
_MAX_PARSE_RETRIES = int(os.environ.get("CAMBRIAN_MAX_PARSE_RETRIES", "2"))

SYSTEM_PROMPT = """\
You are a code generator. You produce complete, working Python codebases from specifications.

Rules:
- Output ONLY <file path="...">content</file:end> blocks. One block per file.
- Every file needed to build, test, and run the project must be in a <file> block.
- Include a requirements.txt with all dependencies.
- Include a test suite that exercises all functionality.
- The code must work in Python 3.14 inside a Docker container with a venv at /venv.
- Do NOT include manifest.json — it is generated separately.
- Do NOT include the spec file — it is copied separately.
- Python 3.14 STRICT: string literals MUST NOT contain unescaped newlines. Use
  triple quotes (\\"\\"\\" or ''') for multi-line strings. Use \\n for embedded newlines in
  single-line strings. A bare newline inside \\"...\\" or '...' is a SyntaxError.
- Test strings that embed XML-like content MUST use raw strings (r\\"...\\") or
  triple-quoted strings to avoid escaping issues.

Critical patterns (these cause the most failures):
- structlog: first positional arg IS the event key. Correct: log.info("prime_starting",
  generation=1). WRONG: log.info("event", event="x") — TypeError: duplicate 'event'.
  WRONG: log.info("Starting gen %d", n) — structlog does not interpolate.
- Tests: every test function MUST import its symbols locally (e.g.
  from src.prime import make_app). A local import in test_health() is NOT visible
  to test_stats(). Do NOT rely on module-level imports.
- Tests: use the aiohttp_client pytest fixture. Do NOT use AioHTTPTestCase or
  @unittest_run_loop — both are deprecated and broken in aiohttp 3.8+.
- LLM calls: MUST use client.messages.stream(), NOT client.messages.create().
  The SDK raises an error for large max_tokens with non-streaming calls.
- JSON field names in wire format use kebab-case (created-at, spec-hash),
  NOT snake_case. Python variables use snake_case internally.
"""


# ---------------------------------------------------------------------------
# File parsing
# ---------------------------------------------------------------------------


class ParseError(Exception):
    pass


def parse_files(response: str) -> dict[str, str]:
    """Parse <file path="...">content</file:end> blocks from LLM response."""
    current_path: str | None = None
    current_lines: list[str] = []
    files: dict[str, str] = {}

    for line in response.splitlines(keepends=True):
        if current_path is None:
            m = re.match(r'<file path="([^"]+)">', line)
            if m:
                current_path = m.group(1)
                current_lines = []
        elif line.rstrip("\n\r") == "</file:end>":
            files[current_path] = "".join(current_lines)
            current_path = None
        else:
            current_lines.append(line)

    if current_path is not None:
        raise ParseError(f"Unclosed <file path={current_path!r}> block")
    if not files:
        raise ParseError("No <file ...> blocks found in LLM response")
    return files


def _resolve_artifact_path(artifact_dir: Path, rel_path: str) -> Path:
    """Resolve a relative artifact path and reject traversal/absolute paths."""
    if not rel_path or not rel_path.strip():
        raise ValueError("empty artifact path")
    rel = Path(rel_path)
    if rel.is_absolute() or rel.anchor:
        raise ValueError(f"absolute artifact path: {rel_path}")
    root = artifact_dir.resolve()
    resolved = (artifact_dir / rel).resolve()
    try:
        resolved.relative_to(root)
    except ValueError as exc:
        raise ValueError(f"artifact path escapes root: {rel_path}") from exc
    return resolved


# ---------------------------------------------------------------------------
# Manifest
# ---------------------------------------------------------------------------


def _compute_artifact_hash(artifact_root: Path, files: list[str]) -> str:
    """Per CAMBRIAN-SPEC-005 algorithm — must match supervisor.compute_artifact_hash."""
    hasher = hashlib.sha256()
    for rel_path in sorted(files):
        if rel_path == "manifest.json":
            continue
        hasher.update(rel_path.encode())
        hasher.update(b"\0")
        hasher.update((artifact_root / rel_path).read_bytes())
    return f"sha256:{hasher.hexdigest()}"


def _compute_spec_hash(spec_text: str) -> str:
    return f"sha256:{hashlib.sha256(spec_text.encode()).hexdigest()}"


def _extract_contracts(spec_text: str) -> list[dict[str, Any]] | None:
    """Extract the contracts JSON array from a ```contracts fenced block in spec_text.

    Per CAMBRIAN-SPEC-005 §6: if the spec contains a JSON array under a fenced code
    block marked with 'contracts', include it verbatim as the manifest contracts field.
    Returns None if no such block is found or the content isn't valid JSON.
    """
    m = re.search(r"```contracts\s*\n(.*?)```", spec_text, re.DOTALL)
    if not m:
        return None
    try:
        data = json.loads(m.group(1).strip())
    except (json.JSONDecodeError, ValueError):
        return None
    return data if isinstance(data, list) else None


def _write_manifest(
    artifact_dir: Path,
    generation: int,
    parent: int,
    spec_hash: str,
    artifact_hash: str,
    files: list[str],
    token_usage: dict[str, int],
    model: str,
    spec_text: str,
) -> None:
    manifest: dict[str, Any] = {
        "cambrian-version": 1,
        "generation": generation,
        "parent-generation": parent,
        "spec-hash": spec_hash,
        "artifact-hash": artifact_hash,
        "producer-model": model,
        "token-usage": token_usage,
        "files": files,
        "created-at": datetime.now(UTC).isoformat(),
        "entry": {
            "build": "uv pip install -r requirements.txt",
            "test": "python -m pytest tests/ -v",
            "start": "python -m src.prime",
            "health": "http://localhost:8401/health",
        },
    }
    contracts = _extract_contracts(spec_text)
    if contracts is not None:
        manifest["contracts"] = contracts
    (artifact_dir / "manifest.json").write_text(json.dumps(manifest, indent=2))


# ---------------------------------------------------------------------------
# Prompt building
# ---------------------------------------------------------------------------


def _build_prompt(
    spec_text: str,
    history: list[dict[str, Any]],
    generation: int,
    parent: int,
    failed_context: dict[str, Any] | None = None,
) -> str:
    history_json = json.dumps(history, indent=2)
    base = (
        f"# Specification\n\n{spec_text}\n\n"
        f"# Generation History\n\n{history_json}\n\n"
        f"# Task\n\n"
        f"Produce a complete working codebase that implements the specification above.\n"
        f"Generation number: {generation}\n"
        f"Parent generation: {parent}\n"
    )
    if failed_context:
        diagnostics = failed_context.get("diagnostics", {})
        stage = diagnostics.get("stage", "unknown")
        summary = diagnostics.get("summary", "")
        artifact_dir: Path | None = failed_context.get("artifact_dir")

        base += (
            f"\n# Previous Attempt Failed\n\n"
            f"Generation {generation - 1} failed at stage: {stage}\n"
            f"Summary: {summary}\n"
        )

        if artifact_dir is not None and artifact_dir.exists():
            base += "\n## Failed Source Code\n\n"
            for src_file in sorted(artifact_dir.rglob("*")):
                if not src_file.is_file():
                    continue
                rel = src_file.relative_to(artifact_dir)
                name = str(rel)
                if name in ("manifest.json",) or name.startswith("spec/"):
                    continue
                suffix = src_file.suffix.lstrip(".")
                try:
                    content = src_file.read_text(encoding="utf-8", errors="replace")
                except OSError:
                    continue
                base += f"### {name}\n```{suffix}\n{content}\n```\n\n"

        base += f"\n## Diagnostics\n\n{json.dumps(diagnostics, indent=2)}\n\n"
        base += (
            "# Task\n\n"
            "The previous attempt failed. Study the failed code and diagnostics above.\n"
            "Produce a complete, corrected codebase that fixes the identified issues.\n"
            f"Generation number: {generation}\n"
            f"Parent generation: {parent}\n"
        )
    return base


def _build_parse_repair_prompt(raw: str, error: str) -> str:
    return (
        f"# Parse Error\n\n"
        f"The previous response could not be parsed. Error: {error}\n\n"
        f"# Malformed Response\n\n"
        f"{raw}\n\n"
        f"# Task\n\n"
        f"Re-emit the EXACT SAME files using the correct format. "
        f"Every <file> block MUST have a\n"
        f"matching </file:end> on its own line. No nesting. "
        f"No extra content between blocks."
    )


# ---------------------------------------------------------------------------
# Core generation
# ---------------------------------------------------------------------------


async def generate_artifact(
    spec_text: str,
    generation: int,
    parent: int,
    artifacts_root: Path,
    history: list[dict[str, Any]] | None = None,
    artifact_rel: str | None = None,
    model: str | None = None,
    failed_context: dict[str, Any] | None = None,
) -> tuple[Path, str, dict[str, int]]:
    """Generate one artifact from spec_text via LLM.

    Writes all source files, copies the spec, writes manifest.json.

    Args:
        spec_text: The spec to generate from.
        generation: Target generation number.
        parent: Parent generation number.
        artifacts_root: Root of the artifacts repo.
        history: Generation records for context (optional, full history passed).
        artifact_rel: Relative path within artifacts_root (default: gen-{generation}).
        model: LLM model to use (default: CAMBRIAN_MODEL env var).
        failed_context: Diagnostics from a previous gen's Test Rig failure.
            When provided, _build_prompt() adds a "Previous Attempt Failed"
            section with the failed source code and diagnostics so the LLM
            can learn from the failure.

    Returns:
        (artifact_path, spec_hash, token_usage)
    """
    model = model or _DEFAULT_MODEL
    history = history or []
    artifact_rel = artifact_rel or f"gen-{generation}"
    artifact_dir = artifacts_root / artifact_rel
    artifact_dir.mkdir(parents=True, exist_ok=True)

    spec_hash = _compute_spec_hash(spec_text)
    client = anthropic.AsyncAnthropic()
    token_usage: dict[str, int] = {"input": 0, "output": 0}

    for attempt in range(1, _MAX_PARSE_RETRIES + 1):
        call_model = _ESCALATION_MODEL if attempt > 1 else model
        prompt = _build_prompt(spec_text, history, generation, parent, failed_context)

        log.info(
            "prime_runner_llm_call",
            generation=generation,
            attempt=attempt,
            model=call_model,
        )
        async with client.messages.stream(
            model=call_model,
            max_tokens=_PER_CALL_MAX_TOKENS,
            system=SYSTEM_PROMPT,
            messages=[{"role": "user", "content": prompt}],
        ) as stream:
            response = await stream.get_final_message()
        token_usage["input"] += response.usage.input_tokens
        token_usage["output"] += response.usage.output_tokens

        raw = response.content[0].text

        # Parse with in-place repair loop (does not consume a generation retry).
        files: dict[str, str] | None = None
        parse_error: str | None = None
        repair_raw = raw
        for repair_attempt in range(_MAX_PARSE_RETRIES + 1):
            try:
                files = parse_files(repair_raw)
                break
            except ParseError as e:
                parse_error = str(e)
                if repair_attempt == _MAX_PARSE_RETRIES:
                    break
                log.warning(
                    "prime_runner_parse_error",
                    attempt=attempt,
                    repair_attempt=repair_attempt + 1,
                    error=parse_error,
                )
                repair_prompt = _build_parse_repair_prompt(repair_raw, parse_error)
                async with client.messages.stream(
                    model=call_model,
                    max_tokens=_PER_CALL_MAX_TOKENS,
                    system=SYSTEM_PROMPT,
                    messages=[{"role": "user", "content": repair_prompt}],
                ) as repair_stream:
                    repair_response = await repair_stream.get_final_message()
                token_usage["input"] += repair_response.usage.input_tokens
                token_usage["output"] += repair_response.usage.output_tokens
                repair_raw = repair_response.content[0].text

        if files is None:
            log.warning(
                "prime_runner_parse_failed_all_repairs",
                attempt=attempt,
                error=parse_error,
            )
            failed_context = {
                "diagnostics": {"stage": "parse", "summary": parse_error or "parse failed"},
                "artifact_dir": artifact_dir,
            }
            continue

        # Write source files
        for rel_path, content in files.items():
            file_path = _resolve_artifact_path(artifact_dir, rel_path)
            file_path.parent.mkdir(parents=True, exist_ok=True)
            file_path.write_text(content)

        # Copy spec into artifact
        spec_dest = artifact_dir / "spec" / "CAMBRIAN-SPEC-005.md"
        spec_dest.parent.mkdir(parents=True, exist_ok=True)
        spec_dest.write_text(spec_text)

        all_files = sorted(
            str(p.relative_to(artifact_dir))
            for p in artifact_dir.rglob("*")
            if p.is_file() and p.name != "manifest.json"
        )

        artifact_hash = _compute_artifact_hash(artifact_dir, all_files)
        _write_manifest(
            artifact_dir,
            generation=generation,
            parent=parent,
            spec_hash=spec_hash,
            artifact_hash=artifact_hash,
            files=["manifest.json"] + all_files,
            token_usage=token_usage,
            model=call_model,
            spec_text=spec_text,
        )

        log.info(
            "prime_runner_artifact_written",
            generation=generation,
            artifact_dir=str(artifact_dir),
            files=len(all_files),
            tokens_in=token_usage["input"],
            tokens_out=token_usage["output"],
        )
        return artifact_dir, spec_hash, token_usage

    # All retries failed — clean up and raise
    shutil.rmtree(artifact_dir, ignore_errors=True)
    raise RuntimeError(
        f"generate_artifact: failed after {_MAX_PARSE_RETRIES} attempts for generation {generation}"
    )
