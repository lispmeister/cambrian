"""
Cambrian Test Rig — mechanical verification pipeline.

Stages: manifest → build → test → start → health → report

Run inside a container with the artifact mounted at /workspace.
Writes viability-report.json to /workspace on completion.
"""

import contextlib
import json
import os
import re
import socket
import subprocess
import sys
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

WORKSPACE = Path(os.environ.get("CAMBRIAN_WORKSPACE", "/workspace"))
HEALTH_TIMEOUT = 10  # seconds per health-check request
TCP_READINESS_TIMEOUT = 30  # seconds to wait for port
TCP_POLL_INTERVAL = 0.5
CAMBRIAN_VERSION = 1

# All 5 stage names in order — used to populate unattempted checks
ALL_STAGES = ["manifest", "build", "test", "start", "health"]


# ---------------------------------------------------------------------------
# Stage 1: Manifest validation
# ---------------------------------------------------------------------------

_SPEC_HASH_RE = re.compile(r"^sha256:[0-9a-f]{64}$")
_ISO8601_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}")
_URL_RE = re.compile(r"^https?://")


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
    elif "manifest.json" not in files:
        errors.append("files: must include 'manifest.json'")

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
    for f in manifest.get("files", []):
        if str(f).endswith("CAMBRIAN-SPEC-005.md"):
            candidate = (WORKSPACE / f).resolve()
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
) -> dict[str, Any]:
    """Run health check — spec vectors first, then manifest contracts, then fallback."""
    t0 = time.monotonic()

    # --- Extract spec vectors (Layer 1) ---
    spec_vectors: list[dict[str, Any]] = []
    if manifest is not None:
        spec_file = _find_spec_file(manifest)
        if spec_file is not None:
            try:
                spec_text = spec_file.read_text(encoding="utf-8")
                spec_vectors = _extract_spec_vectors(spec_text)
            except Exception as e:
                print(f"[test-rig] Warning: failed to read spec vectors: {e}", flush=True)

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
        body = _substitute_generation(body, generation)

    # body — exact match
    if "body" in expect:
        if body != expect["body"]:
            return {
                "passed": False,
                "duration_ms": duration_ms,
                "error": f"body mismatch: expected {expect['body']!r}, got {body!r}",
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
# Fitness vector
# ---------------------------------------------------------------------------


def compute_fitness(
    checks: dict[str, Any],
    manifest: dict[str, Any],
    stages_completed: list[str],
) -> dict[str, Any]:
    """Compute the 15-metric fitness vector."""
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

    fitness["total_duration_ms"] = sum(checks.get(s, {}).get("duration_ms", 0) for s in ALL_STAGES)

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


# ---------------------------------------------------------------------------
# Utility
# ---------------------------------------------------------------------------


def _tail_lines(text: str, n: int) -> str:
    """Return the last n lines of text."""
    lines = text.splitlines(keepends=True)
    return "".join(lines[-n:])


def _skipped_check() -> dict[str, Any]:
    """Standard entry for a stage that was not attempted."""
    return {"passed": False, "duration_ms": 0}


# ---------------------------------------------------------------------------
# Main pipeline
# ---------------------------------------------------------------------------


def run_pipeline() -> None:
    manifest_path = WORKSPACE / "manifest.json"
    stages_completed: list[str] = []

    # Build the full checks dict with skipped entries upfront
    checks: dict[str, Any] = {s: _skipped_check() for s in ALL_STAGES}

    # -----------------------------------------------------------------------
    # Stage 1: Manifest
    # -----------------------------------------------------------------------
    print("[test-rig] Stage: manifest", flush=True)

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

    # -----------------------------------------------------------------------
    # Stage 2: Build
    # -----------------------------------------------------------------------
    print("[test-rig] Stage: build", flush=True)
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
    # Stage 3: Test
    # -----------------------------------------------------------------------
    print("[test-rig] Stage: test", flush=True)
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
    print("[test-rig] Stage: start", flush=True)
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
                proc.kill()
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
    print(f"[test-rig] Stage: health ({health_url})", flush=True)
    health_result = run_health_check(health_url, contracts, generation, manifest)
    checks["health"] = health_result
    stages_completed.append("health")

    # Terminate the artifact process
    with _suppress():
        proc.terminate()
    try:
        proc.wait(timeout=5)
    except subprocess.TimeoutExpired:
        with _suppress():
            proc.kill()

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
    print("[test-rig] All stages passed — viable", flush=True)
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
    report_path = WORKSPACE / "viability-report.json"
    report_path.write_text(json.dumps(report, indent=2))
    print(f"[test-rig] Report written to {report_path}", flush=True)


if __name__ == "__main__":
    run_pipeline()
