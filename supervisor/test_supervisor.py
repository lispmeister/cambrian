"""Unit tests for Supervisor HTTP endpoints and generation record store."""

import json
import time
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient

# Point CAMBRIAN_ARTIFACTS_ROOT at a temp dir before importing supervisor modules
# (each test patches this via fixture)


@pytest.fixture
def artifacts_root(tmp_path: Path) -> Path:
    root = tmp_path / "artifacts"
    root.mkdir()
    return root


@pytest.fixture(autouse=True)
def set_artifacts_root(artifacts_root: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CAMBRIAN_ARTIFACTS_ROOT", str(artifacts_root))
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")


# ---------------------------------------------------------------------------
# generations.py tests
# ---------------------------------------------------------------------------


class TestGenerations:
    def test_load_all_empty(self, artifacts_root: Path) -> None:
        from supervisor import generations

        records = generations.load_all()
        assert records == []

    def test_load_all_missing_file(self, artifacts_root: Path) -> None:
        from supervisor import generations

        # File doesn't exist — should return empty list, not raise
        assert generations.load_all() == []

    def test_append_and_load(self, artifacts_root: Path) -> None:
        from supervisor import generations

        record: dict[str, Any] = {
            "generation": 1,
            "outcome": "in_progress",
        }
        generations.append(record)
        loaded = generations.load_all()
        assert len(loaded) == 1
        assert loaded[0]["generation"] == 1

    def test_get_existing(self, artifacts_root: Path) -> None:
        from supervisor import generations

        generations.append({"generation": 1, "outcome": "in_progress"})
        generations.append({"generation": 2, "outcome": "in_progress"})
        rec = generations.get(2)
        assert rec is not None
        assert rec["generation"] == 2

    def test_get_missing(self, artifacts_root: Path) -> None:
        from supervisor import generations

        assert generations.get(999) is None

    def test_update_non_terminal(self, artifacts_root: Path) -> None:
        from supervisor import generations

        generations.append({"generation": 1, "outcome": "in_progress"})
        generations.update(1, {"outcome": "tested"})
        rec = generations.get(1)
        assert rec is not None
        assert rec["outcome"] == "tested"

    def test_update_terminal_is_rejected(self, artifacts_root: Path) -> None:
        from supervisor import generations

        generations.append({"generation": 1, "outcome": "promoted"})
        # Attempt to change a promoted record — should be silently rejected
        generations.update(1, {"outcome": "failed"})
        rec = generations.get(1)
        assert rec is not None
        assert rec["outcome"] == "promoted"  # unchanged

    def test_update_timeout_is_terminal(self, artifacts_root: Path) -> None:
        from supervisor import generations

        generations.append({"generation": 1, "outcome": "timeout"})
        generations.update(1, {"outcome": "tested"})
        rec = generations.get(1)
        assert rec is not None
        assert rec["outcome"] == "timeout"  # unchanged

    def test_update_failed_is_terminal(self, artifacts_root: Path) -> None:
        from supervisor import generations

        generations.append({"generation": 1, "outcome": "failed"})
        generations.update(1, {"outcome": "promoted"})
        rec = generations.get(1)
        assert rec is not None
        assert rec["outcome"] == "failed"  # unchanged

    def test_update_sets_completed_timestamp(self, artifacts_root: Path) -> None:
        from supervisor import generations

        generations.append({"generation": 1, "outcome": "in_progress"})
        generations.update(1, {"outcome": "tested"})
        rec = generations.get(1)
        assert rec is not None
        assert "completed" in rec

    def test_update_nonexistent_generation_is_noop(self, artifacts_root: Path) -> None:
        from supervisor import generations

        generations.append({"generation": 1, "outcome": "in_progress"})
        path = artifacts_root / "generations.json"
        mtime_before = path.stat().st_mtime
        generations.update(999, {"outcome": "tested"})
        # File must not be written when no record matches
        assert path.stat().st_mtime == mtime_before
        assert generations.get(1) is not None  # existing record untouched


# ---------------------------------------------------------------------------
# supervisor.py HTTP endpoint tests
# ---------------------------------------------------------------------------


@pytest.fixture
def mock_git_ops(tmp_path: Path) -> Any:
    """Patch git_ops so tests don't need a real git repo."""
    with patch("supervisor.supervisor.git_ops") as mock:
        mock.artifacts_root.return_value = str(tmp_path / "artifacts")
        mock.ensure_on_main = AsyncMock()
        mock.git = AsyncMock()
        mock.promote = AsyncMock(return_value="gen-1")
        mock.rollback = AsyncMock(return_value="gen-1-failed")
        mock.GitError = Exception
        yield mock


@pytest.fixture
def app(mock_git_ops: Any) -> web.Application:
    import supervisor.supervisor as sup

    sup._start_time = time.time()
    sup._status = "idle"
    sup._current_generation = None
    return sup.make_app()


@pytest.fixture
async def client(app: web.Application, aiohttp_client: Any) -> TestClient:
    return await aiohttp_client(app)


@pytest.mark.asyncio
async def test_get_root_returns_html(client: TestClient) -> None:
    resp = await client.get("/")
    assert resp.status == 200
    text = await resp.text()
    assert "Cambrian Supervisor" in text


@pytest.mark.asyncio
async def test_stats_idle_no_generations(
    client: TestClient,
    artifacts_root: Path,
) -> None:
    resp = await client.get("/stats")
    assert resp.status == 200
    data = await resp.json()
    assert data["generation"] == 0
    assert data["status"] == "idle"
    assert isinstance(data["uptime"], int)
    assert data["uptime"] >= 0


@pytest.mark.asyncio
async def test_stats_returns_latest_generation(
    client: TestClient,
    artifacts_root: Path,
) -> None:
    from supervisor import generations

    generations.append({"generation": 3, "outcome": "promoted"})
    resp = await client.get("/stats")
    data = await resp.json()
    assert data["generation"] == 3


@pytest.mark.asyncio
async def test_stats_status_field_is_supervisor_state(
    client: TestClient,
    artifacts_root: Path,
) -> None:
    import supervisor.supervisor as sup

    sup._status = "idle"
    resp = await client.get("/stats")
    data = await resp.json()
    # Status must be supervisor operational state, NOT a generation outcome
    assert data["status"] == "idle"
    assert data["status"] != "promoted"


@pytest.mark.asyncio
async def test_versions_empty(client: TestClient) -> None:
    resp = await client.get("/versions")
    assert resp.status == 200
    data = await resp.json()
    assert data == []


@pytest.mark.asyncio
async def test_versions_returns_all_records(
    client: TestClient,
    artifacts_root: Path,
) -> None:
    from supervisor import generations

    generations.append({"generation": 1, "outcome": "promoted"})
    generations.append({"generation": 2, "outcome": "in_progress"})
    resp = await client.get("/versions")
    data = await resp.json()
    assert len(data) == 2


@pytest.mark.asyncio
async def test_debug_state_returns_json(client: TestClient) -> None:
    resp = await client.get("/debug/state")
    assert resp.status == 200
    data = await resp.json()
    assert "status" in data
    assert "uptime" in data
    assert "config" in data


@pytest.mark.asyncio
async def test_spawn_missing_artifact_path(
    client: TestClient,
    artifacts_root: Path,
) -> None:
    resp = await client.post(
        "/spawn",
        json={
            "generation": 1,
            "artifact-path": "nonexistent/path",
            "spec-hash": "sha256:" + "a" * 64,
        },
    )
    assert resp.status == 400
    data = await resp.json()
    assert data["ok"] is False
    assert "not exist" in data["error"]


@pytest.mark.asyncio
async def test_spawn_path_traversal_rejected(
    client: TestClient,
    artifacts_root: Path,
) -> None:
    resp = await client.post(
        "/spawn",
        json={
            "generation": 1,
            "artifact-path": "../../etc/passwd",
            "spec-hash": "sha256:" + "a" * 64,
        },
    )
    assert resp.status == 400
    data = await resp.json()
    assert data["ok"] is False
    assert "escapes" in data["error"]


@pytest.mark.asyncio
async def test_spawn_missing_docker_image(
    client: TestClient,
    artifacts_root: Path,
) -> None:
    # Create artifact dir inside artifacts_root and use a relative path
    art_dir = artifacts_root / "art1"
    art_dir.mkdir()

    with patch("supervisor.supervisor.aiodocker.Docker") as mock_docker_cls:
        mock_docker = AsyncMock()
        # Return empty list — image not found
        mock_docker.images.list = AsyncMock(return_value=[])
        mock_docker.close = AsyncMock()
        mock_docker_cls.return_value = mock_docker

        resp = await client.post(
            "/spawn",
            json={
                "generation": 1,
                "artifact-path": "art1",
                "spec-hash": "sha256:" + "a" * 64,
            },
        )
    assert resp.status == 400
    data = await resp.json()
    assert data["ok"] is False
    assert "Docker image" in data["error"]


@pytest.mark.asyncio
async def test_promote_generation_not_found(client: TestClient) -> None:
    resp = await client.post("/promote", json={"generation": 99})
    assert resp.status == 404
    data = await resp.json()
    assert data["ok"] is False


@pytest.mark.asyncio
async def test_promote_success(
    client: TestClient,
    artifacts_root: Path,
    mock_git_ops: Any,
) -> None:
    from supervisor import generations

    generations.append(
        {
            "generation": 1,
            "outcome": "tested",
            "artifact-ref": "gen-1",
        }
    )
    resp = await client.post("/promote", json={"generation": 1})
    assert resp.status == 200
    data = await resp.json()
    assert data["ok"] is True
    assert data["generation"] == 1


@pytest.mark.asyncio
async def test_rollback_git_error(
    client: TestClient,
    mock_git_ops: Any,
) -> None:
    mock_git_ops.rollback = AsyncMock(side_effect=Exception("git error"))
    mock_git_ops.GitError = Exception
    resp = await client.post("/rollback", json={"generation": 1})
    assert resp.status == 500
    data = await resp.json()
    assert data["ok"] is False


@pytest.mark.asyncio
async def test_spawn_includes_campaign_id(
    client: TestClient,
    artifacts_root: Path,
) -> None:
    art_dir = artifacts_root / "art_campaign"
    art_dir.mkdir()

    with (
        patch("supervisor.supervisor.aiodocker.Docker") as mock_docker_cls,
        patch("supervisor.supervisor.asyncio.create_task", return_value=None),
    ):
        mock_docker = AsyncMock()
        # Return the image so the image-found check passes
        mock_docker.images.list = AsyncMock(return_value=[{"RepoTags": ["cambrian-base:latest"]}])
        mock_docker.close = AsyncMock()
        mock_docker_cls.return_value = mock_docker

        resp = await client.post(
            "/spawn",
            json={
                "generation": 1,
                "artifact-path": "art_campaign",
                "spec-hash": "sha256:" + "a" * 64,
                "campaign-id": "campaign-abc12345",
            },
        )

    assert resp.status == 200
    from supervisor import generations

    rec = generations.get(1)
    assert rec is not None
    assert rec.get("campaign-id") == "campaign-abc12345"


# ---------------------------------------------------------------------------
# run_test_rig tests
# ---------------------------------------------------------------------------


def _make_mock_docker() -> tuple[Any, Any]:
    """Return (mock_docker_cls, mock_docker) with a pre-configured container mock."""
    mock_docker_cls = MagicMock()
    mock_docker = AsyncMock()
    mock_container = AsyncMock()
    mock_docker.containers.create_or_replace = AsyncMock(return_value=mock_container)
    mock_container.start = AsyncMock()
    mock_container.wait = AsyncMock(return_value={"StatusCode": 0})
    mock_container.kill = AsyncMock()
    mock_container.delete = AsyncMock()
    mock_docker.close = AsyncMock()
    mock_docker_cls.return_value = mock_docker
    return mock_docker_cls, mock_docker


@pytest.mark.asyncio
async def test_run_test_rig_happy_path(artifacts_root: Path) -> None:
    """Container exits normally with a viable report — record updated to tested."""
    from supervisor import generations
    from supervisor import supervisor as sup

    generations.append({"generation": 1, "outcome": "in_progress"})
    artifact_path = artifacts_root / "gen-1"
    artifact_path.mkdir()

    # Write a viable viability report into the artifact dir
    viability = {
        "generation": 1,
        "status": "viable",
        "failure_stage": "none",
        "checks": {
            "manifest": {"passed": True},
            "build": {"passed": True, "duration_ms": 100},
            "test": {"passed": True, "tests_run": 5, "tests_passed": 5, "duration_ms": 200},
            "start": {"passed": True, "duration_ms": 50},
            "health": {"passed": True, "duration_ms": 10},
        },
        "completed_at": "2026-03-30T00:00:00Z",
    }
    (artifact_path / "viability-report.json").write_text(json.dumps(viability))

    mock_docker_cls, _ = _make_mock_docker()
    with patch("supervisor.supervisor.aiodocker.Docker", mock_docker_cls):
        await sup.run_test_rig(1, artifact_path, "lab-gen-1")

    rec = generations.get(1)
    assert rec is not None
    assert rec["outcome"] == "tested"
    assert rec["viability"]["status"] == "viable"
    assert sup._status == "idle"


@pytest.mark.asyncio
async def test_run_test_rig_container_timeout(artifacts_root: Path) -> None:
    """Container times out — record updated to 'timeout', container killed."""
    from supervisor import generations
    from supervisor import supervisor as sup

    generations.append({"generation": 2, "outcome": "in_progress"})
    artifact_path = artifacts_root / "gen-2"
    artifact_path.mkdir()

    mock_docker_cls, mock_docker = _make_mock_docker()
    mock_container = mock_docker.containers.create_or_replace.return_value
    # Make container.wait() raise TimeoutError to simulate a timeout
    mock_container.wait = AsyncMock(side_effect=TimeoutError)

    with patch("supervisor.supervisor.aiodocker.Docker", mock_docker_cls):
        await sup.run_test_rig(2, artifact_path, "lab-gen-2")

    rec = generations.get(2)
    assert rec is not None
    assert rec["outcome"] == "timeout"
    mock_container.kill.assert_called_once()
    assert sup._status == "idle"


@pytest.mark.asyncio
async def test_run_test_rig_missing_viability_report(artifacts_root: Path) -> None:
    """Container exits but writes no viability report — fallback non-viable report generated."""
    from supervisor import generations
    from supervisor import supervisor as sup

    generations.append({"generation": 3, "outcome": "in_progress"})
    artifact_path = artifacts_root / "gen-3"
    artifact_path.mkdir()
    # No viability-report.json written

    mock_docker_cls, _ = _make_mock_docker()
    with patch("supervisor.supervisor.aiodocker.Docker", mock_docker_cls):
        await sup.run_test_rig(3, artifact_path, "lab-gen-3")

    rec = generations.get(3)
    assert rec is not None
    assert rec["outcome"] == "tested"
    assert rec["viability"]["status"] == "non-viable"
    assert "crashed or exited" in rec["viability"]["diagnostics"]["summary"]


@pytest.mark.asyncio
async def test_run_test_rig_docker_error(artifacts_root: Path) -> None:
    """Container creation fails — record updated with infrastructure error viability."""
    from supervisor import generations
    from supervisor import supervisor as sup

    generations.append({"generation": 4, "outcome": "in_progress"})
    artifact_path = artifacts_root / "gen-4"
    artifact_path.mkdir()

    mock_docker_cls = MagicMock()
    mock_docker = AsyncMock()
    mock_docker.containers.create_or_replace = AsyncMock(side_effect=Exception("docker boom"))
    mock_docker.close = AsyncMock()
    mock_docker_cls.return_value = mock_docker

    with patch("supervisor.supervisor.aiodocker.Docker", mock_docker_cls):
        await sup.run_test_rig(4, artifact_path, "lab-gen-4")

    rec = generations.get(4)
    assert rec is not None
    assert rec["outcome"] == "tested"
    assert "infrastructure error" in rec["viability"]["diagnostics"]["summary"]
    assert "docker boom" in rec["viability"]["diagnostics"]["summary"]


@pytest.mark.asyncio
async def test_run_test_rig_cleans_up_cache_dirs(artifacts_root: Path) -> None:
    """Cache dirs created by the container are cleaned up after run."""
    from supervisor import generations
    from supervisor import supervisor as sup

    generations.append({"generation": 5, "outcome": "in_progress"})
    artifact_path = artifacts_root / "gen-5"
    artifact_path.mkdir()

    # Simulate cache dirs that the container would have left behind
    pycache = artifact_path / "src" / "__pycache__"
    pycache.mkdir(parents=True)
    pytest_cache = artifact_path / ".pytest_cache"
    pytest_cache.mkdir()

    mock_docker_cls, _ = _make_mock_docker()
    with patch("supervisor.supervisor.aiodocker.Docker", mock_docker_cls):
        await sup.run_test_rig(5, artifact_path, "lab-gen-5")

    assert not pycache.exists(), "__pycache__ should be removed"
    assert not pytest_cache.exists(), ".pytest_cache should be removed"
