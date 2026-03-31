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
async def test_spawn_missing_spec_hash_rejected(
    client: TestClient,
    artifacts_root: Path,
) -> None:
    resp = await client.post(
        "/spawn",
        json={"generation": 1, "artifact-path": "gen-1"},
    )
    assert resp.status == 400
    data = await resp.json()
    assert data["ok"] is False
    assert "spec-hash" in data["error"]


@pytest.mark.asyncio
async def test_spawn_empty_spec_hash_rejected(
    client: TestClient,
    artifacts_root: Path,
) -> None:
    resp = await client.post(
        "/spawn",
        json={"generation": 1, "artifact-path": "gen-1", "spec-hash": ""},
    )
    assert resp.status == 400
    data = await resp.json()
    assert data["ok"] is False
    assert "spec-hash" in data["error"]


@pytest.mark.asyncio
async def test_spawn_generation_zero_rejected(
    client: TestClient,
    artifacts_root: Path,
) -> None:
    """generation=0 is reserved for hand-crafted test artifacts — LLM-generated must be >= 1."""
    resp = await client.post(
        "/spawn",
        json={"generation": 0, "artifact-path": "gen-0", "spec-hash": "sha256:" + "a" * 64},
    )
    assert resp.status == 400
    data = await resp.json()
    assert data["ok"] is False
    assert "generation" in data["error"]


@pytest.mark.asyncio
async def test_spawn_generation_negative_rejected(
    client: TestClient,
    artifacts_root: Path,
) -> None:
    resp = await client.post(
        "/spawn",
        json={"generation": -1, "artifact-path": "gen-neg", "spec-hash": "sha256:" + "a" * 64},
    )
    assert resp.status == 400
    data = await resp.json()
    assert data["ok"] is False


@pytest.mark.asyncio
async def test_spawn_duplicate_generation_rejected(
    client: TestClient,
    artifacts_root: Path,
) -> None:
    """Second spawn for same generation number returns 409 Conflict."""
    from supervisor import generations as gen_store
    from supervisor import git_ops

    # Inject an existing record for generation 7
    gen_store.append({"generation": 7, "outcome": "in_progress"})
    try:
        resp = await client.post(
            "/spawn",
            json={"generation": 7, "artifact-path": "gen-7", "spec-hash": "sha256:" + "a" * 64},
        )
        assert resp.status == 409
        data = await resp.json()
        assert data["ok"] is False
        assert "7" in data["error"]
    finally:
        # Clean up injected record
        all_records = gen_store.load_all()
        remaining = [r for r in all_records if r.get("generation") != 7]
        gen_file = Path(git_ops.artifacts_root()) / "generations.json"
        if gen_file.exists():
            gen_file.write_text(json.dumps(remaining))


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
    output_dir = artifacts_root / "output-gen-1"
    output_dir.mkdir()
    (output_dir / "viability-report.json").write_text(json.dumps(viability))

    mock_docker_cls, _ = _make_mock_docker()
    with patch("supervisor.supervisor.tempfile.mkdtemp", return_value=str(output_dir)):
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


# ---------------------------------------------------------------------------
# Campaign runner tests
# ---------------------------------------------------------------------------


def _make_record(
    generation: int,
    viable: bool,
    failure_stage: str = "none",
    fitness: dict | None = None,
) -> dict:
    """Build a minimal generation record for campaign tests."""
    _all_stages = ["manifest", "build", "test", "start", "health"]
    return {
        "generation": generation,
        "outcome": "promoted" if viable else "failed",
        "viability": {
            "status": "viable" if viable else "non-viable",
            "failure_stage": failure_stage,
            "fitness": fitness or {"stages_completed": _all_stages},
        },
    }


class TestComputeCampaignSummary:
    def test_empty_records(self) -> None:
        from supervisor.campaign import compute_campaign_summary

        summary = compute_campaign_summary([])
        assert summary["viability_rate"] == 0.0
        assert summary["generation_count"] == 0
        assert summary["fitness_mean"] == {}

    def test_all_viable(self) -> None:
        from supervisor.campaign import compute_campaign_summary

        records = [_make_record(i, viable=True) for i in range(1, 6)]
        summary = compute_campaign_summary(records)
        assert summary["viability_rate"] == 1.0
        assert summary["generation_count"] == 5
        assert summary["failure_distribution"] == {"none": 5}

    def test_all_non_viable(self) -> None:
        from supervisor.campaign import compute_campaign_summary

        records = [_make_record(i, viable=False, failure_stage="build") for i in range(1, 4)]
        summary = compute_campaign_summary(records)
        assert summary["viability_rate"] == 0.0
        assert summary["failure_distribution"] == {"build": 3}

    def test_mixed_viability_rate(self) -> None:
        from supervisor.campaign import compute_campaign_summary

        records = [
            _make_record(1, viable=True),
            _make_record(2, viable=False, failure_stage="test"),
            _make_record(3, viable=True),
            _make_record(4, viable=False, failure_stage="build"),
        ]
        summary = compute_campaign_summary(records)
        assert summary["viability_rate"] == pytest.approx(0.5, rel=1e-4)
        assert summary["failure_distribution"] == {"none": 2, "test": 1, "build": 1}

    def test_fitness_mean_computed(self) -> None:
        from supervisor.campaign import compute_campaign_summary

        records = [
            _make_record(1, viable=True, fitness={"total_duration_ms": 1000, "test_count": 5,
                                                  "stages_completed": []}),
            _make_record(2, viable=True, fitness={"total_duration_ms": 2000, "test_count": 3,
                                                  "stages_completed": []}),
        ]
        summary = compute_campaign_summary(records)
        assert summary["fitness_mean"]["total_duration_ms"] == pytest.approx(1500.0, rel=1e-4)
        assert summary["fitness_mean"]["test_count"] == pytest.approx(4.0, rel=1e-4)

    def test_fitness_trend_positive(self) -> None:
        from supervisor.campaign import compute_campaign_summary

        # Improving: [0, 0, 1, 1, 1] — slope should be positive
        records = [
            _make_record(1, viable=False),
            _make_record(2, viable=False),
            _make_record(3, viable=True),
            _make_record(4, viable=True),
            _make_record(5, viable=True),
        ]
        summary = compute_campaign_summary(records)
        assert summary["fitness_trend"] > 0

    def test_fitness_trend_negative(self) -> None:
        from supervisor.campaign import compute_campaign_summary

        # Declining: [1, 1, 0, 0, 0]
        records = [
            _make_record(1, viable=True),
            _make_record(2, viable=True),
            _make_record(3, viable=False),
            _make_record(4, viable=False),
            _make_record(5, viable=False),
        ]
        summary = compute_campaign_summary(records)
        assert summary["fitness_trend"] < 0

    def test_fitness_trend_flat_constant(self) -> None:
        from supervisor.campaign import compute_campaign_summary

        # Constant viability (all non-viable) → slope must be exactly 0.0
        records = [_make_record(i, viable=False) for i in range(1, 6)]
        summary = compute_campaign_summary(records)
        assert summary["fitness_trend"] == pytest.approx(0.0, abs=1e-9)

    def test_stages_completed_distribution(self) -> None:
        from supervisor.campaign import compute_campaign_summary

        all_stages = ["manifest", "build", "test", "start", "health"]
        records = [
            _make_record(1, viable=True, fitness={"stages_completed": all_stages}),
            _make_record(2, viable=False, failure_stage="build",
                         fitness={"stages_completed": ["manifest", "build"]}),
            _make_record(3, viable=False, failure_stage="test",
                         fitness={"stages_completed": ["manifest", "build", "test"]}),
        ]
        summary = compute_campaign_summary(records)
        dist = summary["stages_completed_distribution"]
        assert dist["health"] == 1
        assert dist["build"] == 1
        assert dist["test"] == 1

    def test_stages_completed_absent_fitness(self) -> None:
        """Infrastructure error records have no fitness key — should default to 'none'."""
        from supervisor.campaign import compute_campaign_summary

        # Simulate _make_error_viability output (no fitness object)
        record = {
            "generation": 1,
            "outcome": "tested",
            "viability": {
                "status": "non-viable",
                "failure_stage": "health",
                "checks": {},
                "diagnostics": {"summary": "container crashed"},
            },
        }
        summary = compute_campaign_summary([record])
        assert summary["stages_completed_distribution"] == {"none": 1}


class TestLinearSlope:
    def test_slope_of_constant_is_zero(self) -> None:
        from supervisor.campaign import _linear_slope

        assert _linear_slope([1, 1, 1, 1]) == pytest.approx(0.0, abs=1e-9)

    def test_slope_of_increasing(self) -> None:
        from supervisor.campaign import _linear_slope

        # [0, 1, 2, 3] — perfect slope of 1.0
        assert _linear_slope([0, 1, 2, 3]) == pytest.approx(1.0, rel=1e-6)

    def test_slope_of_single_value(self) -> None:
        from supervisor.campaign import _linear_slope

        assert _linear_slope([42]) == 0.0

    def test_slope_of_empty(self) -> None:
        from supervisor.campaign import _linear_slope

        assert _linear_slope([]) == 0.0


# ---------------------------------------------------------------------------
# Spec diff tests
# ---------------------------------------------------------------------------

_SPEC_A = """\
## Preamble section
some text here

<!-- BEGIN FROZEN: identity-anchor -->
## Frozen Section
this must not change
<!-- END FROZEN: identity-anchor -->

## Implementation
build this thing

## Testing
test this thing
"""

_SPEC_B = """\
## Preamble section
some text here

<!-- BEGIN FROZEN: identity-anchor -->
## Frozen Section
this must not change
<!-- END FROZEN: identity-anchor -->

## Implementation
build this thing differently
with more lines

## Testing
test this thing
"""


class TestParseSections:
    def test_preamble_captured(self) -> None:
        from supervisor.spec_diff import parse_sections

        spec = "line before\n## Section A\nbody\n"
        sections = parse_sections(spec)
        assert "__preamble__" in sections
        assert "line before\n" in sections["__preamble__"]

    def test_sections_split_correctly(self) -> None:
        from supervisor.spec_diff import parse_sections

        sections = parse_sections(_SPEC_A)
        assert "Implementation" in sections
        assert "Testing" in sections
        assert "build this thing" in sections["Implementation"]

    def test_frozen_section_names_detected(self) -> None:
        from supervisor.spec_diff import frozen_section_names

        frozen = frozen_section_names(_SPEC_A)
        assert "Frozen Section" in frozen
        assert "Implementation" not in frozen


class TestDiffSpec:
    def test_no_change_produces_empty_diff(self) -> None:
        from supervisor.spec_diff import diff_spec

        d = diff_spec(_SPEC_A, _SPEC_A)
        assert d.total_lines_added == 0
        assert d.total_lines_removed == 0
        assert d.sections_changed == []

    def test_changed_section_detected(self) -> None:
        from supervisor.spec_diff import diff_spec

        d = diff_spec(_SPEC_A, _SPEC_B)
        changed_names = [sc.section_name for sc in d.sections_changed]
        assert "Implementation" in changed_names

    def test_unchanged_sections_listed(self) -> None:
        from supervisor.spec_diff import diff_spec

        d = diff_spec(_SPEC_A, _SPEC_B)
        assert "Testing" in d.sections_unchanged

    def test_frozen_flag_set_on_frozen_section(self) -> None:
        from supervisor.spec_diff import diff_spec

        spec_b_frozen_modified = _SPEC_A.replace(
            "this must not change", "this has changed!"
        )
        d = diff_spec(_SPEC_A, spec_b_frozen_modified)
        frozen_changed = [sc for sc in d.sections_changed if sc.is_frozen]
        assert any(sc.section_name == "Frozen Section" for sc in frozen_changed)

    def test_line_counts_correct(self) -> None:
        from supervisor.spec_diff import diff_spec

        d = diff_spec(_SPEC_A, _SPEC_B)
        # _SPEC_B adds "with more lines" and changes one line in Implementation
        assert d.total_lines_added >= 1
        assert d.total_lines_removed >= 1

    def test_hashes_differ(self) -> None:
        from supervisor.spec_diff import diff_spec

        d = diff_spec(_SPEC_A, _SPEC_B)
        assert d.parent_hash != d.child_hash
        assert d.parent_hash.startswith("sha256:")

    def test_unified_diff_non_empty_on_change(self) -> None:
        from supervisor.spec_diff import diff_spec

        d = diff_spec(_SPEC_A, _SPEC_B)
        assert "@@" in d.unified_diff


class TestAttributeFitnessDelta:
    def test_viability_delta_computed(self) -> None:
        from supervisor.spec_diff import attribute_fitness_delta, diff_spec

        d = diff_spec(_SPEC_A, _SPEC_B)
        before = {"viability_rate": 0.4, "fitness_mean": {"total_duration_ms": 5000.0}}
        after = {"viability_rate": 0.8, "fitness_mean": {"total_duration_ms": 4000.0}}
        attr = attribute_fitness_delta(d, before, after)
        assert attr["viability_rate_delta"] == pytest.approx(0.4, rel=1e-4)
        assert attr["fitness_mean_deltas"]["total_duration_ms"] == pytest.approx(-1000.0, rel=1e-4)

    def test_sections_changed_listed(self) -> None:
        from supervisor.spec_diff import attribute_fitness_delta, diff_spec

        d = diff_spec(_SPEC_A, _SPEC_B)
        attr = attribute_fitness_delta(d, {}, {})
        assert "Implementation" in attr["sections_changed"]

    def test_entanglement_score_between_0_and_1(self) -> None:
        from supervisor.spec_diff import attribute_fitness_delta, diff_spec

        d = diff_spec(_SPEC_A, _SPEC_B)
        attr = attribute_fitness_delta(d, {}, {})
        assert 0.0 <= attr["entanglement_score"] <= 1.0

    def test_no_change_zero_entanglement(self) -> None:
        from supervisor.spec_diff import attribute_fitness_delta, diff_spec

        d = diff_spec(_SPEC_A, _SPEC_A)
        attr = attribute_fitness_delta(d, {}, {})
        assert attr["entanglement_score"] == 0.0
        assert attr["sections_changed"] == []


class TestApplyRevertDiff:
    def test_apply_produces_modified_text(self) -> None:
        from supervisor.spec_diff import apply_spec_diff, diff_spec

        d = diff_spec(_SPEC_A, _SPEC_B)
        result = apply_spec_diff(_SPEC_A, d.unified_diff)
        assert "build this thing differently" in result
        assert "with more lines" in result

    def test_revert_recovers_original(self) -> None:
        from supervisor.spec_diff import diff_spec, revert_spec_diff

        d = diff_spec(_SPEC_A, _SPEC_B)
        result = revert_spec_diff(_SPEC_B, d.unified_diff)
        assert "build this thing differently" not in result
        assert "build this thing" in result

    def test_apply_empty_diff_unchanged(self) -> None:
        from supervisor.spec_diff import apply_spec_diff

        result = apply_spec_diff(_SPEC_A, "")
        assert result == _SPEC_A

    def test_roundtrip(self) -> None:
        from supervisor.spec_diff import apply_spec_diff, diff_spec, revert_spec_diff

        d = diff_spec(_SPEC_A, _SPEC_B)
        applied = apply_spec_diff(_SPEC_A, d.unified_diff)
        reverted = revert_spec_diff(applied, d.unified_diff)
        # Reverted should match the original (modulo possible trailing newlines)
        assert reverted.strip() == _SPEC_A.strip()


# ---------------------------------------------------------------------------
# Spec grammar tests
# ---------------------------------------------------------------------------


def _make_valid_spec(extra_sections: str = "") -> str:
    """Build a minimal valid spec for grammar tests.

    Invariants lives inside the FROZEN block only — not duplicated in the
    section list below — to avoid triggering the duplicate_heading rule.
    """
    non_frozen = [
        "What This Document Is", "Glossary", "Problem Statement",
        "Goals", "Non-Goals", "Design Principles", "What Prime Does", "Contracts",
        "The Generation Loop", "Failure Handling", "LLM Integration",
        "Implementation Requirements", "Acceptance Criteria", "Verification Layers",
    ]
    sections = "\n".join(f"## {s}\nsome content\n" for s in non_frozen)
    return (
        "<!-- BEGIN FROZEN: identity-anchor -->\n"
        "## Invariants\nfrozen content here\n"
        "<!-- END FROZEN: identity-anchor -->\n\n"
        + sections
        + "\nport 8401\nThis server MUST respond.\n"
        + extra_sections
    )


class TestSpecGrammarValidation:
    def test_valid_spec_no_violations(self) -> None:
        from supervisor.spec_grammar import validate_spec

        spec = _make_valid_spec()
        violations = validate_spec(spec)
        fatal = [v for v in violations if v.fatal]
        assert fatal == [], f"Unexpected fatal violations: {fatal}"

    def test_missing_required_section(self) -> None:
        from supervisor.spec_grammar import validate_spec

        # Remove "Goals" section
        spec = _make_valid_spec().replace("## Goals\nsome content\n", "")
        violations = validate_spec(spec)
        rules = [v.rule for v in violations]
        assert "missing_required_section" in rules

    def test_missing_port(self) -> None:
        from supervisor.spec_grammar import validate_spec

        spec = _make_valid_spec().replace("port 8401", "port XXXX")
        violations = validate_spec(spec)
        rules = [v.rule for v in violations]
        assert "missing_port" in rules

    def test_duplicate_heading(self) -> None:
        from supervisor.spec_grammar import validate_spec

        spec = _make_valid_spec() + "\n## Goals\nduplicate\n"
        violations = validate_spec(spec)
        rules = [v.rule for v in violations]
        assert "duplicate_heading" in rules

    def test_unpaired_frozen_marker(self) -> None:
        from supervisor.spec_grammar import validate_spec

        spec = _make_valid_spec().replace("<!-- END FROZEN: identity-anchor -->", "")
        violations = validate_spec(spec)
        rules = [v.rule for v in violations]
        assert "unpaired_frozen_markers" in rules


class TestSpecGrammarMutation:
    def test_valid_mutation_no_violations(self) -> None:
        from supervisor.spec_grammar import validate_mutation

        parent = _make_valid_spec()
        child = parent.replace(
            "The Generation Loop\nsome content", "The Generation Loop\nupdated content"
        )
        violations = validate_mutation(parent, child)
        fatal = [v for v in violations if v.fatal]
        assert fatal == []

    def test_frozen_block_modified_rejected(self) -> None:
        from supervisor.spec_grammar import validate_mutation

        parent = _make_valid_spec()
        child = parent.replace("frozen content here", "HACKED frozen content")
        violations = validate_mutation(parent, child)
        rules = [v.rule for v in violations]
        assert "frozen_block_modified" in rules

    def test_frozen_block_removed_rejected(self) -> None:
        from supervisor.spec_grammar import validate_mutation

        parent = _make_valid_spec()
        frozen_block = (
            "<!-- BEGIN FROZEN: identity-anchor -->\n"
            "## Invariants\nfrozen content here\n"
            "<!-- END FROZEN: identity-anchor -->\n"
        )
        child = parent.replace(frozen_block, "")
        violations = validate_mutation(parent, child)
        rules = [v.rule for v in violations]
        assert "frozen_block_removed" in rules

    def test_is_valid_mutation_helper(self) -> None:
        from supervisor.spec_grammar import is_valid_mutation

        parent = _make_valid_spec()
        child = parent.replace("LLM Integration\nsome content", "LLM Integration\nbetter content")
        assert is_valid_mutation(parent, child)

    def test_evolvable_sections_excludes_frozen(self) -> None:
        from supervisor.spec_grammar import FROZEN_SECTION_NAMES, evolvable_sections

        spec = _make_valid_spec()
        sections = evolvable_sections(spec)
        for frozen_name in FROZEN_SECTION_NAMES:
            assert frozen_name not in sections


# ---------------------------------------------------------------------------
# Spec mutator tests (cambrian-7cc)
# ---------------------------------------------------------------------------


class TestSpecMutator:
    def test_propose_target_section_build_failure(self) -> None:
        from supervisor.spec_mutator import propose_target_section

        summary = {
            "viability_rate": 0.0,
            "failure_distribution": {"build": 4, "none": 1},
        }
        evolvable = ["Implementation Requirements", "LLM Integration", "Contracts"]
        target = propose_target_section(summary, evolvable)
        assert target == "Implementation Requirements"

    def test_propose_target_section_test_failure(self) -> None:
        from supervisor.spec_mutator import propose_target_section

        summary = {
            "viability_rate": 0.2,
            "failure_distribution": {"test": 3, "none": 2},
        }
        evolvable = ["Acceptance Criteria", "LLM Integration"]
        target = propose_target_section(summary, evolvable)
        assert target == "Acceptance Criteria"

    def test_propose_target_section_no_failures(self) -> None:
        from supervisor.spec_mutator import propose_target_section

        summary = {"viability_rate": 1.0, "failure_distribution": {"none": 5}}
        evolvable = ["Goals", "LLM Integration"]
        target = propose_target_section(summary, evolvable)
        assert target is None

    def test_propose_target_section_empty_evolvable(self) -> None:
        from supervisor.spec_mutator import propose_target_section

        summary = {"viability_rate": 0.0, "failure_distribution": {"build": 3}}
        target = propose_target_section(summary, [])
        assert target is None

    def test_extract_spec_strips_code_fence(self) -> None:
        from supervisor.spec_mutator import _extract_spec_from_response

        response = "```markdown\n## Section A\ncontent\n```"
        result = _extract_spec_from_response(response)
        assert result is not None
        assert "## Section A" in result
        assert "```" not in result

    def test_extract_spec_no_fence(self) -> None:
        from supervisor.spec_mutator import _extract_spec_from_response

        response = "## Section A\ncontent here"
        result = _extract_spec_from_response(response)
        assert result == response

    def test_extract_spec_empty_returns_none(self) -> None:
        from supervisor.spec_mutator import _extract_spec_from_response

        assert _extract_spec_from_response("") is None
        assert _extract_spec_from_response("   ") is None

    @pytest.mark.asyncio
    async def test_type1_mutate_grammar_violation_retries(self) -> None:
        """type1_mutate retries when the response fails grammar validation."""
        from unittest.mock import patch

        from supervisor.spec_mutator import type1_mutate

        parent = _make_valid_spec()

        # First response: invalid (removes required section)
        invalid_mutated = parent.replace("## Goals\nsome content\n", "")
        # Second response: valid (just changes content)
        valid_mutated = parent.replace("Goals\nsome content", "Goals\nbetter content")

        mock_msg1 = MagicMock()
        mock_msg1.content = [MagicMock(text=invalid_mutated)]
        mock_msg2 = MagicMock()
        mock_msg2.content = [MagicMock(text=valid_mutated)]

        call_count = 0

        async def fake_create(**kwargs: Any) -> Any:
            nonlocal call_count
            call_count += 1
            return mock_msg1 if call_count == 1 else mock_msg2

        mock_messages = MagicMock()
        mock_messages.create = fake_create
        mock_client = MagicMock()
        mock_client.messages = mock_messages

        with patch("supervisor.spec_mutator.anthropic.AsyncAnthropic", return_value=mock_client):
            result = await type1_mutate(parent, {})

        assert result is not None
        assert call_count == 2  # retried once

    @pytest.mark.asyncio
    async def test_type1_mutate_returns_none_on_all_failures(self) -> None:
        """type1_mutate returns None when all attempts fail grammar check."""
        from unittest.mock import patch

        from supervisor.spec_mutator import type1_mutate

        parent = _make_valid_spec()
        # Always return a spec missing a required section
        bad = parent.replace("## Goals\nsome content\n", "")

        mock_msg = MagicMock()
        mock_msg.content = [MagicMock(text=bad)]

        async def fake_create(**kwargs: Any) -> Any:
            return mock_msg

        mock_messages = MagicMock()
        mock_messages.create = fake_create
        mock_client = MagicMock()
        mock_client.messages = mock_messages

        with patch("supervisor.spec_mutator.anthropic.AsyncAnthropic", return_value=mock_client):
            result = await type1_mutate(parent, {})

        assert result is None


# ---------------------------------------------------------------------------
# BO loop unit tests (cambrian-yy5)
# ---------------------------------------------------------------------------


class TestBOLoop:
    def test_extract_features_base_spec_all_zeros(self, tmp_path: Path) -> None:
        from supervisor.bo_loop import extract_features
        from supervisor.spec_grammar import evolvable_sections

        spec = _make_valid_spec()
        sections = evolvable_sections(spec)
        features = extract_features(spec, sections, spec)
        assert all(f == 0.0 for f in features)

    def test_extract_features_changed_section(self, tmp_path: Path) -> None:
        from supervisor.bo_loop import extract_features
        from supervisor.spec_grammar import evolvable_sections

        base = _make_valid_spec()
        modified = base.replace("## Goals\nsome content\n", "## Goals\nline1\nline2\nline3\n")
        sections = evolvable_sections(base)
        features = extract_features(modified, sections, base)
        # Goals section grew by 2 lines → one dimension should be > 0
        assert any(f > 0 for f in features)

    def test_decode_suggestion_picks_largest_magnitude(self) -> None:
        from supervisor.bo_loop import decode_suggestion

        sections = ["Goals", "LLM Integration", "Contracts"]
        # Second dimension has largest magnitude
        suggested_x = [2.0, 50.0, -3.0]
        target = decode_suggestion(suggested_x, sections, {})
        assert target == "LLM Integration"

    def test_decode_suggestion_small_magnitudes_fallback(self) -> None:
        from supervisor.bo_loop import decode_suggestion

        sections = ["Goals", "LLM Integration"]
        # All near-zero → falls back to propose_target_section
        suggested_x = [0.5, 0.3]
        # Empty campaign_summary → propose_target_section returns None
        target = decode_suggestion(suggested_x, sections, {})
        assert target is None

    def test_bo_loop_init(self, tmp_path: Path) -> None:
        from supervisor.bo_loop import SpecBOLoop

        spec = _make_valid_spec()
        spec_path = tmp_path / "spec.md"
        spec_path.write_text(spec)
        loop = SpecBOLoop(spec_path, budget=5)
        assert len(loop.section_names) > 0
        assert loop.budget == 5

    def test_bo_loop_optimizer_dimensions_match_sections(self, tmp_path: Path) -> None:
        from supervisor.bo_loop import SpecBOLoop
        from supervisor.spec_grammar import evolvable_sections

        spec = _make_valid_spec()
        spec_path = tmp_path / "spec.md"
        spec_path.write_text(spec)
        loop = SpecBOLoop(spec_path, budget=3)
        expected = len(evolvable_sections(spec))
        assert len(loop.optimizer.space.dimensions) == expected


# ---------------------------------------------------------------------------
# Mini-campaign screener tests (cambrian-3sb)
# ---------------------------------------------------------------------------


class TestMiniCampaignScreener:
    @pytest.mark.asyncio
    async def test_screen_passes_when_any_viable(self) -> None:
        from unittest.mock import patch

        from supervisor.campaign import screen_mutation

        async def fake_run_campaign(**kwargs: Any) -> dict:
            return {"viability_rate": 0.5, "generation_count": 2}

        with patch("supervisor.campaign.run_campaign", side_effect=fake_run_campaign):
            passes, summary = await screen_mutation(Path("/tmp/spec.md"))

        assert passes is True
        assert summary["viability_rate"] == 0.5

    @pytest.mark.asyncio
    async def test_screen_fails_when_none_viable(self) -> None:
        from unittest.mock import patch

        from supervisor.campaign import screen_mutation

        async def fake_run_campaign(**kwargs: Any) -> dict:
            return {"viability_rate": 0.0, "generation_count": 2}

        with patch("supervisor.campaign.run_campaign", side_effect=fake_run_campaign):
            passes, summary = await screen_mutation(Path("/tmp/spec.md"))

        assert passes is False

    @pytest.mark.asyncio
    async def test_screen_uses_mini_n_default(self) -> None:
        from unittest.mock import patch

        from supervisor.campaign import screen_mutation

        captured: dict = {}

        async def fake_run_campaign(**kwargs: Any) -> dict:
            captured.update(kwargs)
            return {"viability_rate": 1.0, "generation_count": kwargs.get("n", 0)}

        with patch("supervisor.campaign.run_campaign", side_effect=fake_run_campaign):
            await screen_mutation(Path("/tmp/spec.md"), n=2)

        assert captured.get("n") == 2


# ---------------------------------------------------------------------------
# Entanglement monitor tests
# ---------------------------------------------------------------------------


class TestEntanglementMonitor:
    def _make_diff(self, sections_changed: list[str], sections_unchanged: list[str]):
        from supervisor.spec_diff import SectionChange, SpecDiff

        changed = [
            SectionChange(section_name=s, lines_added=1, lines_removed=0, is_frozen=False)
            for s in sections_changed
        ]
        return SpecDiff(
            parent_hash="aaa",
            child_hash="bbb",
            sections_changed=changed,
            sections_unchanged=sections_unchanged,
            total_lines_added=1,
            total_lines_removed=0,
            unified_diff="",
        )

    def test_empty_diffs_returns_zero_report(self) -> None:
        from supervisor.entanglement import compute_entanglement_report

        report = compute_entanglement_report([])
        assert report.mutation_count == 0
        assert report.entanglement_trend == 0.0
        assert report.is_entangling is False

    def test_single_section_per_mutation_is_not_entangling(self) -> None:
        from supervisor.entanglement import compute_entanglement_report

        diffs = [
            self._make_diff(["A"], ["B", "C"]),
            self._make_diff(["B"], ["A", "C"]),
            self._make_diff(["C"], ["A", "B"]),
        ]
        report = compute_entanglement_report(diffs)
        assert report.mean_sections_per_mutation == 1.0
        assert report.is_entangling is False

    def test_rising_section_count_triggers_alert(self) -> None:
        from supervisor.entanglement import compute_entanglement_report

        # Each mutation touches progressively more sections
        diffs = [
            self._make_diff(["A"], ["B", "C", "D", "E"]),
            self._make_diff(["A", "B"], ["C", "D", "E"]),
            self._make_diff(["A", "B", "C"], ["D", "E"]),
            self._make_diff(["A", "B", "C", "D"], ["E"]),
            self._make_diff(["A", "B", "C", "D", "E"], []),
        ]
        report = compute_entanglement_report(diffs)
        assert report.is_entangling is True
        assert report.entanglement_trend > 0.0

    def test_independence_score_perfect_for_solo_mutations(self) -> None:
        from supervisor.entanglement import compute_entanglement_report

        diffs = [
            self._make_diff(["X"], ["Y"]),
            self._make_diff(["X"], ["Y"]),
        ]
        report = compute_entanglement_report(diffs)
        x_score = next(s for s in report.section_scores if s.section_name == "X")
        assert x_score.independence_score == 1.0
        assert x_score.mutations_touching == 2
        assert x_score.mutations_touching_alone == 2

    def test_independence_score_zero_for_always_coupled(self) -> None:
        from supervisor.entanglement import compute_entanglement_report

        diffs = [
            self._make_diff(["A", "B"], []),
            self._make_diff(["A", "B"], []),
        ]
        report = compute_entanglement_report(diffs)
        a_score = next(s for s in report.section_scores if s.section_name == "A")
        assert a_score.independence_score == 0.0

    def test_entanglement_alert_returns_none_when_stable(self) -> None:
        from supervisor.entanglement import compute_entanglement_report, entanglement_alert

        diffs = [self._make_diff(["A"], ["B"]) for _ in range(5)]
        report = compute_entanglement_report(diffs)
        assert entanglement_alert(report) is None

    def test_entanglement_alert_returns_string_when_rising(self) -> None:
        from supervisor.entanglement import compute_entanglement_report, entanglement_alert

        diffs = [
            self._make_diff(["A"], ["B", "C", "D", "E"]),
            self._make_diff(["A", "B"], ["C", "D", "E"]),
            self._make_diff(["A", "B", "C"], ["D", "E"]),
            self._make_diff(["A", "B", "C", "D"], ["E"]),
            self._make_diff(["A", "B", "C", "D", "E"], []),
        ]
        report = compute_entanglement_report(diffs)
        alert = entanglement_alert(report)
        assert alert is not None
        assert "Entanglement rising" in alert

    def test_cross_ref_matrix_populated_from_spec(self) -> None:
        from supervisor.entanglement import compute_entanglement_report

        spec = """\
## Alpha
References Beta here.

## Beta
No cross references.
"""
        diffs = [self._make_diff(["Alpha"], ["Beta"])]
        report = compute_entanglement_report(diffs, spec_text=spec)
        # Alpha references Beta — should appear in matrix
        assert "Beta" in report.cross_ref_matrix.get("Alpha", [])

    def test_section_scores_ordered_alphabetically(self) -> None:
        from supervisor.entanglement import compute_entanglement_report

        diffs = [self._make_diff(["C", "A", "B"], [])]
        report = compute_entanglement_report(diffs)
        names = [s.section_name for s in report.section_scores]
        assert names == sorted(names)


# ---------------------------------------------------------------------------
# Adaptive test generation tests
# ---------------------------------------------------------------------------


class TestAdaptiveTests:
    def test_load_tests_returns_empty_when_file_absent(self, tmp_path: Path) -> None:
        from supervisor.adaptive_tests import load_tests

        result = load_tests(str(tmp_path))
        assert result == []

    def test_save_and_load_roundtrip(self, tmp_path: Path) -> None:
        from supervisor.adaptive_tests import load_tests, save_tests

        tests = [{"test_id": "adaptive-0-0", "test_code": "def test_x(): pass"}]
        save_tests(tests, str(tmp_path))
        loaded = load_tests(str(tmp_path))
        assert loaded == tests

    def test_get_active_tests_filters_expired(self, tmp_path: Path) -> None:
        from supervisor.adaptive_tests import get_active_tests, save_tests

        tests = [
            {"test_id": "t0", "created_at_campaign": 0, "expires_after": 5},  # expires at 5
            {"test_id": "t1", "created_at_campaign": 0, "expires_after": 2},  # expires at 2
        ]
        save_tests(tests, str(tmp_path))

        active = get_active_tests(campaign_index=3, artifacts_root=str(tmp_path))
        assert len(active) == 1
        assert active[0]["test_id"] == "t0"

    def test_get_active_tests_newest_first(self, tmp_path: Path) -> None:
        from supervisor.adaptive_tests import get_active_tests, save_tests

        tests = [
            {"test_id": "old", "created_at_campaign": 0, "expires_after": 10},
            {"test_id": "new", "created_at_campaign": 5, "expires_after": 10},
        ]
        save_tests(tests, str(tmp_path))

        active = get_active_tests(campaign_index=6, artifacts_root=str(tmp_path))
        assert active[0]["test_id"] == "new"

    def test_expire_old_tests_removes_expired(self, tmp_path: Path) -> None:
        from supervisor.adaptive_tests import expire_old_tests, load_tests, save_tests

        tests = [
            {"test_id": "keep", "created_at_campaign": 5, "expires_after": 5},
            {"test_id": "drop", "created_at_campaign": 0, "expires_after": 3},
        ]
        save_tests(tests, str(tmp_path))

        removed = expire_old_tests(campaign_index=4, artifacts_root=str(tmp_path))
        assert removed == 1
        remaining = load_tests(str(tmp_path))
        assert len(remaining) == 1
        assert remaining[0]["test_id"] == "keep"

    def test_extract_test_cases_splits_on_def_test(self) -> None:
        from supervisor.adaptive_tests import _extract_test_cases

        text = """\
def test_alpha():
    assert 1 == 1

def test_beta():
    import os
    assert os.path.exists("/")
"""
        cases = _extract_test_cases(text)
        assert len(cases) == 2
        assert cases[0].startswith("def test_alpha")
        assert cases[1].startswith("def test_beta")

    def test_extract_test_cases_strips_markdown_fence(self) -> None:
        from supervisor.adaptive_tests import _extract_test_cases

        text = "```python\ndef test_wrapped():\n    assert True\n```"
        cases = _extract_test_cases(text)
        assert len(cases) == 1
        assert cases[0].startswith("def test_wrapped")

    @pytest.mark.asyncio
    async def test_generate_skips_fully_viable_campaign(self, tmp_path: Path) -> None:
        from supervisor.adaptive_tests import generate_adaptive_tests

        summary = {"viability_rate": 1.0, "failure_distribution": {"none": 5}}
        result = await generate_adaptive_tests(summary, "spec text", 0, str(tmp_path))
        assert result == []

    @pytest.mark.asyncio
    async def test_generate_calls_api_on_failure(self, tmp_path: Path) -> None:
        from unittest.mock import AsyncMock, MagicMock, patch

        from supervisor.adaptive_tests import generate_adaptive_tests

        summary = {
            "viability_rate": 0.5,
            "failure_distribution": {"build": 3, "none": 2},
            "campaign_id": "test-campaign",
        }

        fake_content = MagicMock()
        fake_content.text = (
            "def test_requirements_present():\n"
            "    from pathlib import Path\n"
            "    assert Path('requirements.txt').exists()\n"
            "\n"
            "def test_no_syntax_errors():\n"
            "    import py_compile\n"
            "    py_compile.compile('app.py')\n"
        )
        fake_response = MagicMock()
        fake_response.content = [fake_content]

        mock_create = AsyncMock(return_value=fake_response)
        with patch("anthropic.AsyncAnthropic") as mock_client_cls:
            mock_client_cls.return_value.messages.create = mock_create
            result = await generate_adaptive_tests(
                summary, "spec text", 0, str(tmp_path)
            )

        assert len(result) >= 1
        assert result[0]["failure_stage"] == "build"
        assert result[0]["test_id"].startswith("adaptive-0-")
