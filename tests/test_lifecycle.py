"""Lifecycle and contract integration tests.

These tests verify end-to-end flows across components: the Supervisor HTTP API
wire format, cross-component schema contracts (viability report structure,
generation record fields), and the spawn → test → promote/rollback lifecycle.

They would have caught the pre-M2 review issues: wrong mock targets, missing
error viability helper, field naming drift between spec and code, and
inconsistent response schemas.

Run with: uv run pytest tests/test_lifecycle.py -v
"""

import json
import time
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient


@pytest.fixture
def artifacts_root(tmp_path: Path) -> Path:
    root = tmp_path / "artifacts"
    root.mkdir()
    return root


@pytest.fixture(autouse=True)
def set_artifacts_root(artifacts_root: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CAMBRIAN_ARTIFACTS_ROOT", str(artifacts_root))
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")


def _write_min_manifest(artifact_dir: Path) -> None:
    manifest = {
        "cambrian-version": 1,
        "generation": 1,
        "parent-generation": 0,
        "spec-hash": "sha256:" + "a" * 64,
        "artifact-hash": "sha256:" + "b" * 64,
        "producer-model": "claude-sonnet-4-6",
        "token-usage": {"input": 1, "output": 1},
        "files": ["manifest.json"],
        "created-at": "2026-03-21T14:30:00Z",
        "entry": {
            "build": "pip install -r requirements.txt",
            "test": "pytest tests/",
            "start": "python -m src.prime",
            "health": "http://localhost:8401/health",
        },
    }
    (artifact_dir / "manifest.json").write_text(json.dumps(manifest))


@pytest.fixture
def mock_git_ops(tmp_path: Path) -> Any:
    with patch("supervisor.supervisor.git_ops") as mock:
        mock.artifacts_root.return_value = str(tmp_path / "artifacts")
        mock.ensure_on_main = AsyncMock()
        mock.git = AsyncMock()
        mock.create_generation_branch = AsyncMock()
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


# ---------------------------------------------------------------------------
# 1. API response schema contracts
# ---------------------------------------------------------------------------


class TestAPIResponseSchemas:
    """Verify that all API responses match the spec-defined wire format.

    This catches field naming drift (artifact_ref vs artifact-ref) and missing
    required response fields.
    """

    @pytest.mark.asyncio
    async def test_stats_response_schema(self, client: TestClient) -> None:
        """GET /stats must return {generation, status, uptime}."""
        resp = await client.get("/stats")
        data = await resp.json()
        assert "generation" in data
        assert "status" in data
        assert "uptime" in data
        assert isinstance(data["generation"], int)
        assert isinstance(data["status"], str)
        assert isinstance(data["uptime"], int)

    @pytest.mark.asyncio
    async def test_stats_status_is_supervisor_state(self, client: TestClient) -> None:
        """status field must be Supervisor operational state, not generation outcome."""
        valid_states = {"idle", "spawning", "testing", "promoting", "rolling-back"}
        resp = await client.get("/stats")
        data = await resp.json()
        assert data["status"] in valid_states, (
            f"status {data['status']!r} is not a valid Supervisor state"
        )

    @pytest.mark.asyncio
    async def test_spawn_success_response_schema(
        self, client: TestClient, artifacts_root: Path
    ) -> None:
        """POST /spawn success must return {ok, container-id, generation}."""
        art_dir = artifacts_root / "gen-1"
        art_dir.mkdir()
        _write_min_manifest(art_dir)

        with (
            patch("supervisor.supervisor.aiodocker.Docker") as mock_cls,
            patch("supervisor.supervisor._schedule_test_rig", return_value=None),
        ):
            mock = AsyncMock()
            mock.images.list = AsyncMock(return_value=[{"RepoTags": ["cambrian-base:latest"]}])
            mock.close = AsyncMock()
            mock_cls.return_value = mock

            resp = await client.post(
                "/spawn",
                json={
                    "generation": 1,
                    "artifact-path": "gen-1",
                    "spec-hash": "sha256:" + "a" * 64,
                },
            )

        assert resp.status == 200
        data = await resp.json()
        assert data["ok"] is True
        assert "container-id" in data, "Response missing container-id (kebab-case)"
        assert "generation" in data
        # Verify kebab-case, not snake_case
        assert "container_id" not in data, "Response uses snake_case container_id"

    @pytest.mark.asyncio
    async def test_spawn_error_response_schema(
        self, client: TestClient, artifacts_root: Path
    ) -> None:
        """POST /spawn error must return {ok: false, error: string}."""
        resp = await client.post(
            "/spawn",
            json={
                "generation": 1,
                "artifact-path": "nonexistent",
                "spec-hash": "abc",
            },
        )
        assert resp.status == 400
        data = await resp.json()
        assert data["ok"] is False
        assert isinstance(data["error"], str)

    @pytest.mark.asyncio
    async def test_promote_success_response_schema(
        self, client: TestClient, artifacts_root: Path
    ) -> None:
        """POST /promote success must return {ok: true, generation: int}."""
        from supervisor import generations

        generations.append({"generation": 1, "outcome": "tested", "artifact-ref": "gen-1"})
        resp = await client.post("/promote", json={"generation": 1})
        data = await resp.json()
        assert data["ok"] is True
        assert data["generation"] == 1

    @pytest.mark.asyncio
    async def test_rollback_success_response_schema(
        self, client: TestClient, artifacts_root: Path
    ) -> None:
        """POST /rollback success must return {ok: true, generation: int}."""
        from supervisor import generations

        generations.append({"generation": 1, "outcome": "tested", "artifact-ref": "gen-1"})
        resp = await client.post("/rollback", json={"generation": 1})
        data = await resp.json()
        assert data["ok"] is True
        assert data["generation"] == 1

    @pytest.mark.asyncio
    async def test_versions_response_is_list(self, client: TestClient) -> None:
        """GET /versions must return a JSON array."""
        resp = await client.get("/versions")
        data = await resp.json()
        assert isinstance(data, list)

    @pytest.mark.asyncio
    async def test_debug_state_response_schema(self, client: TestClient) -> None:
        """GET /debug/state must return {status, current_generation, uptime, records, config}."""
        resp = await client.get("/debug/state")
        data = await resp.json()
        for key in ("status", "current_generation", "uptime", "records", "config"):
            assert key in data, f"debug/state missing key: {key}"


# ---------------------------------------------------------------------------
# 2. Viability report schema contract
# ---------------------------------------------------------------------------


class TestViabilityReportContract:
    """Verify that the error viability report helper produces a valid schema.

    The pre-M2 review found duplicated viability report construction that
    diverged. The fix was to extract _make_error_viability. These tests verify
    the schema is correct.
    """

    def test_error_viability_has_required_fields(self) -> None:
        from supervisor.supervisor import _make_error_viability

        report = _make_error_viability(1, "test error")
        required = {
            "generation",
            "status",
            "failure_stage",
            "checks",
            "completed_at",
            "diagnostics",
        }
        assert required <= set(report.keys())

    def test_error_viability_status_is_non_viable(self) -> None:
        from supervisor.supervisor import _make_error_viability

        report = _make_error_viability(1, "test error")
        assert report["status"] == "non-viable"

    def test_error_viability_checks_have_all_stages(self) -> None:
        from supervisor.supervisor import _make_error_viability

        report = _make_error_viability(1, "test error")
        for stage in ("manifest", "build", "test", "start", "health"):
            assert stage in report["checks"], f"Missing check stage: {stage}"
            assert "passed" in report["checks"][stage]

    def test_error_viability_diagnostics_schema(self) -> None:
        from supervisor.supervisor import _make_error_viability

        report = _make_error_viability(1, "test error")
        diag = report["diagnostics"]
        for key in ("stage", "summary", "exit_code", "failures", "stdout_tail", "stderr_tail"):
            assert key in diag, f"Missing diagnostics key: {key}"
        assert diag["summary"] == "test error"

    def test_error_viability_generation_matches_input(self) -> None:
        from supervisor.supervisor import _make_error_viability

        report = _make_error_viability(42, "gen 42 error")
        assert report["generation"] == 42

    def test_error_viability_completed_at_is_iso(self) -> None:
        from datetime import datetime

        from supervisor.supervisor import _make_error_viability

        report = _make_error_viability(1, "test")
        # Should be parseable as ISO datetime
        datetime.fromisoformat(report["completed_at"])


class TestCampaignErrorRecord:
    def test_campaign_error_record_schema(self) -> None:
        from supervisor.campaign import _error_record

        record = _error_record(7, "artifact generation failed")
        assert record["generation"] == 7
        assert record["outcome"] == "failed"
        viability = record["viability"]
        required = {
            "generation",
            "status",
            "failure_stage",
            "checks",
            "completed_at",
            "diagnostics",
        }
        assert required <= set(viability.keys())
        assert viability["generation"] == 7
        assert viability["status"] == "non-viable"


# ---------------------------------------------------------------------------
# 3. Spawn → Test → Promote/Rollback lifecycle
# ---------------------------------------------------------------------------


def _make_mock_docker() -> tuple[Any, Any]:
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


class TestSpawnTestPromoteLifecycle:
    """Test the full spawn → test rig → promote lifecycle.

    This verifies that components agree on the generation record schema
    and that state transitions happen correctly end to end.
    """

    @pytest.mark.asyncio
    async def test_spawn_creates_record_then_test_rig_updates_it(
        self, artifacts_root: Path
    ) -> None:
        """Full spawn → run_test_rig flow: record goes in_progress → tested."""
        from supervisor import generations
        from supervisor import supervisor as sup

        # Manually create the record (simulating what handle_spawn does)
        generations.append(
            {
                "generation": 1,
                "parent": 0,
                "spec-hash": "sha256:abc",
                "artifact-hash": "",
                "outcome": "in_progress",
                "artifact-ref": "gen-1",
                "created": "2026-03-30T00:00:00Z",
                "completed": None,
                "container-id": "lab-gen-1",
                "viability": None,
            }
        )

        artifact_path = artifacts_root / "gen-1"
        artifact_path.mkdir()
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
            "completed_at": "2026-03-30T00:01:00Z",
        }
        # The supervisor writes the report to an isolated output dir (separate from
        # the artifact workspace). Seed that dir with the viability report.
        output_dir = artifacts_root / "output-gen-1"
        output_dir.mkdir()
        (output_dir / "viability-report.json").write_text(json.dumps(viability))

        mock_docker_cls, _ = _make_mock_docker()
        with patch("supervisor.supervisor.tempfile.mkdtemp", return_value=str(output_dir)):
            with patch("supervisor.supervisor.aiodocker.Docker", mock_docker_cls):
                await sup.run_test_rig(1, artifact_path, "lab-gen-1")

        rec = generations.get(1)
        assert rec["outcome"] == "tested"
        assert rec["viability"]["status"] == "viable"
        # All original fields should still be present
        assert rec["spec-hash"] == "sha256:abc"
        assert rec["container-id"] == "lab-gen-1"
        assert rec["created"] == "2026-03-30T00:00:00Z"

    @pytest.mark.asyncio
    async def test_promote_after_test_sets_terminal_state(
        self, client: TestClient, artifacts_root: Path, mock_git_ops: Any
    ) -> None:
        """After test rig marks tested, /promote sets promoted (terminal)."""
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

        rec = generations.get(1)
        assert rec["outcome"] == "promoted"
        # Promoted is terminal — further updates should be rejected
        generations.update(1, {"outcome": "failed"})
        assert generations.get(1)["outcome"] == "promoted"

    @pytest.mark.asyncio
    async def test_rollback_after_test_sets_terminal_state(
        self, client: TestClient, artifacts_root: Path, mock_git_ops: Any
    ) -> None:
        """After test rig marks tested, /rollback sets failed (terminal)."""
        from supervisor import generations

        generations.append(
            {
                "generation": 1,
                "outcome": "tested",
                "artifact-ref": "gen-1",
            }
        )
        resp = await client.post("/rollback", json={"generation": 1})
        assert resp.status == 200

        rec = generations.get(1)
        assert rec["outcome"] == "failed"
        # Failed is terminal — further updates should be rejected
        generations.update(1, {"outcome": "promoted"})
        assert generations.get(1)["outcome"] == "failed"

    @pytest.mark.asyncio
    async def test_timeout_sets_terminal_state(self, artifacts_root: Path) -> None:
        """Container timeout sets timeout (terminal) directly."""
        from supervisor import generations
        from supervisor import supervisor as sup

        generations.append({"generation": 1, "outcome": "in_progress"})
        artifact_path = artifacts_root / "gen-1"
        artifact_path.mkdir()

        mock_docker_cls, mock_docker = _make_mock_docker()
        mock_container = mock_docker.containers.create_or_replace.return_value
        mock_container.wait = AsyncMock(side_effect=TimeoutError)

        with patch("supervisor.supervisor.aiodocker.Docker", mock_docker_cls):
            await sup.run_test_rig(1, artifact_path, "lab-gen-1")

        rec = generations.get(1)
        assert rec["outcome"] == "timeout"
        # Timeout is terminal
        generations.update(1, {"outcome": "tested"})
        assert generations.get(1)["outcome"] == "timeout"


# ---------------------------------------------------------------------------
# 4. Docker mock correctness
# ---------------------------------------------------------------------------


class TestDockerMockCorrectness:
    """Verify that tests mock the correct Docker API methods.

    The pre-M2 review found that test_spawn_missing_docker_image mocked
    images.inspect() but code used images.list(). These tests ensure we
    mock the actual methods used in the code.
    """

    @pytest.mark.asyncio
    async def test_image_check_uses_list_not_inspect(
        self, client: TestClient, artifacts_root: Path
    ) -> None:
        """Verify the code calls images.list() to check for images."""
        art_dir = artifacts_root / "gen-1"
        art_dir.mkdir()
        _write_min_manifest(art_dir)

        with patch("supervisor.supervisor.aiodocker.Docker") as mock_cls:
            mock_docker = AsyncMock()
            mock_docker.images.list = AsyncMock(return_value=[])
            mock_docker.close = AsyncMock()
            mock_cls.return_value = mock_docker

            await client.post(
                "/spawn",
                json={
                    "generation": 1,
                    "artifact-path": "gen-1",
                    "spec-hash": "sha256:" + "a" * 64,
                },
            )

            # images.list() must have been called
            mock_docker.images.list.assert_called_once()

    @pytest.mark.asyncio
    async def test_container_lifecycle_methods(self, artifacts_root: Path) -> None:
        """Verify run_test_rig calls the correct container lifecycle methods."""
        from supervisor import generations
        from supervisor import supervisor as sup

        generations.append({"generation": 1, "outcome": "in_progress"})
        artifact_path = artifacts_root / "gen-1"
        artifact_path.mkdir()

        mock_docker_cls, mock_docker = _make_mock_docker()
        mock_container = mock_docker.containers.create_or_replace.return_value

        with patch("supervisor.supervisor.aiodocker.Docker", mock_docker_cls):
            await sup.run_test_rig(1, artifact_path, "lab-gen-1")

        # Verify the correct lifecycle: create_or_replace → start → wait
        # Note: delete() is intentionally NOT called (cambrian-p0z0) — containers are
        # retained after the test rig exits so logs can be inspected with docker logs.
        mock_docker.containers.create_or_replace.assert_called_once()
        mock_container.start.assert_called_once()
        mock_container.wait.assert_called_once()
        mock_container.delete.assert_not_called()
        mock_docker.close.assert_called_once()


# ---------------------------------------------------------------------------
# 5. Supervisor status transitions
# ---------------------------------------------------------------------------


class TestSupervisorStatusTransitions:
    """Verify Supervisor operational status is set correctly during operations."""

    @pytest.mark.asyncio
    async def test_status_returns_to_idle_after_test_rig(self, artifacts_root: Path) -> None:
        from supervisor import generations
        from supervisor import supervisor as sup

        generations.append({"generation": 1, "outcome": "in_progress"})
        artifact_path = artifacts_root / "gen-1"
        artifact_path.mkdir()

        mock_docker_cls, _ = _make_mock_docker()
        with patch("supervisor.supervisor.aiodocker.Docker", mock_docker_cls):
            await sup.run_test_rig(1, artifact_path, "lab-gen-1")
        assert sup._status == "idle"

    @pytest.mark.asyncio
    async def test_status_returns_to_idle_after_timeout(self, artifacts_root: Path) -> None:
        from supervisor import generations
        from supervisor import supervisor as sup

        generations.append({"generation": 1, "outcome": "in_progress"})
        artifact_path = artifacts_root / "gen-1"
        artifact_path.mkdir()

        mock_docker_cls, mock_docker = _make_mock_docker()
        mock_container = mock_docker.containers.create_or_replace.return_value
        mock_container.wait = AsyncMock(side_effect=TimeoutError)

        with patch("supervisor.supervisor.aiodocker.Docker", mock_docker_cls):
            await sup.run_test_rig(1, artifact_path, "lab-gen-1")
        assert sup._status == "idle"

    @pytest.mark.asyncio
    async def test_status_returns_to_idle_after_docker_error(self, artifacts_root: Path) -> None:
        from supervisor import generations
        from supervisor import supervisor as sup

        generations.append({"generation": 1, "outcome": "in_progress"})
        artifact_path = artifacts_root / "gen-1"
        artifact_path.mkdir()

        mock_docker_cls = MagicMock()
        mock_docker = AsyncMock()
        mock_docker.containers.create_or_replace = AsyncMock(side_effect=Exception("boom"))
        mock_docker.close = AsyncMock()
        mock_docker_cls.return_value = mock_docker

        with patch("supervisor.supervisor.aiodocker.Docker", mock_docker_cls):
            await sup.run_test_rig(1, artifact_path, "lab-gen-1")
        assert sup._status == "idle"

    @pytest.mark.asyncio
    async def test_status_idle_after_promote(
        self, client: TestClient, artifacts_root: Path
    ) -> None:
        from supervisor import generations

        generations.append({"generation": 1, "outcome": "tested", "artifact-ref": "gen-1"})
        await client.post("/promote", json={"generation": 1})

        import supervisor.supervisor as sup

        assert sup._status == "idle"

    @pytest.mark.asyncio
    async def test_status_idle_after_rollback(self, client: TestClient) -> None:
        await client.post("/rollback", json={"generation": 1})

        import supervisor.supervisor as sup

        assert sup._status == "idle"


# ---------------------------------------------------------------------------
# 6. Generation record field naming (wire format agreement)
# ---------------------------------------------------------------------------


class TestWireFormatFieldNaming:
    """Verify that generation records use kebab-case for wire-format fields.

    This catches the artifact_ref → artifact-ref class of bugs that was
    found in the pre-M2 review.
    """

    @pytest.mark.asyncio
    async def test_spawn_record_uses_kebab_case(
        self, client: TestClient, artifacts_root: Path
    ) -> None:
        """The record created by /spawn must use kebab-case field names."""
        art_dir = artifacts_root / "gen-1"
        art_dir.mkdir()
        _write_min_manifest(art_dir)

        with (
            patch("supervisor.supervisor.aiodocker.Docker") as mock_cls,
            patch("supervisor.supervisor._schedule_test_rig", return_value=None),
        ):
            mock = AsyncMock()
            mock.images.list = AsyncMock(return_value=[{"RepoTags": ["cambrian-base:latest"]}])
            mock.close = AsyncMock()
            mock_cls.return_value = mock

            await client.post(
                "/spawn",
                json={
                    "generation": 1,
                    "artifact-path": "gen-1",
                    "spec-hash": "sha256:" + "a" * 64,
                },
            )

        from supervisor import generations

        rec = generations.get(1)
        assert rec is not None
        # These fields MUST be kebab-case per spec
        assert "spec-hash" in rec
        assert "artifact-hash" in rec
        # artifact-ref is absent while in_progress (MAY field per spec §4.3)
        assert "container-id" in rec
        # These must NOT appear in snake_case
        assert "spec_hash" not in rec
        assert "artifact_hash" not in rec
        assert "artifact_ref" not in rec
        assert "container_id" not in rec

    @pytest.mark.asyncio
    async def test_promote_updates_use_kebab_case(
        self, client: TestClient, artifacts_root: Path
    ) -> None:
        from supervisor import generations

        generations.append({"generation": 1, "outcome": "tested", "artifact-ref": "gen-1"})
        await client.post("/promote", json={"generation": 1})
        rec = generations.get(1)
        assert "artifact-ref" in rec
        assert "artifact_ref" not in rec

    @pytest.mark.asyncio
    async def test_versions_endpoint_returns_kebab_case(
        self, client: TestClient, artifacts_root: Path
    ) -> None:
        """GET /versions returns records with kebab-case fields."""
        from supervisor import generations

        generations.append(
            {
                "generation": 1,
                "outcome": "tested",
                "spec-hash": "sha256:abc",
                "artifact-ref": "gen-1",
                "container-id": "lab-gen-1",
            }
        )
        resp = await client.get("/versions")
        data = await resp.json()
        assert len(data) == 1
        rec = data[0]
        assert "spec-hash" in rec
        assert "artifact-ref" in rec
        assert "container-id" in rec
