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
    output_dir = tmp_path / "output"
    output_dir.mkdir()
    monkeypatch.setenv("CAMBRIAN_WORKSPACE", str(tmp_path))
    monkeypatch.setenv("CAMBRIAN_OUTPUT_DIR", str(output_dir))
    # Reload test_rig to pick up the new WORKSPACE / OUTPUT_DIR paths
    import sys

    if "test_rig" in sys.modules:
        del sys.modules["test_rig"]


@pytest.fixture
def workspace(tmp_path: Path) -> Path:
    return tmp_path


@pytest.fixture
def output_dir(tmp_path: Path) -> Path:
    return tmp_path / "output"


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

    def test_fitness_weights_always_present(self, workspace: Path) -> None:
        import test_rig

        m = _write_manifest(workspace)
        checks = {s: {"passed": False, "duration_ms": 0} for s in test_rig.ALL_STAGES}
        fitness = test_rig.compute_fitness(checks, m, [])
        weights = fitness["fitness_weights"]
        assert weights["test_count"] == 0.5
        assert weights["test_pass_rate"] == 0.5
        assert weights["trivial_assert_rate"] == 0.5

    def test_assertion_density_no_test_files(self, workspace: Path) -> None:
        import test_rig

        # No test files in manifest → assertion_density = 0.0
        m = _write_manifest(workspace, {"files": ["manifest.json", "server.py"]})
        checks = {s: {"passed": False, "duration_ms": 0} for s in test_rig.ALL_STAGES}
        fitness = test_rig.compute_fitness(checks, m, [])
        assert fitness["assertion_density"] == 0.0
        assert fitness["trivial_assert_rate"] == 0.0

    def test_assertion_density_computed(self, workspace: Path) -> None:
        import test_rig

        (workspace / "test_server.py").write_text(
            "def test_ok():\n    assert resp.status == 200\n    assert body['ok'] is True\n\n"
            "def test_fail():\n    assert x == 1\n"
        )
        m = _write_manifest(workspace, {"files": ["manifest.json", "test_server.py"]})
        checks = {s: {"passed": False, "duration_ms": 0} for s in test_rig.ALL_STAGES}
        fitness = test_rig.compute_fitness(checks, m, [])
        # 3 assertions, 2 test functions → density = 1.5
        assert fitness["assertion_density"] == pytest.approx(1.5, rel=1e-4)
        # No trivial asserts → rate = 0.0
        assert fitness["trivial_assert_rate"] == 0.0

    def test_trivial_assert_rate_detected(self, workspace: Path) -> None:
        import test_rig

        (workspace / "test_bad.py").write_text(
            "def test_trivial():\n    assert True\n    assert False\n    assert None\n\n"
            "def test_real():\n    assert x == 1\n"
        )
        m = _write_manifest(workspace, {"files": ["manifest.json", "test_bad.py"]})
        checks = {s: {"passed": False, "duration_ms": 0} for s in test_rig.ALL_STAGES}
        fitness = test_rig.compute_fitness(checks, m, [])
        # 4 asserts total, 3 trivial → rate = 0.75
        assert fitness["trivial_assert_rate"] == pytest.approx(0.75, rel=1e-4)
        # 4 asserts, 2 functions → density = 2.0
        assert fitness["assertion_density"] == pytest.approx(2.0, rel=1e-4)


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
    def test_unattempted_stages_have_passed_false(self, workspace: Path, output_dir: Path) -> None:
        """Stages after a failure must appear with passed=False, duration_ms=0."""
        import test_rig

        # Write an invalid manifest — causes failure at manifest stage
        (workspace / "manifest.json").write_text('{"cambrian-version": 99}')

        with pytest.raises(SystemExit):
            test_rig.run_pipeline()

        report_path = output_dir / "viability-report.json"
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
    def test_manifest_failure_diagnostics(self, workspace: Path, output_dir: Path) -> None:
        import test_rig

        (workspace / "manifest.json").write_text('{"cambrian-version": 99}')

        with pytest.raises(SystemExit):
            test_rig.run_pipeline()

        report = json.loads((output_dir / "viability-report.json").read_text())
        diag = report.get("diagnostics")
        assert diag is not None
        assert diag["stage"] == "manifest"
        assert isinstance(diag["summary"], str) and diag["summary"]
        assert diag["exit_code"] is None
        assert isinstance(diag["failures"], list)
        assert isinstance(diag["stdout_tail"], str)
        assert isinstance(diag["stderr_tail"], str)

    def test_non_viable_has_diagnostics(self, workspace: Path, output_dir: Path) -> None:
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

        report = json.loads((output_dir / "viability-report.json").read_text())
        assert report["status"] == "non-viable"
        assert "diagnostics" in report

    def test_viable_has_no_diagnostics(self, workspace: Path, output_dir: Path) -> None:
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
        report = json.loads((output_dir / "viability-report.json").read_text())
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


# ---------------------------------------------------------------------------
# run_build tests
# ---------------------------------------------------------------------------


class TestRunBuild:
    def test_empty_build_command_passes(self, workspace: Path) -> None:
        import test_rig

        result = test_rig.run_build({"build": ""})
        assert result["passed"] is True
        assert result["duration_ms"] == 0

    def test_successful_build(self, workspace: Path) -> None:
        import test_rig

        mock_result = MagicMock()
        mock_result.returncode = 0
        with patch("subprocess.run", return_value=mock_result):
            result = test_rig.run_build({"build": "pip install -r requirements.txt"})
        assert result["passed"] is True
        assert "duration_ms" in result

    def test_failed_build(self, workspace: Path) -> None:
        import test_rig

        mock_result = MagicMock()
        mock_result.returncode = 1
        mock_result.stdout = "some output"
        mock_result.stderr = "error message"
        with patch("subprocess.run", return_value=mock_result):
            result = test_rig.run_build({"build": "pip install missing-pkg"})
        assert result["passed"] is False
        assert result["exit_code"] == 1
        assert "error message" in result["stderr_tail"]

    def test_build_timeout(self, workspace: Path) -> None:
        import subprocess

        import test_rig

        exc = subprocess.TimeoutExpired("pip install", 300)
        exc.stdout = b"partial output"
        exc.stderr = b"partial error"
        with patch("subprocess.run", side_effect=exc):
            result = test_rig.run_build({"build": "pip install slow-pkg"})
        assert result["passed"] is False
        assert result["exit_code"] is None
        assert result["timed_out"] is True


# ---------------------------------------------------------------------------
# run_tests tests
# ---------------------------------------------------------------------------


class TestRunTests:
    def test_empty_test_command_passes(self, workspace: Path) -> None:
        import test_rig

        result = test_rig.run_tests({"test": ""})
        assert result["passed"] is True
        assert result["tests_run"] == 0
        assert result["tests_passed"] == 0

    def test_successful_tests(self, workspace: Path) -> None:
        import test_rig

        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = "5 passed in 1.2s"
        mock_result.stderr = ""
        with patch("subprocess.run", return_value=mock_result):
            result = test_rig.run_tests({"test": "pytest tests/"})
        assert result["passed"] is True
        assert result["tests_run"] == 5
        assert result["tests_passed"] == 5

    def test_failed_tests_with_failures_parsed(self, workspace: Path) -> None:
        import test_rig

        mock_result = MagicMock()
        mock_result.returncode = 1
        mock_result.stdout = (
            "FAILED tests/test_api.py::test_spawn - AssertionError: x\n3 failed in 0.5s"
        )
        mock_result.stderr = ""
        with patch("subprocess.run", return_value=mock_result):
            result = test_rig.run_tests({"test": "pytest tests/"})
        assert result["passed"] is False
        assert result["tests_run"] == 3
        assert result["tests_passed"] == 0
        assert len(result["failures"]) == 1

    def test_test_timeout(self, workspace: Path) -> None:
        import subprocess

        import test_rig

        exc = subprocess.TimeoutExpired("pytest", 120)
        exc.stdout = b""
        exc.stderr = b"timeout"
        with patch("subprocess.run", side_effect=exc):
            result = test_rig.run_tests({"test": "pytest tests/"})
        assert result["passed"] is False
        assert result["timed_out"] is True
        assert result["tests_run"] == -1
        assert result["tests_passed"] == -1


# ---------------------------------------------------------------------------
# start_process tests
# ---------------------------------------------------------------------------


class TestStartProcess:
    def test_start_returns_popen_and_result(self, workspace: Path) -> None:
        import subprocess

        import test_rig

        mock_proc = MagicMock(spec=subprocess.Popen)
        with patch("subprocess.Popen", return_value=mock_proc):
            proc, result = test_rig.start_process({"start": "python src/server.py"})
        assert proc is mock_proc
        assert result["passed"] is True
        assert "duration_ms" in result


# ---------------------------------------------------------------------------
# _run_fallback_health tests
# ---------------------------------------------------------------------------


class TestFallbackHealth:
    def _mock_http_response(self, status: int, body: bytes = b'{"generation": 1}') -> Any:
        resp = MagicMock()
        resp.status = status
        resp.read.return_value = body
        resp.__enter__ = MagicMock(return_value=resp)
        resp.__exit__ = MagicMock(return_value=False)
        return resp

    def test_health_and_stats_pass(self, workspace: Path) -> None:
        import time

        import test_rig

        ok_resp = self._mock_http_response(200)
        with patch("urllib.request.urlopen", return_value=ok_resp):
            result = test_rig._run_fallback_health("http://localhost:8401/health", time.monotonic())
        assert result["passed"] is True

    def test_health_fails(self, workspace: Path) -> None:
        import time
        import urllib.error

        import test_rig

        with patch("urllib.request.urlopen", side_effect=urllib.error.URLError("refused")):
            result = test_rig._run_fallback_health("http://localhost:8401/health", time.monotonic())
        assert result["passed"] is False
        assert "error" in result

    def test_stats_missing_generation_field(self, workspace: Path) -> None:
        import time

        import test_rig

        call_count = 0

        def side_effect(url: str, timeout: int) -> Any:
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return self._mock_http_response(200)  # health passes
            return self._mock_http_response(200, b'{"status": "idle"}')  # stats missing generation

        with patch("urllib.request.urlopen", side_effect=side_effect):
            result = test_rig._run_fallback_health("http://localhost:8401/health", time.monotonic())
        assert result["passed"] is False
        assert "generation" in result["error"]


# ---------------------------------------------------------------------------
# Spec vector extraction
# ---------------------------------------------------------------------------

_FROZEN_BLOCK = """\
<!-- BEGIN FROZEN: acceptance-vectors -->

```spec-vector
name: sv-a
type: http
method: GET
path: /health
expect:
  status: 200
```

```spec-vector
name: sv-b
type: http
method: GET
path: /stats
expect:
  status: 200
  body_has_keys:
    - generation
```

<!-- END FROZEN: acceptance-vectors -->
"""


class TestExtractSpecVectors:
    def test_no_frozen_markers_returns_empty(self, workspace: Path) -> None:
        import test_rig

        assert test_rig._extract_spec_vectors("no frozen block here") == []

    def test_no_spec_vector_blocks_returns_empty(self, workspace: Path) -> None:
        import test_rig

        text = (
            "<!-- BEGIN FROZEN: acceptance-vectors -->\n"
            "no code blocks\n"
            "<!-- END FROZEN: acceptance-vectors -->"
        )
        assert test_rig._extract_spec_vectors(text) == []

    def test_extracts_all_vectors(self, workspace: Path) -> None:
        import test_rig

        vectors = test_rig._extract_spec_vectors(_FROZEN_BLOCK)
        assert len(vectors) == 2
        assert vectors[0]["name"] == "sv-a"
        assert vectors[1]["name"] == "sv-b"

    def test_vector_fields_parsed_correctly(self, workspace: Path) -> None:
        import test_rig

        vectors = test_rig._extract_spec_vectors(_FROZEN_BLOCK)
        v = vectors[1]
        assert v["method"] == "GET"
        assert v["path"] == "/stats"
        assert v["expect"]["body_has_keys"] == ["generation"]

    def test_skips_non_dict_yaml(self, workspace: Path) -> None:
        import test_rig

        text = (
            "<!-- BEGIN FROZEN: acceptance-vectors -->\n"
            "```spec-vector\n- just a list\n```\n"
            "<!-- END FROZEN: acceptance-vectors -->"
        )
        assert test_rig._extract_spec_vectors(text) == []

    def test_begin_after_end_returns_empty(self, workspace: Path) -> None:
        import test_rig

        text = "<!-- END FROZEN: acceptance-vectors -->\n<!-- BEGIN FROZEN: acceptance-vectors -->"
        assert test_rig._extract_spec_vectors(text) == []


# ---------------------------------------------------------------------------
# Spec file discovery
# ---------------------------------------------------------------------------


class TestFindSpecFile:
    def test_returns_none_when_not_in_workspace(self, workspace: Path) -> None:
        import test_rig

        manifest: dict[str, Any] = {"files": ["manifest.json"]}
        assert test_rig._find_spec_file(manifest) is None

    def test_finds_via_manifest_files_array(self, workspace: Path) -> None:
        import test_rig

        spec_path = workspace / "CAMBRIAN-SPEC-005.md"
        spec_path.write_text("spec content")
        manifest: dict[str, Any] = {"files": ["manifest.json", "CAMBRIAN-SPEC-005.md"]}
        found = test_rig._find_spec_file(manifest)
        assert found == spec_path

    def test_finds_via_glob_when_not_in_files(self, workspace: Path) -> None:
        import test_rig

        spec_path = workspace / "spec" / "CAMBRIAN-SPEC-005.md"
        spec_path.parent.mkdir()
        spec_path.write_text("spec content")
        manifest: dict[str, Any] = {"files": ["manifest.json"]}
        found = test_rig._find_spec_file(manifest)
        assert found == spec_path

    def test_manifest_files_entry_missing_from_disk_falls_back_to_glob(
        self, workspace: Path
    ) -> None:
        import test_rig

        # manifest references a file that doesn't exist → glob finds the real one
        spec_path = workspace / "CAMBRIAN-SPEC-005.md"
        spec_path.write_text("spec content")
        manifest: dict[str, Any] = {"files": ["nonexistent/CAMBRIAN-SPEC-005.md"]}
        found = test_rig._find_spec_file(manifest)
        assert found == spec_path


# ---------------------------------------------------------------------------
# Spec vector evaluation in run_health_check
# ---------------------------------------------------------------------------


class TestRunHealthCheckWithSpecVectors:
    def _make_ok_response(self, body: bytes = b'{"generation": 2, "status": "idle"}') -> Any:
        resp = MagicMock()
        resp.status = 200
        resp.read.return_value = body
        resp.__enter__ = MagicMock(return_value=resp)
        resp.__exit__ = MagicMock(return_value=False)
        return resp

    def _make_err_response(self, status: int, body: bytes = b"{}") -> Any:
        import urllib.error

        e = urllib.error.HTTPError(url="", code=status, msg="", hdrs=None, fp=None)  # type: ignore[arg-type]
        e.read = MagicMock(return_value=body)
        return e

    def test_spec_vectors_appear_in_result(self, workspace: Path) -> None:
        import test_rig

        spec_path = workspace / "CAMBRIAN-SPEC-005.md"
        spec_path.write_text(_FROZEN_BLOCK)
        manifest: dict[str, Any] = {"files": ["CAMBRIAN-SPEC-005.md"]}

        with patch("urllib.request.urlopen", return_value=self._make_ok_response()):
            result = test_rig.run_health_check("http://localhost:8401/health", None, 2, manifest)

        assert "spec-vectors" in result
        assert "sv-a" in result["spec-vectors"]
        assert "sv-b" in result["spec-vectors"]

    def test_failing_spec_vector_fails_health(self, workspace: Path) -> None:
        import test_rig

        spec_path = workspace / "CAMBRIAN-SPEC-005.md"
        spec_path.write_text(_FROZEN_BLOCK)
        manifest: dict[str, Any] = {"files": ["CAMBRIAN-SPEC-005.md"]}

        # sv-a expects 200 but server returns 500
        with patch("urllib.request.urlopen", side_effect=self._make_err_response(500)):
            result = test_rig.run_health_check("http://localhost:8401/health", None, 2, manifest)

        assert result["passed"] is False
        assert result["spec-vectors"]["sv-a"]["passed"] is False

    def test_spec_vectors_evaluated_even_when_all_pass(self, workspace: Path) -> None:
        import test_rig

        spec_path = workspace / "CAMBRIAN-SPEC-005.md"
        spec_path.write_text(_FROZEN_BLOCK)
        manifest: dict[str, Any] = {"files": ["CAMBRIAN-SPEC-005.md"]}

        with patch("urllib.request.urlopen", return_value=self._make_ok_response()):
            result = test_rig.run_health_check("http://localhost:8401/health", None, 2, manifest)

        assert result["passed"] is True
        assert all(v["passed"] for v in result["spec-vectors"].values())

    def test_no_spec_file_falls_back_to_contracts(self, workspace: Path) -> None:
        import test_rig

        manifest: dict[str, Any] = {"files": ["manifest.json"]}
        contracts = [
            {
                "name": "c1",
                "type": "http",
                "method": "GET",
                "path": "/health",
                "expect": {"status": 200},
            }
        ]

        with patch("urllib.request.urlopen", return_value=self._make_ok_response()):
            result = test_rig.run_health_check(
                "http://localhost:8401/health", contracts, 2, manifest
            )

        assert "spec-vectors" not in result
        assert "contracts" in result

    def test_no_spec_file_no_contracts_uses_fallback(self, workspace: Path) -> None:
        import test_rig

        manifest: dict[str, Any] = {"files": ["manifest.json"]}

        with patch("urllib.request.urlopen", return_value=self._make_ok_response()):
            result = test_rig.run_health_check("http://localhost:8401/health", None, 2, manifest)

        # Fallback result has no spec-vectors or contracts sub-objects
        assert "spec-vectors" not in result
        assert "contracts" not in result

    def test_spec_vectors_and_contracts_both_evaluated(self, workspace: Path) -> None:
        import test_rig

        spec_path = workspace / "CAMBRIAN-SPEC-005.md"
        spec_path.write_text(_FROZEN_BLOCK)
        manifest: dict[str, Any] = {"files": ["CAMBRIAN-SPEC-005.md"]}
        contracts = [
            {
                "name": "c1",
                "type": "http",
                "method": "GET",
                "path": "/health",
                "expect": {"status": 200},
            }
        ]

        with patch("urllib.request.urlopen", return_value=self._make_ok_response()):
            result = test_rig.run_health_check(
                "http://localhost:8401/health", contracts, 2, manifest
            )

        assert "spec-vectors" in result
        assert "contracts" in result


# ---------------------------------------------------------------------------
# spec_vector_pass_rate in compute_fitness
# ---------------------------------------------------------------------------


class TestComputeFitnessSpecVectorPassRate:
    def test_spec_vector_pass_rate_present_when_vectors_in_checks(self, workspace: Path) -> None:
        import test_rig

        checks = {
            "health": {
                "passed": True,
                "duration_ms": 50,
                "spec-vectors": {
                    "sv-a": {"passed": True, "duration_ms": 10},
                    "sv-b": {"passed": False, "duration_ms": 10, "error": "status mismatch"},
                },
            }
        }
        fitness = test_rig.compute_fitness(checks, {}, ["health"])
        assert "spec_vector_pass_rate" in fitness
        assert fitness["spec_vector_pass_rate"] == 0.5

    def test_spec_vector_pass_rate_absent_without_spec_vectors(self, workspace: Path) -> None:
        import test_rig

        checks = {"health": {"passed": True, "duration_ms": 50}}
        fitness = test_rig.compute_fitness(checks, {}, ["health"])
        assert "spec_vector_pass_rate" not in fitness

    def test_spec_vector_pass_rate_all_pass(self, workspace: Path) -> None:
        import test_rig

        checks = {
            "health": {
                "passed": True,
                "duration_ms": 50,
                "spec-vectors": {
                    "sv-a": {"passed": True, "duration_ms": 10},
                    "sv-b": {"passed": True, "duration_ms": 10},
                },
            }
        }
        fitness = test_rig.compute_fitness(checks, {}, ["health"])
        assert fitness["spec_vector_pass_rate"] == 1.0
