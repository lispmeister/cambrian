import importlib
import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
TEST_RIG_DIR = PROJECT_ROOT / "test-rig"
if str(TEST_RIG_DIR) not in sys.path:
    sys.path.insert(0, str(TEST_RIG_DIR))

test_rig = importlib.import_module("test_rig")


def test_run_syntax_check_detects_syntax_error(tmp_path, monkeypatch):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "bad.py").write_text("x = 'unterminated\n")

    monkeypatch.setattr(test_rig, "WORKSPACE", workspace)
    result = test_rig.run_syntax_check()

    assert result["passed"] is False
    assert "bad.py" in result["error"]


def test_run_structlog_lint_flags_known_antipatterns(tmp_path, monkeypatch):
    workspace = tmp_path / "workspace"
    src = workspace / "src"
    src.mkdir(parents=True)
    (src / "prime.py").write_text(
        """
import structlog

log = structlog.get_logger()

def f(n):
    log.info("event", event="dup")
    log.info("gen %02d", n=n)
""".lstrip()
    )

    monkeypatch.setattr(test_rig, "WORKSPACE", workspace)
    result = test_rig.run_structlog_lint()

    assert result["passed"] is False
    assert "event" in result["error"]
    assert "printf formatting" in result["error"]


def test_run_baseline_check_all_malformed_contracts_returns_none(tmp_path, monkeypatch):
    battery_path = tmp_path / "battery.json"
    battery_path.write_text(
        json.dumps(
            {
                "generation": 7,
                "contracts": [
                    {"name": "missing-path", "expect": {"status": 200}},
                    {"path": "/health", "expect": {"status": 200}},
                ],
            }
        )
    )

    monkeypatch.setattr(test_rig, "BASELINE_PATH", battery_path)
    result = test_rig.run_baseline_check("http://localhost:8401/health", generation=8)

    assert result is None
