"""
Cambrian Test Rig — mechanical verification pipeline.

Stages: manifest → build → test → start → health → report

Run inside a container with the artifact mounted at /workspace.
Writes viability-report.json to /output on completion.
"""

import contextlib
import json
import os
import re
import signal
import socket
import subprocess
import sys
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import structlog

WORKSPACE = Path(os.environ.get("CAMBRIAN_WORKSPACE", "/workspace"))
# Output directory for the viability report — separate from /workspace so the organism
# (which runs in /workspace) cannot predict or overwrite it.
OUTPUT_DIR = Path(os.environ.get("CAMBRIAN_OUTPUT_DIR", "/output"))
# Baseline battery for dual-run regression testing (M3). Mounted read-only by Supervisor.
BASELINE_PATH = Path(os.environ.get("CAMBRIAN_BASELINE_PATH", "/baseline/battery.json"))
HEALTH_TIMEOUT = 10  # seconds per health-check request
TCP_READINESS_TIMEOUT = 30  # seconds to wait for port
TCP_POLL_INTERVAL = 0.5
CAMBRIAN_VERSION = 1

# All 5 stage names in order — used to populate unattempted checks
ALL_STAGES = ["manifest", "build", "test", "start", "health"]

log = structlog.get_logger(component="test_rig")


# ---------------------------------------------------------------------------
# Stage 1: Manifest validation
# ---------------------------------------------------------------------------

_SPEC_HASH_RE = re.compile(r"^sha256:[0-9a-f]{64}$")
_ISO8601_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}")
_URL_RE = re.compile(r"^https?://")
_SCRIPT_FORM_RE = re.compile(r"(^|\s)python[3]?\s+\S+\.py\b")


def _validate_manifest(manifest: dict[str, Any]) -> list[str]:
    """Validate all MUST fields. Returns list of error strings (empty = valid)."""
    errors: list[str] = []

    # cambrian-version
    v = manifest.get("cambrian-version")
    if not isinstance(v, int):
        errors.append(f"cambrian-version: expected integer, got {type(v).__name__}")
    elif v != CAMBRIAN_VERSION:
        errors.append(f"cambrian-version: expected {CAMBRIAN_VERSION}, got {v}")

    # generation
    g = manifest.get("generation")
    if not isinstance(g, int) or g < 0:
        errors.append(f"generation: expected integer >= 0, got {g!r}")

    # parent-generation
    pg = manifest.get("parent-generation")
    if not isinstance(pg, int) or pg < 0:
        errors.append(f"parent-generation: expected integer >= 0, got {pg!r}")

    # spec-hash
    sh = manifest.get("spec-hash")
    if not isinstance(sh, str) or not _SPEC_HASH_RE.match(sh):
        errors.append(f"spec-hash: expected sha256:<64-hex>, got {sh!r}")

    # artifact-hash
    ah = manifest.get("artifact-hash")
    if not isinstance(ah, str) or not _SPEC_HASH_RE.match(ah):
        errors.append(f"artifact-hash: expected sha256:<64-hex>, got {ah!r}")

    # producer-model
    pm = manifest.get("producer-model")
    if not isinstance(pm, str) or not pm:
        errors.append(f"producer-model: expected non-empty string, got {pm!r}")

    # token-usage
    tu = manifest.get("token-usage")
    if not isinstance(tu, dict):
        errors.append(f"token-usage: expected object, got {type(tu).__name__}")
    else:
        for field in ("input", "output"):
            val = tu.get(field)
            if not isinstance(val, int) or val < 0:
                errors.append(f"token-usage.{field}: expected integer >= 0, got {val!r}")

    # files
    files = manifest.get("files")
    if not isinstance(files, list) or len(files) == 0:
        errors.append("files: expected non-empty array of strings")
    else:
        file_entries = [f for f in files if isinstance(f, str)]
        if len(file_entries) != len(files):
            errors.append("files: expected array of strings")
        elif "manifest.json" not in file_entries:
            errors.append("files: must include 'manifest.json'")
        else:
            normalized = {Path(f).as_posix().lstrip("./") for f in file_entries}
            if not any(p.endswith("CAMBRIAN-SPEC-005.md") for p in normalized):
                errors.append("files: must include spec/CAMBRIAN-SPEC-005.md")
            if "src/__init__.py" not in normalized:
                errors.append("files: must include src/__init__.py")

    # created-at
    ca = manifest.get("created-at")
    if not isinstance(ca, str) or not _ISO8601_RE.match(ca):
        errors.append(f"created-at: expected ISO-8601 datetime string, got {ca!r}")

    # entry
    entry = manifest.get("entry")
    if not isinstance(entry, dict):
        errors.append("entry: expected object")
    else:
        for field in ("build", "test", "start"):
            val = entry.get(field)
            if not isinstance(val, str) or not val:
                errors.append(f"entry.{field}: expected non-empty string, got {val!r}")
        # Reject script form when src/ is a package — causes sys.path failures at runtime
        # even when tests pass (pytest adds CWD to sys.path; direct script invocation does not).
        start_cmd = entry.get("start", "")
        if (
            isinstance(start_cmd, str)
            and _SCRIPT_FORM_RE.search(start_cmd)
            and (WORKSPACE / "src" / "__init__.py").exists()
        ):
            errors.append(
                "entry.start: MUST use module form (e.g. python -m src.prime), "
                "not script form (e.g. python src/prime.py), when src/__init__.py exists — "
                "see spec § entry.start"
            )
        health = entry.get("health")
        if not isinstance(health, str) or not _URL_RE.match(health):
            errors.append(f"entry.health: expected http(s):// URL, got {health!r}")

    # contracts (optional) — validate schema if present
    contracts = manifest.get("contracts")
    if contracts is not None:
        contract_errors = _validate_contracts(contracts)
        errors.extend(contract_errors)

    return errors


def _validate_contracts(contracts: Any) -> list[str]:
    """Validate contracts array schema. Returns error strings."""
    errors: list[str] = []
    if not isinstance(contracts, list):
        errors.append("contracts: expected array")
        return errors
    names_seen: set[str] = set()
    for i, c in enumerate(contracts):
        prefix = f"contracts[{i}]"
        if not isinstance(c, dict):
            errors.append(f"{prefix}: expected object")
            continue
        name = c.get("name")
        if not isinstance(name, str) or not name:
            errors.append(f"{prefix}.name: expected non-empty string")
        elif name in names_seen:
            errors.append(f"{prefix}.name: duplicate name {name!r}")
        else:
            names_seen.add(name)
        ctype = c.get("type")
        if ctype != "http":
            errors.append(f"{prefix}.type: expected 'http', got {ctype!r}")
        method = c.get("method")
        if method not in ("GET", "POST"):
            errors.append(f"{prefix}.method: expected 'GET' or 'POST', got {method!r}")
        path = c.get("path")
        if not isinstance(path, str) or not path.startswith("/"):
            errors.append(f"{prefix}.path: expected string starting with '/', got {path!r}")
        expect = c.get("expect")
        if not isinstance(expect, dict):
            errors.append(f"{prefix}.expect: expected object")
        elif "status" not in expect:
            errors.append(f"{prefix}.expect.status: required")
    return errors


# ---------------------------------------------------------------------------
# Stage 2: Build
# ---------------------------------------------------------------------------


def _kill_process_group(proc: subprocess.Popen[str]) -> None:
    """SIGTERM the process group, wait 5s, then SIGKILL any survivors.

    Because organism commands run with start_new_session=True, they form their
    own process group. Killing the group (instead of just proc.pid) ensures any
    daemonised children spawned by the organism are also reaped.
    """
    try:
        pgid = os.getpgid(proc.pid)
        with contextlib.suppress(ProcessLookupError, PermissionError):
            os.killpg(pgid, signal.SIGTERM)
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            with contextlib.suppress(ProcessLookupError, PermissionError):
                os.killpg(pgid, signal.SIGKILL)
    except ProcessLookupError:
        pass  # process already gone


def run_build(entry: dict[str, Any]) -> dict[str, Any]:
    cmd = entry.get("build", "")
    if not cmd:
        return {"passed": True, "duration_ms": 0}

    t0 = time.monotonic()
    try:
        result = subprocess.run(
            cmd,
            shell=True,
            cwd=WORKSPACE,
            capture_output=True,
            text=True,
            timeout=300,
            start_new_session=True,
        )
    except subprocess.TimeoutExpired as exc:
        duration_ms = int((time.monotonic() - t0) * 1000)
        raw_out = exc.stdout or b""
        raw_err = exc.stderr or b""
        stdout = raw_out.decode(errors="replace") if isinstance(raw_out, bytes) else raw_out
        stderr = raw_err.decode(errors="replace") if isinstance(raw_err, bytes) else raw_err
        return {
            "passed": False,
            "duration_ms": duration_ms,
            "exit_code": None,
            "stdout_tail": _tail_lines(stdout, 100),
            "stderr_tail": _tail_lines(stderr, 100),
            "timed_out": True,
        }
    duration_ms = int((time.monotonic() - t0) * 1000)

    if result.returncode != 0:
        return {
            "passed": False,
            "duration_ms": duration_ms,
            "exit_code": result.returncode,
            "stdout_tail": _tail_lines(result.stdout, 100),
            "stderr_tail": _tail_lines(result.stderr, 100),
        }
    return {"passed": True, "duration_ms": duration_ms}


# ---------------------------------------------------------------------------
# Pre-test: Syntax check + structlog lint
# ---------------------------------------------------------------------------


def run_syntax_check() -> dict[str, Any]:
    """Compile every .py file in WORKSPACE with ast.parse().

    Catches Python 3.14 SyntaxErrors (unescaped newlines in strings, etc.) before
    pytest runs, producing a clear file:line diagnostic instead of a confusing
    pytest collection error.
    """
    import ast

    t0 = time.monotonic()
    errors: list[str] = []
    for path in sorted(WORKSPACE.rglob("*.py")):
        try:
            src = path.read_text(encoding="utf-8", errors="replace")
            ast.parse(src, filename=str(path))
        except SyntaxError as e:
            rel = path.relative_to(WORKSPACE)
            errors.append(f"{rel}:{e.lineno}: {e.msg}")
        except OSError:
            pass

    duration_ms = int((time.monotonic() - t0) * 1000)
    if errors:
        extra = f" (and {len(errors) - 5} more)" if len(errors) > 5 else ""
        return {
            "passed": False,
            "duration_ms": duration_ms,
            "error": "; ".join(errors[:5]) + extra,
        }
    return {"passed": True, "duration_ms": duration_ms}


def run_structlog_lint() -> dict[str, Any]:
    """AST-walk all .py files under /workspace/src/ for structlog anti-patterns.

    Detects:
    1. log.*(positional_string, event=...) — passes event twice → TypeError at runtime.
    2. log.*(format_string_with_percent) — structlog does not interpolate; %d stays literal.

    Returns a diagnostic with file:line and the correct form so the LLM can fix it.
    """
    import ast
    import re

    _LOG_METHODS = {"debug", "info", "warning", "error", "critical", "exception"}

    t0 = time.monotonic()
    violations: list[str] = []

    src_dir = WORKSPACE / "src"
    if not src_dir.exists():
        return {"passed": True, "duration_ms": 0}

    for path in sorted(src_dir.rglob("*.py")):
        try:
            src = path.read_text(encoding="utf-8", errors="replace")
            tree = ast.parse(src, filename=str(path))
        except SyntaxError, OSError:
            continue

        rel = path.relative_to(WORKSPACE)
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            # Match log.info(...), log.error(...), etc.
            if not (isinstance(func, ast.Attribute) and func.attr in _LOG_METHODS):
                continue
            kwarg_names = {kw.arg for kw in node.keywords if isinstance(kw, ast.keyword)}
            pos_args = node.args

            # Anti-pattern 1: first positional arg is a string AND event= is also a kwarg
            if (
                pos_args
                and isinstance(pos_args[0], ast.Constant)
                and isinstance(pos_args[0].value, str)
                and "event" in kwarg_names
            ):
                violations.append(
                    f"{rel}:{node.lineno}: structlog anti-pattern: "
                    f"log.{func.attr}(\"...\", event=...) passes 'event' twice → TypeError. "
                    f'Use log.{func.attr}("event_name", other_key=value) instead.'
                )

            # Anti-pattern 2: first positional arg is a string containing % formatting
            _PRINTF_RE = re.compile(r"%[-+0 #]*\d*\.?\d*[diouxXeEfFgGcrsab]")
            if (
                pos_args
                and isinstance(pos_args[0], ast.Constant)
                and isinstance(pos_args[0].value, str)
                and _PRINTF_RE.search(pos_args[0].value)
            ):
                violations.append(
                    f"{rel}:{node.lineno}: structlog anti-pattern: "
                    f'log.{func.attr}("{pos_args[0].value[:30]}...") uses printf formatting '
                    f"which structlog does not interpolate. Use keyword args instead."
                )

    duration_ms = int((time.monotonic() - t0) * 1000)
    if violations:
        summary = "; ".join(violations[:3])
        if len(violations) > 3:
            summary += f" (and {len(violations) - 3} more)"
        return {"passed": False, "duration_ms": duration_ms, "error": summary}
    return {"passed": True, "duration_ms": duration_ms}


# ---------------------------------------------------------------------------
# Stage 3: Test
# ---------------------------------------------------------------------------


def run_tests(entry: dict[str, Any]) -> dict[str, Any]:
    cmd = entry.get("test", "")
    if not cmd:
        return {"passed": True, "duration_ms": 0, "tests_passed": 0, "tests_run": 0}

    t0 = time.monotonic()
    try:
        result = subprocess.run(
            cmd,
            shell=True,
            cwd=WORKSPACE,
            capture_output=True,
            text=True,
            timeout=120,
            start_new_session=True,
        )
    except subprocess.TimeoutExpired as exc:
        duration_ms = int((time.monotonic() - t0) * 1000)
        raw_out = exc.stdout or b""
        raw_err = exc.stderr or b""
        stdout = raw_out.decode(errors="replace") if isinstance(raw_out, bytes) else raw_out
        stderr = raw_err.decode(errors="replace") if isinstance(raw_err, bytes) else raw_err
        return {
            "passed": False,
            "duration_ms": duration_ms,
            "exit_code": None,
            "tests_passed": -1,
            "tests_run": -1,
            "stdout_tail": _tail_lines(stdout, 100),
            "stderr_tail": _tail_lines(stderr, 100),
            "timed_out": True,
        }
    duration_ms = int((time.monotonic() - t0) * 1000)

    combined = result.stdout + result.stderr
    tests_passed, tests_run = _parse_pytest_counts(combined)
    failures = _parse_pytest_failures(combined)

    if result.returncode != 0:
        return {
            "passed": False,
            "duration_ms": duration_ms,
            "exit_code": result.returncode,
            "tests_passed": tests_passed,
            "tests_run": tests_run,
            "failures": failures,
            "stdout_tail": _tail_lines(result.stdout, 100),
            "stderr_tail": _tail_lines(result.stderr, 100),
        }
    return {
        "passed": True,
        "duration_ms": duration_ms,
        "tests_passed": tests_passed,
        "tests_run": tests_run,
    }


def _parse_pytest_counts(output: str) -> tuple[int, int]:
    """Extract (passed, total) from pytest output.
    Returns (-1, -1) if pattern is not found (non-pytest or crash)."""
    passed = 0
    failed = 0
    found = False
    for match in re.finditer(r"(\d+) (passed|failed|error)", output):
        count, kind = int(match.group(1)), match.group(2)
        if kind == "passed":
            passed = count
            found = True
        elif kind in ("failed", "error"):
            failed += count
            found = True
    if not found:
        return -1, -1
    return passed, passed + failed


def _parse_pytest_failures(output: str) -> list[dict[str, Any]]:
    """Parse FAILED <file>::<test> - <error> lines from pytest output."""
    failures: list[dict[str, Any]] = []
    # Pattern: FAILED tests/test_foo.py::test_bar - SomeError: message
    pattern = re.compile(r"^FAILED ([^:]+(?:::[^:]+)*) - (.+)$", re.MULTILINE)
    for match in pattern.finditer(output):
        full_name = match.group(1).strip()
        error = match.group(2).strip()[:500]
        # Split file::test_name
        parts = full_name.split("::")
        file_path = parts[0] if parts else ""
        failure: dict[str, Any] = {
            "test": full_name,
            "error": error,
        }
        if file_path:
            failure["file"] = file_path
        # Try to extract line number from traceback
        line_match = re.search(
            rf"{re.escape(file_path)}:(\d+):" if file_path else r"",
            output,
        )
        if line_match:
            failure["line"] = int(line_match.group(1))
        failures.append(failure)
    return failures


# ---------------------------------------------------------------------------
# Stage 4: Start (with TCP readiness polling)
# ---------------------------------------------------------------------------


def run_import_check(entry: dict[str, Any]) -> dict[str, Any]:
    """Verify the start module is importable before attempting to start the process.

    Extracts the module name from 'python -m <module>' in entry.start and runs
    it through the interpreter's import machinery. Catches ModuleNotFoundError
    and sys.path issues before the start stage, where they produce opaque crashes.
    Returns immediately (passed=True) if the start command does not use -m.
    """
    import shlex
    import sys

    cmd = entry.get("start", "")
    try:
        parts = shlex.split(cmd)
    except ValueError:
        return {"passed": True, "duration_ms": 0}

    module: str | None = None
    for i, part in enumerate(parts):
        if part == "-m" and i + 1 < len(parts):
            module = parts[i + 1]
            break

    if module is None:
        return {"passed": True, "duration_ms": 0}

    t0 = time.monotonic()
    try:
        result = subprocess.run(
            [sys.executable, "-c", f"import {module}"],
            cwd=WORKSPACE,
            capture_output=True,
            text=True,
            timeout=30,
            start_new_session=True,
        )
    except subprocess.TimeoutExpired:
        duration_ms = int((time.monotonic() - t0) * 1000)
        return {
            "passed": False,
            "duration_ms": duration_ms,
            "error": f"import {module!r} timed out after 30s",
        }

    duration_ms = int((time.monotonic() - t0) * 1000)
    if result.returncode != 0:
        return {
            "passed": False,
            "duration_ms": duration_ms,
            "exit_code": result.returncode,
            "stdout_tail": _tail_lines(result.stdout, 20),
            "stderr_tail": _tail_lines(result.stderr, 20),
        }
    return {"passed": True, "duration_ms": duration_ms}


def _parse_port_from_url(url: str) -> tuple[str, int]:
    """Extract (host, port) from a URL. Returns ('localhost', 8401) on failure."""
    try:
        parsed = urlparse(url)
        host = parsed.hostname or "localhost"
        port = parsed.port
        if port is None:
            port = 443 if parsed.scheme == "https" else 80
        return host, port
    except Exception:
        return "localhost", 8401


def start_process(entry: dict[str, Any]) -> tuple[subprocess.Popen[str], dict[str, Any]]:
    """Start the artifact server process."""
    cmd = entry.get("start", "")
    t0 = time.monotonic()
    proc = subprocess.Popen(
        cmd,
        shell=True,
        cwd=WORKSPACE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        start_new_session=True,
    )
    duration_ms = int((time.monotonic() - t0) * 1000)
    return proc, {"passed": True, "duration_ms": duration_ms}


def wait_for_tcp(host: str, port: int, proc: subprocess.Popen[str]) -> dict[str, Any]:
    """Poll TCP port until it accepts connections or times out.

    Fails immediately if the process exits before the port opens.
    """
    t0 = time.monotonic()
    deadline = t0 + TCP_READINESS_TIMEOUT
    last_error = ""

    while time.monotonic() < deadline:
        # Check if process has already died
        if proc.poll() is not None:
            stdout = proc.stdout.read() if proc.stdout else ""
            stderr = proc.stderr.read() if proc.stderr else ""
            duration_ms = int((time.monotonic() - t0) * 1000)
            return {
                "passed": False,
                "duration_ms": duration_ms,
                "exit_code": proc.returncode,
                "stdout_tail": _tail_lines(stdout, 100),
                "stderr_tail": _tail_lines(stderr, 100),
            }
        # Try to connect
        try:
            with socket.create_connection((host, port), timeout=1):
                pass
            duration_ms = int((time.monotonic() - t0) * 1000)
            return {"passed": True, "duration_ms": duration_ms}
        except OSError as e:
            last_error = str(e)
        time.sleep(TCP_POLL_INTERVAL)

    duration_ms = int((time.monotonic() - t0) * 1000)
    return {
        "passed": False,
        "duration_ms": duration_ms,
        "exit_code": None,
        "stdout_tail": "",
        "stderr_tail": "",
        "error": f"Port {port} not open after {TCP_READINESS_TIMEOUT}s. Last error: {last_error}",
    }


# ---------------------------------------------------------------------------
# Stage 5: Health check (spec vectors + contracts or fallback)
# ---------------------------------------------------------------------------


def _find_spec_file(manifest: dict[str, Any]) -> Path | None:
    """Find the spec file in WORKSPACE. Returns None if not found."""
    workspace_root = WORKSPACE.resolve()
    for f in manifest.get("files", []):
        if str(f).endswith("CAMBRIAN-SPEC-005.md"):
            candidate = (WORKSPACE / f).resolve()
            try:
                candidate.relative_to(workspace_root)
            except ValueError:
                continue
            if candidate.exists():
                return candidate
    matches = list(WORKSPACE.glob("**/CAMBRIAN-SPEC-005.md"))
    return matches[0] if matches else None


def _extract_spec_vectors(spec_text: str) -> list[dict[str, Any]]:
    """Extract spec vectors from the FROZEN acceptance-vectors block."""
    import yaml

    begin_marker = "<!-- BEGIN FROZEN: acceptance-vectors -->"
    end_marker = "<!-- END FROZEN: acceptance-vectors -->"
    begin_idx = spec_text.find(begin_marker)
    end_idx = spec_text.find(end_marker)
    if begin_idx == -1 or end_idx == -1 or begin_idx >= end_idx:
        return []

    region = spec_text[begin_idx + len(begin_marker) : end_idx]
    vectors: list[dict[str, Any]] = []
    for m in re.finditer(r"```spec-vector\n(.*?)\n```", region, re.DOTALL):
        doc = yaml.safe_load(m.group(1))
        if isinstance(doc, dict):
            vectors.append(doc)
    return vectors


def run_health_check(
    health_url: str,
    contracts: list[dict[str, Any]] | None,
    generation: int,
    manifest: dict[str, Any] | None = None,
    spec_vectors: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Run health check — spec vectors first, then manifest contracts, then fallback.

    spec_vectors: pre-read at manifest stage (before organism ran). When provided,
    the spec file is NOT re-read, so the organism cannot tamper with it to defeat
    spec-vector evaluation. When None (e.g. unit tests calling directly), falls back
    to reading from the spec file via manifest.
    """
    t0 = time.monotonic()

    # --- Resolve spec vectors (Layer 1) ---
    # Use pre-read vectors if provided; otherwise fall back to reading from disk.
    if spec_vectors is None and manifest is not None:
        spec_file = _find_spec_file(manifest)
        if spec_file is not None:
            try:
                spec_vectors = _extract_spec_vectors(spec_file.read_text(encoding="utf-8"))
            except Exception as e:
                log.warning("spec_vectors_read_failed", error=str(e))
    if spec_vectors is None:
        spec_vectors = []

    # No spec vectors and no contracts → fallback
    if not spec_vectors and not contracts:
        return _run_fallback_health(health_url, t0)

    parsed = urlparse(health_url)
    base = f"{parsed.scheme}://{parsed.netloc}"
    all_passed = True
    result: dict[str, Any] = {}

    # Evaluate spec vectors first (per spec: vectors are the floor)
    if spec_vectors:
        sv_results: dict[str, Any] = {}
        for vector in spec_vectors:
            name = vector["name"]
            sv_result = _eval_contract(base, vector, generation)
            sv_results[name] = sv_result
            if not sv_result["passed"]:
                all_passed = False
        result["spec-vectors"] = sv_results

    # Evaluate manifest contracts (the ceiling)
    if contracts:
        contract_results: dict[str, Any] = {}
        for contract in contracts:
            name = contract["name"]
            cr = _eval_contract(base, contract, generation)
            contract_results[name] = cr
            if not cr["passed"]:
                all_passed = False
        result["contracts"] = contract_results

    result["passed"] = all_passed
    result["duration_ms"] = int((time.monotonic() - t0) * 1000)
    return result


def _run_contracts(
    health_url: str,
    contracts: list[dict[str, Any]],
    generation: int,
    t0: float,
) -> dict[str, Any]:
    """Evaluate all contracts. Does NOT short-circuit on first failure."""
    parsed = urlparse(health_url)
    base = f"{parsed.scheme}://{parsed.netloc}"

    contract_results: dict[str, Any] = {}
    all_passed = True

    for contract in contracts:
        name = contract["name"]
        result = _eval_contract(base, contract, generation)
        contract_results[name] = result
        if not result["passed"]:
            all_passed = False

    duration_ms = int((time.monotonic() - t0) * 1000)
    return {
        "passed": all_passed,
        "duration_ms": duration_ms,
        "contracts": contract_results,
    }


def _eval_contract(base_url: str, contract: dict[str, Any], generation: int) -> dict[str, Any]:
    """Execute a single HTTP contract. Returns {passed, duration_ms, error?}."""
    import urllib.error
    import urllib.request

    t0 = time.monotonic()
    method = contract["method"]
    path = contract["path"]
    url = base_url + path
    expect = contract["expect"]

    try:
        req = urllib.request.Request(url, method=method)
        with urllib.request.urlopen(req, timeout=HEALTH_TIMEOUT) as resp:
            status = resp.status
            body_bytes = resp.read()
    except urllib.error.HTTPError as e:
        status = e.code
        body_bytes = e.read()
    except Exception as e:
        duration_ms = int((time.monotonic() - t0) * 1000)
        return {"passed": False, "duration_ms": duration_ms, "error": str(e)}

    duration_ms = int((time.monotonic() - t0) * 1000)

    # Check status
    expected_status = expect.get("status")
    if expected_status is not None and status != expected_status:
        return {
            "passed": False,
            "duration_ms": duration_ms,
            "error": f"status {status} != expected {expected_status}",
        }

    # Parse body for body-level checks
    body_checks = ("body" in expect) or ("body_contains" in expect) or ("body_has_keys" in expect)
    body: Any = None
    if body_checks:
        try:
            body = json.loads(body_bytes)
        except json.JSONDecodeError as e:
            return {
                "passed": False,
                "duration_ms": duration_ms,
                "error": f"response body is not valid JSON: {e}",
            }
    # body — exact match
    if "body" in expect:
        expected_body = _substitute_generation(expect["body"], generation)
        if body != expected_body:
            return {
                "passed": False,
                "duration_ms": duration_ms,
                "error": f"body mismatch: expected {expected_body!r}, got {body!r}",
            }

    # body_contains — partial match
    if "body_contains" in expect:
        required = _substitute_generation(expect["body_contains"], generation)
        if not isinstance(body, dict) or not isinstance(required, dict):
            return {
                "passed": False,
                "duration_ms": duration_ms,
                "error": "body_contains requires a JSON object response",
            }
        for k, v in required.items():
            if k not in body:
                return {
                    "passed": False,
                    "duration_ms": duration_ms,
                    "error": f"missing key in body: {k!r}",
                }
            if body[k] != v:
                return {
                    "passed": False,
                    "duration_ms": duration_ms,
                    "error": f"body[{k!r}] = {body[k]!r}, expected {v!r}",
                }

    # body_has_keys — key presence
    if "body_has_keys" in expect:
        required_keys: list[str] = expect["body_has_keys"]
        if not isinstance(body, dict):
            return {
                "passed": False,
                "duration_ms": duration_ms,
                "error": "body_has_keys requires a JSON object response",
            }
        for k in required_keys:
            if k not in body:
                return {
                    "passed": False,
                    "duration_ms": duration_ms,
                    "error": f"missing key: {k!r}",
                }

    return {"passed": True, "duration_ms": duration_ms}


def _substitute_generation(value: Any, generation: int) -> Any:
    """Recursively replace '$GENERATION' with the integer generation number."""
    if isinstance(value, str):
        if value == "$GENERATION":
            return generation
        return value.replace("$GENERATION", str(generation))
    if isinstance(value, dict):
        return {k: _substitute_generation(v, generation) for k, v in value.items()}
    if isinstance(value, list):
        return [_substitute_generation(item, generation) for item in value]
    return value


def _run_fallback_health(health_url: str, t0: float) -> dict[str, Any]:
    """Hard-coded health check: GET /health (200) + GET /stats (200, JSON with generation)."""
    import urllib.error
    import urllib.request

    errors: list[str] = []

    # Check 1: GET health_url → 200
    try:
        with urllib.request.urlopen(health_url, timeout=HEALTH_TIMEOUT) as resp:
            if resp.status != 200:
                errors.append(f"GET {health_url} returned {resp.status} (expected 200)")
    except Exception as e:
        errors.append(f"GET {health_url} failed: {e}")

    # Check 2: GET /stats on same host:port → 200 JSON with generation
    parsed = urlparse(health_url)
    stats_url = f"{parsed.scheme}://{parsed.netloc}/stats"
    try:
        with urllib.request.urlopen(stats_url, timeout=HEALTH_TIMEOUT) as resp:
            if resp.status != 200:
                errors.append(f"GET {stats_url} returned {resp.status} (expected 200)")
            else:
                try:
                    data = json.loads(resp.read())
                    if "generation" not in data:
                        errors.append(f"GET {stats_url} response missing 'generation' field")
                except json.JSONDecodeError:
                    errors.append(f"GET {stats_url} response is not valid JSON")
    except Exception as e:
        errors.append(f"GET {stats_url} failed: {e}")

    duration_ms = int((time.monotonic() - t0) * 1000)
    if errors:
        return {
            "passed": False,
            "duration_ms": duration_ms,
            "error": "; ".join(errors),
        }
    return {"passed": True, "duration_ms": duration_ms}


# ---------------------------------------------------------------------------
# M3: Dual-run baseline check
# ---------------------------------------------------------------------------


def run_baseline_check(health_url: str, generation: int) -> dict[str, Any] | None:
    """Evaluate baseline contracts from the last promoted generation against the running server.

    Loaded from CAMBRIAN_BASELINE_PATH (battery.json mounted by the Supervisor).
    Results are INFORMATIONAL — they do not affect viability, only fitness.
    Returns None if no baseline is present or the battery has no contracts.
    """
    if not BASELINE_PATH.exists():
        return None

    try:
        battery = json.loads(BASELINE_PATH.read_text())
    except Exception as e:
        log.warning("baseline_battery_load_failed", path=str(BASELINE_PATH), error=str(e))
        return None

    baseline_contracts: list[dict[str, Any]] = battery.get("contracts", [])
    baseline_generation = battery.get("generation")

    if not baseline_contracts:
        # No contracts to check — return None so fitness skips baseline_contract_pass_rate
        # rather than reporting a misleading perfect score.
        return None

    t0 = time.monotonic()
    parsed = urlparse(health_url)
    base = f"{parsed.scheme}://{parsed.netloc}"

    contract_results: dict[str, Any] = {}
    all_passed = True
    for contract in baseline_contracts:
        name = contract.get("name")
        if not name or not contract.get("path") or not contract.get("expect"):
            log.warning(
                "baseline_contract_malformed",
                generation=generation,
                contract=contract,
            )
            continue
        result = _eval_contract(base, contract, generation)
        contract_results[name] = result
        if not result["passed"]:
            all_passed = False

    if not contract_results:
        # If every contract was malformed, emit no baseline signal.
        return None

    return {
        "baseline-generation": baseline_generation,
        "passed": all_passed,
        "duration_ms": int((time.monotonic() - t0) * 1000),
        "contracts": contract_results,
    }


# ---------------------------------------------------------------------------
# Fitness vector
# ---------------------------------------------------------------------------


def compute_fitness(
    checks: dict[str, Any],
    manifest: dict[str, Any],
    stages_completed: list[str],
) -> dict[str, Any]:
    """Compute the fitness vector.

    Dimensions:
      Speed (5): build/test/start/health/total duration_ms
      Correctness (2): test_count, test_pass_rate  [self-referential, weight 0.5]
      Economy (4): token_input, token_output, source_lines, dependency_count
      Robustness (4): source_files, test_files, test_lines, assertion_density
      Quality (1): trivial_assert_rate  [lower is better]
      Conditional: contract_pass_rate, spec_vector_pass_rate
      Meta: stages_completed, fitness_weights
    """
    fitness: dict[str, Any] = {}

    # Duration metrics (from checks)
    for stage, key in (
        ("build", "build_duration_ms"),
        ("test", "test_duration_ms"),
        ("start", "start_duration_ms"),
        ("health", "health_duration_ms"),
    ):
        if stage in stages_completed:
            fitness[key] = checks.get(stage, {}).get("duration_ms", 0)

    fitness["total_duration_ms"] = sum(
        checks.get(s, {}).get("duration_ms", 0) for s in stages_completed
    )

    # Test metrics
    if "test" in stages_completed:
        test_result = checks.get("test", {})
        tests_passed = test_result.get("tests_passed", -1)
        tests_run = test_result.get("tests_run", -1)
        fitness["test_count"] = tests_run
        fitness["test_pass_rate"] = round(tests_passed / tests_run, 4) if tests_run > 0 else 0.0

    # File metrics from manifest
    files: list[str] = manifest.get("files", [])
    source_files: list[str] = []
    test_files: list[str] = []
    for f in files:
        if f == "manifest.json":
            continue
        basename = Path(f).name
        if basename.startswith("test") or "_test" in basename or basename.endswith("_test.py"):
            test_files.append(f)
        else:
            source_files.append(f)

    fitness["source_files"] = len(source_files)
    fitness["test_files"] = len(test_files)

    # Line counts
    fitness["source_lines"] = _count_lines(source_files)
    fitness["test_lines"] = _count_lines(test_files)

    # Dependency count from requirements.txt
    req_path = WORKSPACE / "requirements.txt"
    if req_path.exists():
        lines = req_path.read_text(errors="replace").splitlines()
        fitness["dependency_count"] = sum(
            1 for line in lines if line.strip() and not line.strip().startswith("#")
        )
    else:
        fitness["dependency_count"] = 0

    # Token usage
    token_usage = manifest.get("token-usage", {})
    fitness["token_input"] = token_usage.get("input", 0)
    fitness["token_output"] = token_usage.get("output", 0)

    # Test quality metrics (self-referential mitigation)
    assertion_density, trivial_assert_rate = _analyze_test_quality(test_files)
    fitness["assertion_density"] = assertion_density
    fitness["trivial_assert_rate"] = trivial_assert_rate

    # Contract pass rate (absent if no contracts)
    health_result = checks.get("health", {})
    contracts_data = health_result.get("contracts")
    if contracts_data is not None and isinstance(contracts_data, dict):
        total = len(contracts_data)
        passed = sum(1 for v in contracts_data.values() if v.get("passed"))
        fitness["contract_pass_rate"] = round(passed / total, 4) if total > 0 else 1.0

    # Spec vector pass rate (absent if no spec vectors evaluated)
    sv_data = health_result.get("spec-vectors")
    if sv_data is not None and isinstance(sv_data, dict):
        total = len(sv_data)
        passed = sum(1 for v in sv_data.values() if v.get("passed"))
        fitness["spec_vector_pass_rate"] = round(passed / total, 4) if total > 0 else 1.0

    # Baseline contract pass rate — regression score vs last promoted generation (M3).
    # 1.0 = full backward compatibility; <1.0 = some baseline contracts broken.
    baseline_data = health_result.get("baseline-contracts")
    if baseline_data is not None and isinstance(baseline_data, dict):
        baseline_contracts = baseline_data.get("contracts", {})
        total = len(baseline_contracts)
        passed = sum(1 for v in baseline_contracts.values() if v.get("passed"))
        fitness["baseline_contract_pass_rate"] = round(passed / total, 4) if total > 0 else 1.0

    # Discount weights for selection policies.
    # Self-referential dimensions (organism controls its own tests) get weight 0.5.
    # External dimensions (Test Rig measures them independently) get weight 1.0.
    # trivial_assert_rate is inverted (lower is better) — consumers must account for this.
    fitness["fitness_weights"] = {
        "test_count": 0.5,
        "test_pass_rate": 0.5,
        "trivial_assert_rate": 0.5,
    }

    # Stages completed
    fitness["stages_completed"] = stages_completed

    return fitness


def _count_lines(file_paths: list[str]) -> int:
    """Count total newlines across files. Skips binary files."""
    total = 0
    for rel_path in file_paths:
        path = WORKSPACE / rel_path
        if not path.exists():
            continue
        try:
            raw = path.read_bytes()
            if b"\x00" in raw[:8192]:  # binary file detection
                continue
            total += raw.decode(errors="replace").count("\n")
        except OSError:
            continue
    return total


# Matches `assert True`, `assert False`, `assert None` (trivial asserts that always pass/fail).
_TRIVIAL_ASSERT_RE = re.compile(r"^\s*assert\s+(True|False|None)\s*(?:#.*)?$", re.MULTILINE)
# Matches any assert statement (including compound forms).
_ASSERT_RE = re.compile(r"^\s*assert\b", re.MULTILINE)
# Matches test function definitions.
_TEST_FUNC_RE = re.compile(r"^\s*(?:async\s+)?def\s+test_", re.MULTILINE)


def _analyze_test_quality(test_file_paths: list[str]) -> tuple[float, float]:
    """Analyse test files and return (assertion_density, trivial_assert_rate).

    assertion_density: mean assertions per test function (0.0 if no test functions).
    trivial_assert_rate: fraction of assertions that are `assert True/False/None`
        (0.0 if no assertions found).
    """
    total_asserts = 0
    trivial_asserts = 0
    total_funcs = 0

    for rel_path in test_file_paths:
        path = WORKSPACE / rel_path
        if not path.exists():
            continue
        try:
            raw = path.read_bytes()
            if b"\x00" in raw[:8192]:
                continue
            text = raw.decode(errors="replace")
        except OSError:
            continue

        total_asserts += len(_ASSERT_RE.findall(text))
        trivial_asserts += len(_TRIVIAL_ASSERT_RE.findall(text))
        total_funcs += len(_TEST_FUNC_RE.findall(text))

    assertion_density = round(total_asserts / total_funcs, 4) if total_funcs > 0 else 0.0
    trivial_assert_rate = round(trivial_asserts / total_asserts, 4) if total_asserts > 0 else 0.0
    return assertion_density, trivial_assert_rate


# ---------------------------------------------------------------------------
# Utility
# ---------------------------------------------------------------------------


def _tail_lines(text: str, n: int) -> str:
    """Return the last n lines of text."""
    lines = text.splitlines(keepends=True)
    return "".join(lines[-n:])


def _skipped_check(stage: str) -> dict[str, Any]:
    """Standard entry for a stage that was not attempted."""
    check: dict[str, Any] = {"passed": False, "duration_ms": 0}
    if stage == "test":
        check["tests_run"] = 0
        check["tests_passed"] = 0
    return check


# ---------------------------------------------------------------------------
# Main pipeline
# ---------------------------------------------------------------------------


def run_pipeline() -> None:
    manifest_path = WORKSPACE / "manifest.json"
    stages_completed: list[str] = []

    # Build the full checks dict with skipped entries upfront
    checks: dict[str, Any] = {s: _skipped_check(s) for s in ALL_STAGES}

    # -----------------------------------------------------------------------
    # Stage 1: Manifest
    # -----------------------------------------------------------------------
    log.info("stage_start", stage="manifest")

    if not manifest_path.exists():
        _write_report(
            generation=0,
            failure_stage="manifest",
            checks=checks,
            fitness=compute_fitness(checks, {}, stages_completed),
            diagnostics={
                "stage": "manifest",
                "summary": "manifest.json not found",
                "exit_code": None,
                "failures": [],
                "stdout_tail": "",
                "stderr_tail": "",
            },
        )
        sys.exit(1)

    try:
        with manifest_path.open() as f:
            manifest: dict[str, Any] = json.load(f)
    except json.JSONDecodeError as e:
        checks["manifest"] = {"passed": False, "duration_ms": 0}
        _write_report(
            generation=0,
            failure_stage="manifest",
            checks=checks,
            fitness=compute_fitness(checks, {}, stages_completed),
            diagnostics={
                "stage": "manifest",
                "summary": f"manifest.json is not valid JSON: {e}",
                "exit_code": None,
                "failures": [],
                "stdout_tail": "",
                "stderr_tail": "",
            },
        )
        sys.exit(1)

    validation_errors = _validate_manifest(manifest)
    if validation_errors:
        checks["manifest"] = {"passed": False, "duration_ms": 0}
        summary = "manifest validation failed: " + "; ".join(validation_errors[:3])
        if len(validation_errors) > 3:
            summary += f" (and {len(validation_errors) - 3} more)"
        _write_report(
            generation=manifest.get("generation", 0),
            failure_stage="manifest",
            checks=checks,
            fitness=compute_fitness(checks, manifest, stages_completed),
            diagnostics={
                "stage": "manifest",
                "summary": summary,
                "exit_code": None,
                "failures": [],
                "stdout_tail": "",
                "stderr_tail": "",
            },
        )
        sys.exit(1)

    generation: int = manifest["generation"]
    entry: dict[str, Any] = manifest["entry"]
    contracts: list[dict[str, Any]] | None = manifest.get("contracts")
    checks["manifest"] = {"passed": True, "duration_ms": 0}
    stages_completed.append("manifest")

    # Pre-read spec vectors NOW (before any organism code runs) so the organism
    # cannot tamper with the spec file to defeat spec-vector evaluation.
    preread_spec_vectors: list[dict[str, Any]] | None = None
    spec_file = _find_spec_file(manifest)
    if spec_file is not None:
        try:
            preread_spec_vectors = _extract_spec_vectors(spec_file.read_text(encoding="utf-8"))
        except Exception as e:
            log.warning("spec_vectors_preread_failed", error=str(e))

    # -----------------------------------------------------------------------
    # Stage 2: Build
    # -----------------------------------------------------------------------
    log.info("stage_start", stage="build")
    build_result = run_build(entry)
    _skip = {"exit_code", "stdout_tail", "stderr_tail", "timed_out"}
    checks["build"] = {k: v for k, v in build_result.items() if k not in _skip}
    stages_completed.append("build")

    if not build_result["passed"]:
        timed_out = build_result.get("timed_out", False)
        if timed_out:
            summary = "build command timed out after 300s"
        else:
            summary = f"build command failed with exit code {build_result.get('exit_code')}"
        _write_report(
            generation=generation,
            failure_stage="build",
            checks=checks,
            fitness=compute_fitness(checks, manifest, stages_completed),
            diagnostics={
                "stage": "build",
                "summary": summary,
                "exit_code": build_result.get("exit_code"),
                "failures": [],
                "stdout_tail": build_result.get("stdout_tail", ""),
                "stderr_tail": build_result.get("stderr_tail", ""),
            },
        )
        sys.exit(1)

    # -----------------------------------------------------------------------
    # Pre-test: Syntax check (catches Python 3.14 SyntaxErrors before pytest)
    # -----------------------------------------------------------------------
    log.info("pre_test_check", check="syntax")
    syntax_result = run_syntax_check()
    if not syntax_result["passed"]:
        _write_report(
            generation=generation,
            failure_stage="build",
            checks=checks,
            fitness=compute_fitness(checks, manifest, stages_completed),
            diagnostics={
                "stage": "build",
                "summary": f"syntax error in generated code: {syntax_result.get('error', '')}",
                "exit_code": None,
                "failures": [],
                "stdout_tail": "",
                "stderr_tail": syntax_result.get("error", ""),
            },
        )
        sys.exit(1)

    # -----------------------------------------------------------------------
    # Pre-test: structlog lint (catches TypeError-inducing anti-patterns before runtime)
    # -----------------------------------------------------------------------
    log.info("pre_test_check", check="structlog_lint")
    lint_result = run_structlog_lint()
    if not lint_result["passed"]:
        _write_report(
            generation=generation,
            failure_stage="build",
            checks=checks,
            fitness=compute_fitness(checks, manifest, stages_completed),
            diagnostics={
                "stage": "build",
                "summary": f"structlog anti-pattern detected: {lint_result.get('error', '')}",
                "exit_code": None,
                "failures": [],
                "stdout_tail": "",
                "stderr_tail": lint_result.get("error", ""),
            },
        )
        sys.exit(1)

    # -----------------------------------------------------------------------
    # Stage 3: Test
    # -----------------------------------------------------------------------
    log.info("stage_start", stage="test")
    test_result = run_tests(entry)
    # Store public fields in checks (no internal keys)
    checks["test"] = {
        k: v
        for k, v in test_result.items()
        if k not in ("exit_code", "stdout_tail", "stderr_tail", "failures", "timed_out")
    }
    stages_completed.append("test")

    if not test_result["passed"]:
        tests_run = test_result.get("tests_run", -1)
        tests_passed = test_result.get("tests_passed", -1)
        timed_out = test_result.get("timed_out", False)
        if timed_out:
            summary = "test command timed out after 120s"
        elif tests_run < 0:
            summary = "test command failed (non-pytest output)"
        else:
            summary = f"{tests_run - tests_passed} of {tests_run} tests failed"
        _write_report(
            generation=generation,
            failure_stage="test",
            checks=checks,
            fitness=compute_fitness(checks, manifest, stages_completed),
            diagnostics={
                "stage": "test",
                "summary": summary,
                "exit_code": test_result.get("exit_code"),
                "failures": test_result.get("failures", []),
                "stdout_tail": test_result.get("stdout_tail", ""),
                "stderr_tail": test_result.get("stderr_tail", ""),
            },
        )
        sys.exit(1)

    # -----------------------------------------------------------------------
    # Stage 4: Start
    # -----------------------------------------------------------------------
    log.info("stage_start", stage="start")

    # Pre-start import check: verify the module is importable before attempting
    # to launch the process. Catches sys.path/ModuleNotFoundError issues that
    # produce opaque start failures (process exits with code 1, stderr buried).
    import_result = run_import_check(entry)
    if not import_result["passed"]:
        checks["start"] = {"passed": False, "duration_ms": import_result["duration_ms"]}
        stages_completed.append("start")
        error_detail = import_result.get("error") or import_result.get("stderr_tail", "")
        _write_report(
            generation=generation,
            failure_stage="start",
            checks=checks,
            fitness=compute_fitness(checks, manifest, stages_completed),
            diagnostics={
                "stage": "start",
                "summary": f"import check failed: {error_detail}",
                "exit_code": import_result.get("exit_code"),
                "failures": [],
                "stdout_tail": import_result.get("stdout_tail", ""),
                "stderr_tail": import_result.get("stderr_tail", ""),
            },
        )
        sys.exit(1)

    health_url = entry.get("health", "http://localhost:8401/health")
    host, port = _parse_port_from_url(health_url)

    proc, _start_launch = start_process(entry)
    tcp_result = wait_for_tcp(host, port, proc)
    checks["start"] = {"passed": tcp_result["passed"], "duration_ms": tcp_result["duration_ms"]}
    stages_completed.append("start")

    if not tcp_result["passed"]:
        if proc.poll() is not None:
            summary = f"process exited with code {tcp_result.get('exit_code')} before binding port"
        else:
            summary = f"port {port} not open after {TCP_READINESS_TIMEOUT}s"
            with _suppress():
                _kill_process_group(proc)
        _write_report(
            generation=generation,
            failure_stage="start",
            checks=checks,
            fitness=compute_fitness(checks, manifest, stages_completed),
            diagnostics={
                "stage": "start",
                "summary": summary,
                "exit_code": tcp_result.get("exit_code"),
                "failures": [],
                "stdout_tail": tcp_result.get("stdout_tail", ""),
                "stderr_tail": tcp_result.get("stderr_tail", ""),
            },
        )
        sys.exit(1)

    # -----------------------------------------------------------------------
    # Stage 5: Health / contracts
    # -----------------------------------------------------------------------
    log.info("stage_start", stage="health", health_url=health_url)
    health_result = run_health_check(
        health_url, contracts, generation, manifest, preread_spec_vectors
    )

    # M3 dual-run: evaluate baseline contracts against the running server (informational).
    # Must run before killing the process — server is still up at this point.
    baseline_check = run_baseline_check(health_url, generation)
    if baseline_check is not None:
        health_result = dict(health_result)
        health_result["baseline-contracts"] = baseline_check
        log.info(
            "baseline_check_complete",
            baseline_generation=baseline_check.get("baseline-generation"),
            passed=baseline_check["passed"],
            contracts=len(baseline_check.get("contracts", {})),
        )

    checks["health"] = health_result
    stages_completed.append("health")

    # Kill the artifact process group — ensures any daemonised children are also reaped
    with _suppress():
        _kill_process_group(proc)

    if not health_result["passed"]:
        error_msg = health_result.get("error", "")
        contracts_data = health_result.get("contracts", {})
        if contracts_data:
            failed_contracts = [
                f"contract {name} failed: {result.get('error', 'unknown')}"
                for name, result in contracts_data.items()
                if not result.get("passed")
            ]
            summary = "; ".join(failed_contracts) if failed_contracts else "health check failed"
        else:
            summary = error_msg or "health check failed"

        _write_report(
            generation=generation,
            failure_stage="health",
            checks=checks,
            fitness=compute_fitness(checks, manifest, stages_completed),
            diagnostics={
                "stage": "health",
                "summary": summary,
                "exit_code": None,
                "failures": [],
                "stdout_tail": "",
                "stderr_tail": "",
            },
        )
        sys.exit(1)

    # All stages passed
    fitness = compute_fitness(checks, manifest, stages_completed)
    log.info("pipeline_viable")
    _write_report(
        generation=generation,
        failure_stage="none",
        checks=checks,
        fitness=fitness,
    )


@contextlib.contextmanager
def _suppress():  # type: ignore[return]
    with contextlib.suppress(Exception):
        yield


# ---------------------------------------------------------------------------
# Report writer
# ---------------------------------------------------------------------------


def _write_report(
    *,
    generation: int,
    failure_stage: str,
    checks: dict[str, Any],
    fitness: dict[str, Any],
    diagnostics: dict[str, Any] | None = None,
) -> None:
    report: dict[str, Any] = {
        "generation": generation,
        "status": "viable" if failure_stage == "none" else "non-viable",
        "failure_stage": failure_stage,
        "checks": checks,
        "fitness": fitness,
        "completed_at": datetime.now(UTC).isoformat(),
    }
    if diagnostics:
        report["diagnostics"] = diagnostics
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    report_path = OUTPUT_DIR / "viability-report.json"
    report_path.write_text(json.dumps(report, indent=2))
    log.info("report_written", path=str(report_path))


def _configure_logging() -> None:
    structlog.configure(
        processors=[
            structlog.stdlib.add_log_level,
            structlog.processors.TimeStamper(fmt="iso"),
            structlog.dev.ConsoleRenderer(),
        ]
    )


if __name__ == "__main__":
    _configure_logging()
    run_pipeline()
