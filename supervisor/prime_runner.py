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
_MAX_TOKENS = int(os.environ.get("CAMBRIAN_TOKEN_BUDGET", "32768"))
_MAX_PARSE_RETRIES = int(os.environ.get("CAMBRIAN_MAX_PARSE_RETRIES", "3"))

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


def _write_manifest(
    artifact_dir: Path,
    generation: int,
    parent: int,
    spec_hash: str,
    artifact_hash: str,
    files: list[str],
    token_usage: dict[str, int],
    model: str,
) -> None:
    manifest = {
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
            "build": "pip install -r requirements.txt",
            "test": "python -m pytest tests/ -v",
            "start": "python -m src.prime",
            "health": "http://localhost:8401/health",
        },
    }
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
    history_json = json.dumps(history[-3:], indent=2)  # last 3 records for context
    base = (
        f"# Specification\n\n{spec_text}\n\n"
        f"# Generation History (recent)\n\n{history_json}\n\n"
        f"# Task\n\n"
        f"Produce a complete working codebase that implements the specification above.\n"
        f"Generation number: {generation}\n"
        f"Parent generation: {parent}\n"
    )
    if failed_context:
        diagnostics = failed_context.get("diagnostics", {})
        stage = diagnostics.get("stage", "unknown")
        summary = diagnostics.get("summary", "")
        base += (
            f"\n# Previous Attempt Failed\n\n"
            f"Stage: {stage}\nSummary: {summary}\n"
            f"Fix the issues and try again.\n"
        )
    return base


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
) -> tuple[Path, str, dict[str, int]]:
    """Generate one artifact from spec_text via LLM.

    Writes all source files, copies the spec, writes manifest.json.

    Args:
        spec_text: The spec to generate from.
        generation: Target generation number.
        parent: Parent generation number.
        artifacts_root: Root of the artifacts repo.
        history: Recent generation records for context (optional).
        artifact_rel: Relative path within artifacts_root (default: gen-{generation}).
        model: LLM model to use (default: CAMBRIAN_MODEL env var).

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

    failed_context: dict[str, Any] | None = None
    token_usage: dict[str, int] = {"input": 0, "output": 0}

    for attempt in range(1, _MAX_PARSE_RETRIES + 1):
        prompt = _build_prompt(spec_text, history, generation, parent, failed_context)

        log.info(
            "prime_runner_llm_call",
            generation=generation,
            attempt=attempt,
            model=model,
        )
        response = await client.messages.create(
            model=model,
            max_tokens=_MAX_TOKENS,
            system=SYSTEM_PROMPT,
            messages=[{"role": "user", "content": prompt}],
        )
        token_usage["input"] += response.usage.input_tokens
        token_usage["output"] += response.usage.output_tokens

        raw = response.content[0].text
        try:
            files = parse_files(raw)
        except ParseError as e:
            log.warning("prime_runner_parse_error", attempt=attempt, error=str(e))
            failed_context = {"diagnostics": {"stage": "parse", "summary": str(e)}}
            continue

        # Write source files
        for rel_path, content in files.items():
            file_path = artifact_dir / rel_path
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
            model=model,
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
        f"generate_artifact: failed to parse LLM response after {_MAX_PARSE_RETRIES} attempts"
        f" for generation {generation}"
    )
