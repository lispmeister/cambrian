"""Unit tests for prime_runner security boundaries."""

from pathlib import Path

import pytest


class TestResolveArtifactPath:
    def test_allows_relative_path(self, tmp_path: Path) -> None:
        from supervisor.prime_runner import _resolve_artifact_path

        root = tmp_path / "artifact"
        root.mkdir()
        resolved = _resolve_artifact_path(root, "src/main.py")
        assert resolved == (root / "src" / "main.py").resolve()

    def test_rejects_absolute_path(self, tmp_path: Path) -> None:
        from supervisor.prime_runner import _resolve_artifact_path

        root = tmp_path / "artifact"
        root.mkdir()
        with pytest.raises(ValueError):
            _resolve_artifact_path(root, "/etc/passwd")

    def test_rejects_traversal_path(self, tmp_path: Path) -> None:
        from supervisor.prime_runner import _resolve_artifact_path

        root = tmp_path / "artifact"
        root.mkdir()
        with pytest.raises(ValueError):
            _resolve_artifact_path(root, "../outside.txt")

    def test_allows_normalized_relative_path(self, tmp_path: Path) -> None:
        from supervisor.prime_runner import _resolve_artifact_path

        root = tmp_path / "artifact"
        root.mkdir()
        resolved = _resolve_artifact_path(root, "src/../src/app.py")
        assert resolved == (root / "src" / "app.py").resolve()
