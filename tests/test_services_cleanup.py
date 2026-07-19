from __future__ import annotations

import time
from pathlib import Path
from unittest.mock import MagicMock

from app.services.cleanup import cleanup_stale_downloads


def _make_settings(download_dir: Path, max_age_hours: float = 24):
    settings = MagicMock()
    settings.download_dir = download_dir
    settings.stale_file_max_age_hours = max_age_hours
    return settings


def _touch_with_mtime(path: Path, age_seconds: float) -> None:
    path.write_bytes(b"x")
    mtime = time.time() - age_seconds
    import os

    os.utime(path, (mtime, mtime))


class TestCleanupStaleDownloads:
    def test_no_active_dir_returns_zero(self, tmp_path):
        settings = _make_settings(tmp_path)
        assert cleanup_stale_downloads(settings) == 0

    def test_removes_old_file_keeps_fresh_file(self, tmp_path):
        active = tmp_path / "active"
        active.mkdir()
        old_file = active / "old.mp4"
        fresh_file = active / "fresh.mp4"
        _touch_with_mtime(old_file, age_seconds=48 * 3600)
        _touch_with_mtime(fresh_file, age_seconds=1 * 3600)

        settings = _make_settings(tmp_path, max_age_hours=24)
        removed = cleanup_stale_downloads(settings)

        assert removed == 1
        assert not old_file.exists()
        assert fresh_file.exists()

    def test_removes_old_leftover_work_directory(self, tmp_path):
        """A per-download uuid subdirectory that survived a crash (normally
        rmtree'd in download_video's finally) must be removed too, not just
        plain files."""
        active = tmp_path / "active"
        active.mkdir()
        stale_dir = active / "abc123"
        stale_dir.mkdir()
        (stale_dir / "partial.mp4").write_bytes(b"x")
        old_mtime = time.time() - 48 * 3600
        import os
        os.utime(stale_dir, (old_mtime, old_mtime))

        settings = _make_settings(tmp_path, max_age_hours=24)
        removed = cleanup_stale_downloads(settings)

        assert removed == 1
        assert not stale_dir.exists()

    def test_max_age_zero_removes_everything(self, tmp_path):
        active = tmp_path / "active"
        active.mkdir()
        f = active / "anything.mp4"
        _touch_with_mtime(f, age_seconds=1)  # just created

        settings = _make_settings(tmp_path, max_age_hours=24)
        removed = cleanup_stale_downloads(settings, max_age_hours=0)

        assert removed == 1
        assert not f.exists()

    def test_uses_settings_default_when_max_age_not_given(self, tmp_path):
        active = tmp_path / "active"
        active.mkdir()
        f = active / "recent.mp4"
        _touch_with_mtime(f, age_seconds=2 * 3600)

        settings = _make_settings(tmp_path, max_age_hours=1)
        removed = cleanup_stale_downloads(settings)  # no explicit max_age_hours

        assert removed == 1
        assert not f.exists()

    def test_empty_active_dir_returns_zero(self, tmp_path):
        active = tmp_path / "active"
        active.mkdir()
        settings = _make_settings(tmp_path)
        assert cleanup_stale_downloads(settings) == 0
