"""
Cambrian Test Rig — mechanical verification pipeline.

Stages: build → test → start → health → report

Run inside a container with the artifact mounted at /workspace.
Writes viability-report.json to /workspace on completion.
"""
import json
import os
import re
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


WORKSPACE = Path(os.environ.get("CAMBRIAN_WORKSPACE", "/workspace"))
HEALTH_TIMEOUT = 10  # seconds — if Prime doesn't respond, fail start stage
HEALTH_POLL_INTERVAL = 0.5


# ---------------------------------------------------------------------------
# Stage runners
# ---------------------------------------------------------------------------

def run_build(entry: dict[str, Any]) -> dict[str, Any]:
    cmd = entry.get("build", "")
    if not cmd:
        return {"passed": True, "duration_ms": 0}

    t0 = time.monotonic()
    result = subprocess.run(
        cmd, shell=True, cwd=WORKSPACE,
        capture_output=True, text=True, timeout=300,
    )
    duration_ms = int((time.monotonic() - t0) * 1000)

    if result.returncode != 0:
        return {
            "passed": False,
            "duration_ms": duration_ms,
            "stdout_tail": result.stdout[-3000:],
            "stderr_tail": result.stderr[-3000:],
        }
    return {"passed": True, "duration_ms": duration_ms}


def run_tests(entry: dict[str, Any]) -> dict[str, Any]:
    cmd = entry.get("test", "")
    if not cmd:
        return {"passed": True, "duration_ms": 0, "tests_passed": 0, "tests_run": 0}

    t0 = time.monotonic()
    result = subprocess.run(
        cmd, shell=True, cwd=WORKSPACE,
        capture_output=True, text=True, timeout=120,
    )
    duration_ms = int((time.monotonic() - t0) * 1000)

    # Parse pytest summary line: "X passed" or "X failed, Y passed"
    tests_passed, tests_run = _parse_pytest_counts(result.stdout + result.stderr)

    if result.returncode != 0:
        return {
            "passed": False,
            "duration_ms": duration_ms,
            "tests_passed": tests_passed,
            "tests_run": tests_run,
            "stdout_tail": result.stdout[-3000:],
            "stderr_tail": result.stderr[-3000:],
        }
    return {
        "passed": True,
        "duration_ms": duration_ms,
        "tests_passed": tests_passed,
        "tests_run": tests_run,
    }


def _parse_pytest_counts(output: str) -> tuple[int, int]:
    """Extract (passed, total) from pytest output. Returns (0, 0) if unparseable."""
    # Match lines like "3 passed", "2 passed, 1 failed", "1 failed"
    passed = 0
    failed = 0
    for match in re.finditer(r"(\d+) (passed|failed|error)", output):
        count, kind = int(match.group(1)), match.group(2)
        if kind == "passed":
            passed = count
        elif kind in ("failed", "error"):
            failed = count
    return passed, passed + failed


def start_process(entry: dict[str, Any]) -> tuple[subprocess.Popen[str], dict[str, Any]]:
    """Start the artifact server. Returns (process, check_result)."""
    cmd = entry.get("start", "")
    t0 = time.monotonic()
    proc = subprocess.Popen(
        cmd, shell=True, cwd=WORKSPACE,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
    )
    # Give it a moment to crash if it's going to
    time.sleep(0.5)
    retcode = proc.poll()
    duration_ms = int((time.monotonic() - t0) * 1000)

    if retcode is not None:
        stdout = proc.stdout.read() if proc.stdout else ""
        stderr = proc.stderr.read() if proc.stderr else ""
        return proc, {
            "passed": False,
            "duration_ms": duration_ms,
            "stdout_tail": stdout[-3000:],
            "stderr_tail": stderr[-3000:],
        }
    return proc, {"passed": True, "duration_ms": duration_ms}


def run_health_check(health_url: str) -> dict[str, Any]:
    """Poll /health until 200 or HEALTH_TIMEOUT seconds elapse."""
    import urllib.request
    import urllib.error

    t0 = time.monotonic()
    deadline = t0 + HEALTH_TIMEOUT
    last_error = ""

    while time.monotonic() < deadline:
        try:
            with urllib.request.urlopen(health_url, timeout=2) as resp:
                if resp.status == 200:
                    duration_ms = int((time.monotonic() - t0) * 1000)
                    return {"passed": True, "duration_ms": duration_ms}
        except Exception as e:
            last_error = str(e)
        time.sleep(HEALTH_POLL_INTERVAL)

    duration_ms = int((time.monotonic() - t0) * 1000)
    return {
        "passed": False,
        "duration_ms": duration_ms,
        "error": f"Health check timed out after {HEALTH_TIMEOUT}s. Last error: {last_error}",
    }


# ---------------------------------------------------------------------------
# Fitness vector
# ---------------------------------------------------------------------------

def compute_fitness(
    checks: dict[str, Any],
    manifest: dict[str, Any],
    durations: dict[str, int],
) -> dict[str, Any]:
    test_result = checks.get("test", {})
    tests_passed = test_result.get("tests_passed", 0)
    tests_run = test_result.get("tests_run", 0)
    test_pass_rate = tests_passed / tests_run if tests_run > 0 else 0.0

    files = manifest.get("files", [])
    source_files = sum(1 for f in files if f.startswith("src/") and f.endswith(".py"))
    test_files = sum(1 for f in files if f.startswith("tests/") and f.endswith(".py"))

    total_duration_ms = sum(durations.values())

    return {
        "test_pass_rate": round(test_pass_rate, 4),
        "source_files": source_files,
        "test_files": test_files,
        "total_duration_ms": total_duration_ms,
    }


# ---------------------------------------------------------------------------
# Main pipeline
# ---------------------------------------------------------------------------

def run_pipeline() -> None:
    manifest_path = WORKSPACE / "manifest.json"

    # Stage 0: manifest check
    if not manifest_path.exists():
        _write_report(
            generation=0,
            failure_stage="manifest",
            checks={"manifest": {"passed": False}},
            fitness={},
            diagnostics={"stage": "manifest", "summary": "manifest.json not found"},
        )
        sys.exit(1)

    with manifest_path.open() as f:
        manifest: dict[str, Any] = json.load(f)

    generation = manifest.get("generation", 0)
    entry = manifest.get("entry", {})
    checks: dict[str, Any] = {"manifest": {"passed": True}}
    durations: dict[str, int] = {}

    # Stage 1: build
    print("[test-rig] Stage: build", flush=True)
    build_result = run_build(entry)
    checks["build"] = build_result
    durations["build"] = build_result.get("duration_ms", 0)
    if not build_result["passed"]:
        fitness = compute_fitness(checks, manifest, durations)
        _write_report(
            generation=generation,
            failure_stage="build",
            checks=checks,
            fitness=fitness,
            diagnostics={"stage": "build", "summary": "Build failed", **_extract_diag(build_result)},
        )
        sys.exit(1)

    # Stage 2: test
    print("[test-rig] Stage: test", flush=True)
    test_result = run_tests(entry)
    checks["test"] = test_result
    durations["test"] = test_result.get("duration_ms", 0)
    if not test_result["passed"]:
        tests_passed = test_result.get("tests_passed", 0)
        tests_run = test_result.get("tests_run", 0)
        fitness = compute_fitness(checks, manifest, durations)
        _write_report(
            generation=generation,
            failure_stage="test",
            checks=checks,
            fitness=fitness,
            diagnostics={
                "stage": "test",
                "summary": f"{tests_run - tests_passed} of {tests_run} tests failed",
                **_extract_diag(test_result),
            },
        )
        sys.exit(1)

    # Stage 3: start
    print("[test-rig] Stage: start", flush=True)
    proc, start_result = start_process(entry)
    checks["start"] = start_result
    durations["start"] = start_result.get("duration_ms", 0)
    if not start_result["passed"]:
        fitness = compute_fitness(checks, manifest, durations)
        _write_report(
            generation=generation,
            failure_stage="start",
            checks=checks,
            fitness=fitness,
            diagnostics={"stage": "start", "summary": "Process exited immediately", **_extract_diag(start_result)},
        )
        sys.exit(1)

    # Stage 4: health
    health_url = entry.get("health", "http://localhost:8401/health")
    print(f"[test-rig] Stage: health ({health_url})", flush=True)
    health_result = run_health_check(health_url)
    checks["health"] = health_result
    durations["health"] = health_result.get("duration_ms", 0)

    # Terminate the artifact process
    proc.terminate()
    try:
        proc.wait(timeout=5)
    except subprocess.TimeoutExpired:
        proc.kill()

    if not health_result["passed"]:
        fitness = compute_fitness(checks, manifest, durations)
        _write_report(
            generation=generation,
            failure_stage="health",
            checks=checks,
            fitness=fitness,
            diagnostics={"stage": "health", "summary": "Health check failed", **_extract_diag(health_result)},
        )
        sys.exit(1)

    # All stages passed
    fitness = compute_fitness(checks, manifest, durations)
    print("[test-rig] All stages passed — viable", flush=True)
    _write_report(
        generation=generation,
        failure_stage="none",
        checks=checks,
        fitness=fitness,
    )


def _extract_diag(result: dict[str, Any]) -> dict[str, Any]:
    diag: dict[str, Any] = {}
    if "stdout_tail" in result:
        diag["stdout_tail"] = result["stdout_tail"]
    if "stderr_tail" in result:
        diag["stderr_tail"] = result["stderr_tail"]
    if "error" in result:
        diag["error"] = result["error"]
    return diag


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
        "completed_at": datetime.now(timezone.utc).isoformat(),
        "fitness": fitness,
    }
    if diagnostics:
        report["diagnostics"] = diagnostics
    report_path = WORKSPACE / "viability-report.json"
    report_path.write_text(json.dumps(report, indent=2))
    print(f"[test-rig] Report written to {report_path}", flush=True)


if __name__ == "__main__":
    run_pipeline()
