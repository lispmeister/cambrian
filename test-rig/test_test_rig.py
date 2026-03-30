"""Unit tests for Test Rig pipeline, manifest validation, diagnostics, and fitness."""

import json
import time
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def set_workspace(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CAMBRIAN_WORKSPACE", str(tmp_path))
    # Reload test_rig to pick up the new WORKSPACE path
    import sys

    if "test_rig" in sys.modules:
        del sys.modules["test_rig"]


@pytest.fixture
def workspace(tmp_path: Path) -> Path:
    return tmp_path


def _write_manifest(workspace: Path, overrides: dict[str, Any] | None = None) -> dict[str, Any]:
    """Write a valid manifest.json to workspace and return it."""
    manifest: dict[str, Any] = {
        "cambrian-version": 1,
        "generation": 2,
        "parent-generation": 1,
        "spec-hash": "sha256:" + "a" * 64,
        "artifact-hash": "sha256:" + "b" * 64,
        "producer-model": "claude-sonnet-4-6",
        "token-usage": {"input": 1000, "output": 500},
        "files": ["manifest.json", "src/server.py", "tests/test_server.py"],
        "created-at": "2026-03-21T14:30:00Z",
        "entry": {
            "build": "pip install -r requirements.txt",
            "test": "pytest tests/",
            "start": "python src/server.py",
            "health": "http://localhost:8401/health",
        },
    }
    if overrides:
        # Deep merge for nested fields like "entry"
        for k, v in overrides.items():
            if isinstance(v, dict) and isinstance(manifest.get(k), dict):
                manifest[k] = {**manifest[k], **v}
            else:
                manifest[k] = v
    (workspace / "manifest.json").write_text(json.dumps(manifest))
    return manifest


# ---------------------------------------------------------------------------
# Manifest validation tests
# ---------------------------------------------------------------------------


class TestManifestValidation:
    def test_valid_manifest_no_errors(self, workspace: Path) -> None:
        import test_rig

        m = _write_manifest(workspace)
        errors = test_rig._validate_manifest(m)
        assert errors == []

    def test_wrong_cambrian_version(self, workspace: Path) -> None:
        import test_rig

        m = _write_manifest(workspace, {"cambrian-version": 2})
        errors = test_rig._validate_manifest(m)
        assert any("cambrian-version" in e for e in errors)

    def test_missing_spec_hash(self, workspace: Path) -> None:
        import test_rig

        m = _write_manifest(workspace)
        m.pop("spec-hash")
        errors = test_rig._validate_manifest(m)
        assert any("spec-hash" in e for e in errors)

    def test_invalid_spec_hash_format(self, workspace: Path) -> None:
        import test_rig

        m = _write_manifest(workspace, {"spec-hash": "not-a-hash"})
        errors = test_rig._validate_manifest(m)
        assert any("spec-hash" in e for e in errors)

    def test_files_missing_manifest_json(self, workspace: Path) -> None:
        import test_rig

        m = _write_manifest(workspace, {"files": ["src/server.py"]})
        errors = test_rig._validate_manifest(m)
        assert any("manifest.json" in e for e in errors)

    def test_empty_files_array(self, workspace: Path) -> None:
        import test_rig

        m = _write_manifest(workspace, {"files": []})
        errors = test_rig._validate_manifest(m)
        assert any("files" in e for e in errors)

    def test_invalid_token_usage(self, workspace: Path) -> None:
        import test_rig

        m = _write_manifest(workspace, {"token-usage": {"input": -1, "output": 0}})
        errors = test_rig._validate_manifest(m)
        assert any("token-usage.input" in e for e in errors)

    def test_invalid_health_url(self, workspace: Path) -> None:
        import test_rig

        m = _write_manifest(workspace)
        m["entry"]["health"] = "not-a-url"
        errors = test_rig._validate_manifest(m)
        assert any("entry.health" in e for e in errors)

    def test_invalid_created_at(self, workspace: Path) -> None:
        import test_rig

        m = _write_manifest(workspace, {"created-at": "not-a-date"})
        errors = test_rig._validate_manifest(m)
        assert any("created-at" in e for e in errors)

    def test_valid_contracts_no_errors(self, workspace: Path) -> None:
        import test_rig

        m = _write_manifest(
            workspace,
            {
                "contracts": [
                    {
                        "name": "health",
                        "type": "http",
                        "method": "GET",
                        "path": "/health",
                        "expect": {"status": 200},
                    }
                ]
            },
        )
        errors = test_rig._validate_manifest(m)
        assert errors == []

    def test_duplicate_contract_name(self, workspace: Path) -> None:
        import test_rig

        c = {"type": "http", "method": "GET", "expect": {"status": 200}}
        m = _write_manifest(
            workspace,
            {
                "contracts": [
                    {**c, "name": "check", "path": "/a"},
                    {**c, "name": "check", "path": "/b"},
                ]
            },
        )
        errors = test_rig._validate_manifest(m)
        assert any("duplicate" in e for e in errors)

    def test_contract_missing_status(self, workspace: Path) -> None:
        import test_rig

        m = _write_manifest(
            workspace,
            {
                "contracts": [
                    {"name": "check", "type": "http", "method": "GET", "path": "/a", "expect": {}}
                ]
            },
        )
        errors = test_rig._validate_manifest(m)
        assert any("status" in e for e in errors)


# ---------------------------------------------------------------------------
# Pytest count parsing tests
# ---------------------------------------------------------------------------


class TestParsePytestCounts:
    def test_all_passed(self) -> None:
        import test_rig

        assert test_rig._parse_pytest_counts("5 passed in 1.2s") == (5, 5)

    def test_some_failed(self) -> None:
        import test_rig

        assert test_rig._parse_pytest_counts("3 passed, 2 failed in 0.5s") == (3, 5)

    def test_only_failed(self) -> None:
        import test_rig

        passed, total = test_rig._parse_pytest_counts("4 failed in 0.3s")
        assert total == 4
        assert passed == 0

    def test_no_pytest_output_returns_minus_one(self) -> None:
        import test_rig

        assert test_rig._parse_pytest_counts("some random output") == (-1, -1)

    def test_empty_output_returns_minus_one(self) -> None:
        import test_rig

        assert test_rig._parse_pytest_counts("") == (-1, -1)


# ---------------------------------------------------------------------------
# Pytest failure extraction tests
# ---------------------------------------------------------------------------


class TestParsePytestFailures:
    def test_single_failure(self) -> None:
        import test_rig

        output = "FAILED tests/test_api.py::test_spawn - AssertionError: expected lab-gen-1"
        failures = test_rig._parse_pytest_failures(output)
        assert len(failures) == 1
        assert failures[0]["test"] == "tests/test_api.py::test_spawn"
        assert "AssertionError" in failures[0]["error"]
        assert failures[0]["file"] == "tests/test_api.py"

    def test_multiple_failures(self) -> None:
        import test_rig

        output = (
            "FAILED tests/test_api.py::test_a - AssertionError: foo\n"
            "FAILED tests/test_api.py::test_b - KeyError: 'generation'\n"
        )
        failures = test_rig._parse_pytest_failures(output)
        assert len(failures) == 2

    def test_no_failures_returns_empty(self) -> None:
        import test_rig

        assert test_rig._parse_pytest_failures("5 passed in 1.2s") == []

    def test_error_truncated_to_500_chars(self) -> None:
        import test_rig

        long_error = "x" * 600
        output = f"FAILED tests/test_api.py::test_a - {long_error}"
        failures = test_rig._parse_pytest_failures(output)
        assert len(failures[0]["error"]) <= 500


# ---------------------------------------------------------------------------
# _tail_lines tests
# ---------------------------------------------------------------------------


class TestTailLines:
    def test_short_text_unchanged(self) -> None:
        import test_rig

        text = "line1\nline2\nline3\n"
        assert test_rig._tail_lines(text, 100) == text

    def test_long_text_truncated(self) -> None:
        import test_rig

        lines = [f"line{i}\n" for i in range(200)]
        text = "".join(lines)
        result = test_rig._tail_lines(text, 100)
        result_lines = result.splitlines()
        assert len(result_lines) == 100
        assert "line199" in result

    def test_empty_string(self) -> None:
        import test_rig

        assert test_rig._tail_lines("", 100) == ""


# ---------------------------------------------------------------------------
# Fitness vector tests
# ---------------------------------------------------------------------------


class TestComputeFitness:
    def test_stages_completed_field(self, workspace: Path) -> None:
        import test_rig

        m = _write_manifest(workspace)
        checks = {s: {"passed": False, "duration_ms": 0} for s in test_rig.ALL_STAGES}
        checks["manifest"] = {"passed": True, "duration_ms": 0}
        checks["build"] = {"passed": True, "duration_ms": 1234}
        stages = ["manifest", "build"]
        fitness = test_rig.compute_fitness(checks, m, stages)
        assert fitness["stages_completed"] == ["manifest", "build"]

    def test_duration_metrics_from_checks(self, workspace: Path) -> None:
        import test_rig

        m = _write_manifest(workspace)
        checks: dict[str, Any] = {
            "manifest": {"passed": True, "duration_ms": 0},
            "build": {"passed": True, "duration_ms": 5000},
            "test": {"passed": True, "duration_ms": 2000, "tests_passed": 3, "tests_run": 3},
            "start": {"passed": False, "duration_ms": 0},
            "health": {"passed": False, "duration_ms": 0},
        }
        stages = ["manifest", "build", "test"]
        fitness = test_rig.compute_fitness(checks, m, stages)
        assert fitness["build_duration_ms"] == 5000
        assert fitness["test_duration_ms"] == 2000
        # start not in stages_completed → key absent
        assert "start_duration_ms" not in fitness

    def test_test_pass_rate(self, workspace: Path) -> None:
        import test_rig

        m = _write_manifest(workspace)
        checks: dict[str, Any] = {
            "manifest": {"passed": True, "duration_ms": 0},
            "build": {"passed": True, "duration_ms": 0},
            "test": {"passed": True, "duration_ms": 0, "tests_passed": 4, "tests_run": 5},
            "start": {"passed": False, "duration_ms": 0},
            "health": {"passed": False, "duration_ms": 0},
        }
        stages = ["manifest", "build", "test"]
        fitness = test_rig.compute_fitness(checks, m, stages)
        assert fitness["test_pass_rate"] == pytest.approx(0.8, rel=1e-4)

    def test_source_vs_test_file_classification(self, workspace: Path) -> None:
        import test_rig

        m = _write_manifest(
            workspace,
            {
                "files": [
                    "manifest.json",
                    "src/server.py",  # source
                    "src/utils.py",  # source
                    "tests/test_server.py",  # test (test* prefix)
                    "src/server_test.py",  # test (*_test*)
                ]
            },
        )
        stages = ["manifest"]
        checks = {s: {"passed": False, "duration_ms": 0} for s in test_rig.ALL_STAGES}
        checks["manifest"] = {"passed": True, "duration_ms": 0}
        fitness = test_rig.compute_fitness(checks, m, stages)
        assert fitness["source_files"] == 2  # server.py, utils.py
        assert fitness["test_files"] == 2  # test_server.py, server_test.py

    def test_dependency_count_from_requirements(self, workspace: Path) -> None:
        import test_rig

        (workspace / "requirements.txt").write_text("aiohttp>=3.9\n# a comment\n\nstructlog\n")
        m = _write_manifest(workspace)
        checks = {s: {"passed": False, "duration_ms": 0} for s in test_rig.ALL_STAGES}
        checks["manifest"] = {"passed": True, "duration_ms": 0}
        fitness = test_rig.compute_fitness(checks, m, ["manifest"])
        assert fitness["dependency_count"] == 2

    def test_contract_pass_rate_present_when_contracts(self, workspace: Path) -> None:
        import test_rig

        m = _write_manifest(workspace)
        checks: dict[str, Any] = {
            "manifest": {"passed": True, "duration_ms": 0},
            "build": {"passed": True, "duration_ms": 0},
            "test": {"passed": True, "duration_ms": 0, "tests_passed": 0, "tests_run": 0},
            "start": {"passed": True, "duration_ms": 0},
            "health": {
                "passed": True,
                "duration_ms": 0,
                "contracts": {
                    "c1": {"passed": True, "duration_ms": 5},
                    "c2": {"passed": False, "duration_ms": 3, "error": "oops"},
                },
            },
        }
        stages = list(test_rig.ALL_STAGES)
        fitness = test_rig.compute_fitness(checks, m, stages)
        assert fitness["contract_pass_rate"] == pytest.approx(0.5, rel=1e-4)

    def test_contract_pass_rate_absent_without_contracts(self, workspace: Path) -> None:
        import test_rig

        m = _write_manifest(workspace)
        checks: dict[str, Any] = {
            s: {"passed": True, "duration_ms": 0} for s in test_rig.ALL_STAGES
        }
        # health has no 'contracts' sub-key → fallback mode
        checks["test"] = {"passed": True, "duration_ms": 0, "tests_passed": 0, "tests_run": 0}
        fitness = test_rig.compute_fitness(checks, m, list(test_rig.ALL_STAGES))
        assert "contract_pass_rate" not in fitness

    def test_token_usage_copied(self, workspace: Path) -> None:
        import test_rig

        m = _write_manifest(workspace, {"token-usage": {"input": 9999, "output": 4444}})
        checks = {s: {"passed": False, "duration_ms": 0} for s in test_rig.ALL_STAGES}
        fitness = test_rig.compute_fitness(checks, m, [])
        assert fitness["token_input"] == 9999
        assert fitness["token_output"] == 4444


# ---------------------------------------------------------------------------
# Contract evaluation tests
# ---------------------------------------------------------------------------


class TestContractEvaluation:
    def _make_server_response(self, status: int, body: bytes = b"") -> Any:
        """Create a mock urllib response."""
        resp = MagicMock()
        resp.status = status
        resp.read.return_value = body
        resp.__enter__ = MagicMock(return_value=resp)
        resp.__exit__ = MagicMock(return_value=False)
        return resp

    def test_status_check_passes(self, workspace: Path) -> None:
        import test_rig

        contract = {
            "name": "health",
            "type": "http",
            "method": "GET",
            "path": "/health",
            "expect": {"status": 200},
        }
        mock_resp = self._make_server_response(200, b"{}")
        with patch("urllib.request.urlopen", return_value=mock_resp):
            result = test_rig._eval_contract("http://localhost:8401", contract, 2)
        assert result["passed"] is True

    def test_status_check_fails(self, workspace: Path) -> None:
        import test_rig

        contract = {
            "name": "health",
            "type": "http",
            "method": "GET",
            "path": "/health",
            "expect": {"status": 200},
        }
        mock_resp = self._make_server_response(503, b"")
        with patch("urllib.request.urlopen", return_value=mock_resp):
            result = test_rig._eval_contract("http://localhost:8401", contract, 2)
        assert result["passed"] is False
        assert "503" in result["error"]

    def test_body_has_keys_passes(self, workspace: Path) -> None:
        import test_rig

        contract = {
            "name": "stats",
            "type": "http",
            "method": "GET",
            "path": "/stats",
            "expect": {"status": 200, "body_has_keys": ["generation", "status", "uptime"]},
        }
        body = json.dumps({"generation": 2, "status": "idle", "uptime": 120}).encode()
        mock_resp = self._make_server_response(200, body)
        with patch("urllib.request.urlopen", return_value=mock_resp):
            result = test_rig._eval_contract("http://localhost:8401", contract, 2)
        assert result["passed"] is True

    def test_body_has_keys_missing_key(self, workspace: Path) -> None:
        import test_rig

        contract = {
            "name": "stats",
            "type": "http",
            "method": "GET",
            "path": "/stats",
            "expect": {"status": 200, "body_has_keys": ["generation", "uptime"]},
        }
        body = json.dumps({"generation": 2}).encode()
        mock_resp = self._make_server_response(200, body)
        with patch("urllib.request.urlopen", return_value=mock_resp):
            result = test_rig._eval_contract("http://localhost:8401", contract, 2)
        assert result["passed"] is False
        assert "uptime" in result["error"]

    def test_body_contains_with_generation_substitution(self, workspace: Path) -> None:
        import test_rig

        contract = {
            "name": "gen-check",
            "type": "http",
            "method": "GET",
            "path": "/stats",
            "expect": {"status": 200, "body_contains": {"generation": "$GENERATION"}},
        }
        body = json.dumps({"generation": 2, "status": "idle"}).encode()
        mock_resp = self._make_server_response(200, body)
        with patch("urllib.request.urlopen", return_value=mock_resp):
            result = test_rig._eval_contract("http://localhost:8401", contract, 2)
        assert result["passed"] is True

    def test_body_contains_wrong_value(self, workspace: Path) -> None:
        import test_rig

        contract = {
            "name": "gen-check",
            "type": "http",
            "method": "GET",
            "path": "/stats",
            "expect": {"status": 200, "body_contains": {"generation": "$GENERATION"}},
        }
        body = json.dumps({"generation": 99}).encode()
        mock_resp = self._make_server_response(200, body)
        with patch("urllib.request.urlopen", return_value=mock_resp):
            result = test_rig._eval_contract("http://localhost:8401", contract, 2)
        assert result["passed"] is False

    def test_all_contracts_evaluated_no_short_circuit(self, workspace: Path) -> None:
        import test_rig

        c = {"type": "http", "method": "GET"}
        contracts = [
            {**c, "name": "c1", "path": "/a", "expect": {"status": 404}},
            {**c, "name": "c2", "path": "/b", "expect": {"status": 200}},
        ]
        responses = [
            self._make_server_response(200, b"{}"),  # c1: status mismatch → fail
            self._make_server_response(200, b"{}"),  # c2: passes
        ]
        with patch("urllib.request.urlopen", side_effect=responses):
            result = test_rig._run_contracts(
                "http://localhost:8401", contracts, 2, time.monotonic()
            )
        # Both contracts evaluated
        assert "c1" in result["contracts"]
        assert "c2" in result["contracts"]
        assert result["contracts"]["c1"]["passed"] is False
        assert result["contracts"]["c2"]["passed"] is True
        assert result["passed"] is False


# ---------------------------------------------------------------------------
# Skipped stages in checks
# ---------------------------------------------------------------------------


class TestSkippedStages:
    def test_unattempted_stages_have_passed_false(self, workspace: Path) -> None:
        """Stages after a failure must appear with passed=False, duration_ms=0."""
        import test_rig

        # Write an invalid manifest — causes failure at manifest stage
        (workspace / "manifest.json").write_text('{"cambrian-version": 99}')

        with pytest.raises(SystemExit):
            test_rig.run_pipeline()

        report_path = workspace / "viability-report.json"
        assert report_path.exists()
        report = json.loads(report_path.read_text())
        checks = report["checks"]

        # All 5 stages must be present
        for stage in test_rig.ALL_STAGES:
            assert stage in checks, f"stage {stage!r} missing from checks"

        # Stages after manifest must be skipped
        for stage in ["build", "test", "start", "health"]:
            assert checks[stage]["passed"] is False
            assert checks[stage]["duration_ms"] == 0


# ---------------------------------------------------------------------------
# Diagnostics schema tests
# ---------------------------------------------------------------------------


class TestDiagnostics:
    def test_manifest_failure_diagnostics(self, workspace: Path) -> None:
        import test_rig

        (workspace / "manifest.json").write_text('{"cambrian-version": 99}')

        with pytest.raises(SystemExit):
            test_rig.run_pipeline()

        report = json.loads((workspace / "viability-report.json").read_text())
        diag = report.get("diagnostics")
        assert diag is not None
        assert diag["stage"] == "manifest"
        assert isinstance(diag["summary"], str) and diag["summary"]
        assert diag["exit_code"] is None
        assert isinstance(diag["failures"], list)
        assert isinstance(diag["stdout_tail"], str)
        assert isinstance(diag["stderr_tail"], str)

    def test_non_viable_has_diagnostics(self, workspace: Path) -> None:
        import test_rig

        # Write manifest but no build command that can succeed
        _write_manifest(
            workspace,
            {
                "entry": {
                    "build": "false",
                    "test": "pytest",
                    "start": "python -c 'import time; time.sleep(100)'",
                    "health": "http://localhost:9999/health",
                }
            },
        )
        with pytest.raises(SystemExit):
            test_rig.run_pipeline()

        report = json.loads((workspace / "viability-report.json").read_text())
        assert report["status"] == "non-viable"
        assert "diagnostics" in report

    def test_viable_has_no_diagnostics(self, workspace: Path) -> None:
        import test_rig

        m = _write_manifest(workspace)
        stages = list(test_rig.ALL_STAGES)
        checks: dict[str, Any] = {s: {"passed": True, "duration_ms": 0} for s in stages}
        checks["test"] = {"passed": True, "duration_ms": 0, "tests_passed": 0, "tests_run": 0}
        fitness = test_rig.compute_fitness(checks, m, stages)
        test_rig._write_report(
            generation=2,
            failure_stage="none",
            checks=checks,
            fitness=fitness,
        )
        report = json.loads((workspace / "viability-report.json").read_text())
        assert report["status"] == "viable"
        assert "diagnostics" not in report


# ---------------------------------------------------------------------------
# _substitute_generation tests
# ---------------------------------------------------------------------------


class TestSubstituteGeneration:
    def test_bare_string_substitution(self) -> None:
        import test_rig

        assert test_rig._substitute_generation("$GENERATION", 5) == 5

    def test_nested_string_substitution(self) -> None:
        import test_rig

        result = test_rig._substitute_generation("gen-$GENERATION", 3)
        assert result == "gen-3"

    def test_dict_substitution(self) -> None:
        import test_rig

        result = test_rig._substitute_generation({"generation": "$GENERATION"}, 7)
        assert result == {"generation": 7}

    def test_passthrough_non_string(self) -> None:
        import test_rig

        assert test_rig._substitute_generation(42, 5) == 42
        assert test_rig._substitute_generation(True, 5) is True


# ---------------------------------------------------------------------------
# TCP readiness tests
# ---------------------------------------------------------------------------


class TestWaitForTcp:
    def test_succeeds_when_port_opens(self, workspace: Path) -> None:
        import test_rig

        proc = MagicMock()
        proc.poll.return_value = None  # process running

        with patch("socket.create_connection") as mock_conn:
            mock_conn.return_value.__enter__ = MagicMock()
            mock_conn.return_value.__exit__ = MagicMock(return_value=False)
            result = test_rig.wait_for_tcp("localhost", 8401, proc)

        assert result["passed"] is True

    def test_fails_when_process_exits(self, workspace: Path) -> None:
        import test_rig

        proc = MagicMock()
        proc.poll.return_value = 1  # process has died
        proc.returncode = 1
        proc.stdout = None
        proc.stderr = None

        with patch("socket.create_connection", side_effect=OSError("refused")):
            result = test_rig.wait_for_tcp("localhost", 8401, proc)

        assert result["passed"] is False
        assert result.get("exit_code") == 1
