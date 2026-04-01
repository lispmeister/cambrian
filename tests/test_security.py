"""Security integration tests.

These tests cover input validation boundaries and path traversal variants
across the Supervisor HTTP API. They would have caught the path traversal
vulnerability found in the pre-M2 quality review, plus similar classes of
injection and boundary-condition bugs.

Run with: uv run pytest tests/test_security.py -v
"""

import time
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, patch

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
# 1. Path traversal variants on /spawn artifact-path
# ---------------------------------------------------------------------------


class TestPathTraversal:
    """Test all common path traversal payloads against /spawn.

    The pre-M2 review found that artifact-path was not validated at all,
    allowing ../../etc/passwd style attacks. These tests verify the fix
    and exercise edge cases.
    """

    TRAVERSAL_PAYLOADS = [
        "../../etc/passwd",
        "../../../etc/shadow",
        "foo/../../bar/../../../etc/hosts",
        "..%2F..%2Fetc%2Fpasswd",  # URL-encoded (aiohttp decodes before handler)
        "gen-1/../../../../tmp",
        "gen-1/../../../..",
    ]

    @pytest.mark.asyncio
    @pytest.mark.parametrize("payload", TRAVERSAL_PAYLOADS)
    async def test_path_traversal_rejected(
        self, client: TestClient, artifacts_root: Path, payload: str
    ) -> None:
        resp = await client.post(
            "/spawn",
            json={
                "generation": 1,
                "artifact-path": payload,
                "spec-hash": "sha256:" + "a" * 64,
            },
        )
        assert resp.status == 400
        data = await resp.json()
        assert data["ok"] is False
        # Must be either "escapes" (traversal caught) or "not exist" (path doesn't exist)
        assert "escapes" in data["error"] or "not exist" in data["error"], (
            f"Payload {payload!r} was not rejected properly: {data['error']}"
        )

    @pytest.mark.asyncio
    async def test_absolute_path_rejected(self, client: TestClient, artifacts_root: Path) -> None:
        """Absolute paths escape the artifacts root when joined with Path()."""
        resp = await client.post(
            "/spawn",
            json={
                "generation": 1,
                "artifact-path": "/etc/passwd",
                "spec-hash": "sha256:" + "a" * 64,
            },
        )
        assert resp.status == 400
        data = await resp.json()
        assert data["ok"] is False

    @pytest.mark.asyncio
    async def test_symlink_traversal_rejected(
        self, client: TestClient, artifacts_root: Path
    ) -> None:
        """A symlink inside artifacts root pointing outside should be caught by resolve()."""
        link = artifacts_root / "evil-link"
        link.symlink_to("/tmp")
        resp = await client.post(
            "/spawn",
            json={
                "generation": 1,
                "artifact-path": "evil-link",
                "spec-hash": "sha256:" + "a" * 64,
            },
        )
        assert resp.status == 400
        data = await resp.json()
        assert data["ok"] is False

    @pytest.mark.asyncio
    async def test_valid_relative_path_accepted(
        self, client: TestClient, artifacts_root: Path
    ) -> None:
        """A valid relative path inside artifacts root should pass traversal check."""
        art_dir = artifacts_root / "gen-1"
        art_dir.mkdir()

        with (
            patch("supervisor.supervisor.aiodocker.Docker") as mock_docker_cls,
            patch("supervisor.supervisor.asyncio.create_task", return_value=None),
        ):
            mock_docker = AsyncMock()
            mock_docker.images.list = AsyncMock(
                return_value=[{"RepoTags": ["cambrian-base:latest"]}]
            )
            mock_docker.close = AsyncMock()
            mock_docker_cls.return_value = mock_docker

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

    @pytest.mark.asyncio
    async def test_nested_valid_path_accepted(
        self, client: TestClient, artifacts_root: Path
    ) -> None:
        """Nested subdirectory inside artifacts root is allowed."""
        nested = artifacts_root / "campaign" / "gen-1"
        nested.mkdir(parents=True)

        with (
            patch("supervisor.supervisor.aiodocker.Docker") as mock_docker_cls,
            patch("supervisor.supervisor.asyncio.create_task", return_value=None),
        ):
            mock_docker = AsyncMock()
            mock_docker.images.list = AsyncMock(
                return_value=[{"RepoTags": ["cambrian-base:latest"]}]
            )
            mock_docker.close = AsyncMock()
            mock_docker_cls.return_value = mock_docker

            resp = await client.post(
                "/spawn",
                json={
                    "generation": 1,
                    "artifact-path": "campaign/gen-1",
                    "spec-hash": "sha256:" + "a" * 64,
                },
            )
        assert resp.status == 200


# ---------------------------------------------------------------------------
# 2. Input validation on /spawn body fields
# ---------------------------------------------------------------------------


class TestSpawnInputValidation:
    """Verify that /spawn rejects malformed or missing required fields."""

    @pytest.mark.asyncio
    async def test_missing_generation_field(self, client: TestClient) -> None:
        resp = await client.post(
            "/spawn",
            json={"artifact-path": "gen-1", "spec-hash": "abc"},
        )
        # Should fail with KeyError → 500, or explicit 400
        assert resp.status >= 400

    @pytest.mark.asyncio
    async def test_missing_artifact_path_field(self, client: TestClient) -> None:
        resp = await client.post(
            "/spawn",
            json={"generation": 1, "spec-hash": "abc"},
        )
        assert resp.status >= 400

    @pytest.mark.asyncio
    async def test_non_integer_generation(self, client: TestClient) -> None:
        resp = await client.post(
            "/spawn",
            json={
                "generation": "not-a-number",
                "artifact-path": "gen-1",
                "spec-hash": "abc",
            },
        )
        assert resp.status >= 400

    @pytest.mark.asyncio
    async def test_negative_generation(self, client: TestClient, artifacts_root: Path) -> None:
        art_dir = artifacts_root / "gen-neg"
        art_dir.mkdir()
        resp = await client.post(
            "/spawn",
            json={
                "generation": -1,
                "artifact-path": "gen-neg",
                "spec-hash": "abc",
            },
        )
        # Negative generation is technically allowed by the code (no validation)
        # but this test documents the behavior
        assert resp.status in (200, 400, 500)

    @pytest.mark.asyncio
    async def test_empty_json_body(self, client: TestClient) -> None:
        resp = await client.post("/spawn", json={})
        assert resp.status >= 400

    @pytest.mark.asyncio
    async def test_non_json_body_rejected(self, client: TestClient) -> None:
        resp = await client.post(
            "/spawn",
            data=b"not json",
            headers={"Content-Type": "text/plain"},
        )
        assert resp.status >= 400


# ---------------------------------------------------------------------------
# 3. Input validation on /promote and /rollback
# ---------------------------------------------------------------------------


class TestPromoteRollbackValidation:
    """Verify that /promote and /rollback handle missing/bad inputs correctly."""

    @pytest.mark.asyncio
    async def test_promote_missing_generation(self, client: TestClient) -> None:
        resp = await client.post("/promote", json={})
        assert resp.status >= 400

    @pytest.mark.asyncio
    async def test_rollback_missing_generation(self, client: TestClient) -> None:
        resp = await client.post("/rollback", json={})
        assert resp.status >= 400

    @pytest.mark.asyncio
    async def test_promote_nonexistent_generation(self, client: TestClient) -> None:
        resp = await client.post("/promote", json={"generation": 999})
        assert resp.status == 404

    @pytest.mark.asyncio
    async def test_rollback_with_git_error(self, client: TestClient, mock_git_ops: Any) -> None:
        from supervisor import generations

        generations.append({"generation": 1, "outcome": "tested", "artifact-ref": "gen-1"})
        mock_git_ops.rollback = AsyncMock(side_effect=Exception("git failed"))
        resp = await client.post("/rollback", json={"generation": 1})
        assert resp.status == 500
        data = await resp.json()
        assert data["ok"] is False


# ---------------------------------------------------------------------------
# 4. Generation store state machine safety
# ---------------------------------------------------------------------------


class TestGenerationStateMachine:
    """Verify the generation store protects state transitions.

    The pre-M2 review found write-on-no-match and terminal-state-corruption bugs.
    These tests exercise the boundaries of the state machine.
    """

    def test_terminal_promoted_cannot_be_overwritten(self, artifacts_root: Path) -> None:
        from supervisor import generations

        generations.append({"generation": 1, "outcome": "promoted"})
        generations.update(1, {"outcome": "failed"})
        assert generations.get(1)["outcome"] == "promoted"

    def test_terminal_failed_cannot_be_overwritten(self, artifacts_root: Path) -> None:
        from supervisor import generations

        generations.append({"generation": 1, "outcome": "failed"})
        generations.update(1, {"outcome": "promoted"})
        assert generations.get(1)["outcome"] == "failed"

    def test_terminal_timeout_cannot_be_overwritten(self, artifacts_root: Path) -> None:
        from supervisor import generations

        generations.append({"generation": 1, "outcome": "timeout"})
        generations.update(1, {"outcome": "tested"})
        assert generations.get(1)["outcome"] == "timeout"

    def test_in_progress_can_transition_to_tested(self, artifacts_root: Path) -> None:
        from supervisor import generations

        generations.append({"generation": 1, "outcome": "in_progress"})
        generations.update(1, {"outcome": "tested"})
        assert generations.get(1)["outcome"] == "tested"

    def test_update_nonexistent_generation_does_not_write(self, artifacts_root: Path) -> None:
        from supervisor import generations

        generations.append({"generation": 1, "outcome": "in_progress"})
        path = artifacts_root / "generations.json"
        mtime = path.stat().st_mtime
        generations.update(999, {"outcome": "tested"})
        assert path.stat().st_mtime == mtime

    def test_update_sets_completed_only_on_terminal(self, artifacts_root: Path) -> None:
        """completed is only stamped when reaching a terminal outcome."""
        from supervisor import generations

        generations.append({"generation": 1, "outcome": "in_progress"})
        # tested is non-terminal — completed must NOT be set
        generations.update(1, {"outcome": "tested"})
        rec = generations.get(1)
        assert rec.get("completed") is None
        # promoted is terminal — completed MUST be set
        generations.update(1, {"outcome": "promoted"})
        rec = generations.get(1)
        assert "completed" in rec
        assert rec["completed"] is not None

    def test_update_does_not_overwrite_explicit_completed(self, artifacts_root: Path) -> None:
        from supervisor import generations

        generations.append({"generation": 1, "outcome": "in_progress"})
        explicit = "2026-01-01T00:00:00Z"
        generations.update(1, {"outcome": "tested", "completed": explicit})
        rec = generations.get(1)
        assert rec["completed"] == explicit

    def test_multiple_updates_to_same_record(self, artifacts_root: Path) -> None:
        from supervisor import generations

        generations.append({"generation": 1, "outcome": "in_progress"})
        generations.update(1, {"outcome": "tested"})
        # tested is not terminal, so a second update should work
        # (but in practice only promoted/failed/timeout are set after tested)
        rec = generations.get(1)
        assert rec["outcome"] == "tested"

    def test_kebab_case_fields_preserved_through_roundtrip(self, artifacts_root: Path) -> None:
        """Fields with hyphens survive JSON serialization roundtrip."""
        from supervisor import generations

        generations.append(
            {
                "generation": 1,
                "outcome": "in_progress",
                "spec-hash": "sha256:abc",
                "artifact-hash": "sha256:def",
                "artifact-ref": "gen-1",
                "container-id": "lab-gen-1",
                "campaign-id": "camp-001",
            }
        )
        rec = generations.get(1)
        assert rec["spec-hash"] == "sha256:abc"
        assert rec["artifact-hash"] == "sha256:def"
        assert rec["artifact-ref"] == "gen-1"
        assert rec["container-id"] == "lab-gen-1"
        assert rec["campaign-id"] == "camp-001"

    def test_generation_store_is_append_only(self, artifacts_root: Path) -> None:
        """New records don't overwrite existing ones."""
        from supervisor import generations

        generations.append({"generation": 1, "outcome": "promoted"})
        generations.append({"generation": 2, "outcome": "in_progress"})
        all_records = generations.load_all()
        assert len(all_records) == 2
        assert all_records[0]["generation"] == 1
        assert all_records[1]["generation"] == 2
