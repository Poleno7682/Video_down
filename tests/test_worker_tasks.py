from __future__ import annotations

from contextlib import contextmanager
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from app.db.models import DownloadRequest, DownloadStatus, TelegramFileType
from app.worker.tasks import _build_caption, _handle_task_failure, process_download_request


# ---------------------------------------------------------------------------
# _build_caption
# ---------------------------------------------------------------------------

class TestBuildCaption:
    def test_no_title(self):
        assert _build_caption(None, "720p") == "Готово ✅ | 720p"

    def test_with_title(self):
        result = _build_caption("My Video", "1080p")
        assert "My Video" in result
        assert "Готово ✅ | 1080p" in result

    def test_long_title_truncated(self):
        # 2000 chars guaranteed to exceed max_title for any quality string
        result = _build_caption("A" * 2000, "720p")
        assert len(result) <= 1024
        assert "…" in result

    def test_short_title_not_truncated(self):
        result = _build_caption("Short", "720p")
        assert "Short" in result
        assert "…" not in result

    def test_empty_string_title(self):
        assert _build_caption("", "audio") == "Готово ✅ | audio"


# ---------------------------------------------------------------------------
# _handle_task_failure
# ---------------------------------------------------------------------------

class TestHandleTaskFailure:
    def test_updates_status_and_notifies(self):
        repo = MagicMock()
        req = MagicMock()
        req.video_id = 1
        exc = ValueError("test error")

        with patch("app.worker.tasks.edit_status") as mock_edit:
            _handle_task_failure(repo, 42, req, exc)

        repo.update_request_status.assert_called_once_with(
            42, DownloadStatus.failed, error="ValueError: test error", finished=True
        )
        repo.mark_video_failed.assert_called_once_with(1, "ValueError: test error")
        mock_edit.assert_called_once()

    def test_no_video_id_skips_mark_failed(self):
        repo = MagicMock()
        req = MagicMock()
        req.video_id = None
        with patch("app.worker.tasks.edit_status"):
            _handle_task_failure(repo, 1, req, RuntimeError("oops"))
        repo.mark_video_failed.assert_not_called()

    def test_error_message_format(self):
        repo = MagicMock()
        req = MagicMock()
        req.video_id = None
        with patch("app.worker.tasks.edit_status"):
            _handle_task_failure(repo, 5, req, RuntimeError("bad network"))
        assert repo.update_request_status.call_args[1]["error"] == "RuntimeError: bad network"


# ---------------------------------------------------------------------------
# Fixtures / helpers for task tests
# ---------------------------------------------------------------------------

def _make_req(**kwargs):
    defaults = dict(
        id=1, user_id=10, chat_id=20, status_message_id=30,
        video_id=5, url_hash="abc123", quality="720p",
        normalized_url="https://youtube.com/watch?v=x",
    )
    defaults.update(kwargs)
    req = MagicMock(spec=DownloadRequest)
    for k, v in defaults.items():
        setattr(req, k, v)
    return req


def _make_settings(**kwargs):
    s = MagicMock()
    s.max_active_downloads_per_user = 1
    s.max_download_duration_seconds = 1800
    s.max_file_mb = 50
    s.delete_local_file_after_telegram_cache = True
    for k, v in kwargs.items():
        setattr(s, k, v)
    return s


def _make_session():
    session = MagicMock()
    session.__enter__ = MagicMock(return_value=session)
    session.__exit__ = MagicMock(return_value=False)
    return session


@contextmanager
def _task_ctx(req=None, settings=None, ready_video=None,
              slot_acquired=True, lock_acquired=True, download_result=None):
    """Patch all task externals and yield (repo, limiter)."""
    if settings is None:
        settings = _make_settings()
    if req is None:
        req = _make_req()

    session = _make_session()
    repo = MagicMock()
    repo.get_request.return_value = req
    repo.get_ready_video.return_value = ready_video

    limiter = MagicMock()
    limiter.acquire_user_download_slot.return_value = slot_acquired
    limiter.acquire_video_lock.return_value = lock_acquired

    fake_file = MagicMock(spec=Path)
    fake_file.stat.return_value.st_size = 10 * 1024 * 1024
    dl_result = download_result if download_result is not None else (fake_file, {"title": "T"})

    with patch("app.worker.tasks.get_settings", return_value=settings), \
         patch("app.worker.tasks.get_redis"), \
         patch("app.worker.tasks.RateLimiter", return_value=limiter), \
         patch("app.worker.tasks.get_session", return_value=session), \
         patch("app.worker.tasks.Repository", return_value=repo), \
         patch("app.worker.tasks.edit_status"), \
         patch("app.worker.tasks.send_cached"), \
         patch("app.worker.tasks.send_file", return_value=("fid", "uid", TelegramFileType.video)), \
         patch("app.worker.tasks.download_video", return_value=dl_result):
        yield repo, limiter


# ---------------------------------------------------------------------------
# process_download_request tests
# ---------------------------------------------------------------------------

class TestProcessDownloadRequest:

    def test_request_not_found(self):
        with _task_ctx() as (repo, _):
            repo.get_request.return_value = None
            process_download_request.apply(args=[999])
        repo.update_request_status.assert_not_called()

    def test_slot_not_acquired_sets_rate_limited(self):
        with _task_ctx(slot_acquired=False) as (repo, _):
            process_download_request.apply(args=[1])
        repo.update_request_status.assert_called_once_with(
            1, DownloadStatus.rate_limited,
            error="Too many active downloads for user", finished=True,
        )

    def test_slot_not_acquired_skips_lock(self):
        with _task_ctx(slot_acquired=False) as (repo, limiter):
            process_download_request.apply(args=[1])
        limiter.acquire_video_lock.assert_not_called()

    def test_slot_not_acquired_no_lock_release(self):
        with _task_ctx(slot_acquired=False) as (repo, limiter):
            process_download_request.apply(args=[1])
        limiter.release_video_lock.assert_not_called()

    def test_cache_hit_skips_lock_acquire(self):
        ready = MagicMock()
        ready.telegram_file_id = "fid"
        ready.telegram_file_type = TelegramFileType.video
        with _task_ctx(ready_video=ready) as (repo, limiter):
            process_download_request.apply(args=[1])
        limiter.acquire_video_lock.assert_not_called()

    def test_cache_hit_marks_done(self):
        ready = MagicMock()
        ready.telegram_file_id = "fid"
        ready.telegram_file_type = TelegramFileType.video
        with _task_ctx(ready_video=ready) as (repo, _):
            process_download_request.apply(args=[1])
        statuses = [c[0][1] for c in repo.update_request_status.call_args_list]
        assert DownloadStatus.done in statuses

    def test_cache_hit_calls_send_cached(self):
        ready = MagicMock()
        ready.telegram_file_id = "fid"
        ready.telegram_file_type = TelegramFileType.video
        with _task_ctx(ready_video=ready) as (repo, limiter):
            with patch("app.worker.tasks.send_cached") as mock_cached:
                process_download_request.apply(args=[1])
        mock_cached.assert_called_once()

    def test_video_lock_not_acquired_sets_rate_limited(self):
        with _task_ctx(lock_acquired=False) as (repo, _):
            process_download_request.apply(args=[1])
        statuses = {c[0][1] for c in repo.update_request_status.call_args_list}
        assert DownloadStatus.rate_limited in statuses

    def test_video_lock_not_acquired_releases_slot(self):
        req = _make_req()
        with _task_ctx(req=req, lock_acquired=False) as (repo, limiter):
            process_download_request.apply(args=[1])
        limiter.release_user_download_slot.assert_called_with(req.user_id)

    def test_successful_download_marks_video_ready(self):
        fake_file = MagicMock(spec=Path)
        fake_file.stat.return_value.st_size = 10 * 1024 * 1024
        with _task_ctx(download_result=(fake_file, {"title": "Vid"})) as (repo, _):
            process_download_request.apply(args=[1])
        repo.mark_video_ready.assert_called_once()

    def test_successful_download_marks_done(self):
        fake_file = MagicMock(spec=Path)
        fake_file.stat.return_value.st_size = 5 * 1024 * 1024
        with _task_ctx(download_result=(fake_file, {})) as (repo, _):
            process_download_request.apply(args=[1])
        statuses = [c[0][1] for c in repo.update_request_status.call_args_list]
        assert DownloadStatus.done in statuses

    def test_successful_download_deletes_file_when_flag_set(self):
        fake_file = MagicMock(spec=Path)
        fake_file.stat.return_value.st_size = 5 * 1024 * 1024
        settings = _make_settings(delete_local_file_after_telegram_cache=True)
        with _task_ctx(settings=settings, download_result=(fake_file, {})) as (repo, _):
            process_download_request.apply(args=[1])
        fake_file.unlink.assert_called_once_with(missing_ok=True)

    def test_no_delete_when_flag_false(self):
        fake_file = MagicMock(spec=Path)
        fake_file.stat.return_value.st_size = 5 * 1024 * 1024
        settings = _make_settings(delete_local_file_after_telegram_cache=False)
        with _task_ctx(settings=settings, download_result=(fake_file, {})) as (repo, _):
            process_download_request.apply(args=[1])
        fake_file.unlink.assert_not_called()

    def test_no_video_id_skips_mark_ready(self):
        req = _make_req(video_id=None)
        fake_file = MagicMock(spec=Path)
        fake_file.stat.return_value.st_size = 3 * 1024 * 1024
        with _task_ctx(req=req, download_result=(fake_file, {})) as (repo, _):
            process_download_request.apply(args=[1])
        repo.mark_video_ready.assert_not_called()

    def test_file_too_large_sets_too_large_status(self):
        big_file = MagicMock(spec=Path)
        big_file.stat.return_value.st_size = 100 * 1024 * 1024
        with _task_ctx(download_result=(big_file, {})) as (repo, _):
            process_download_request.apply(args=[1])
        statuses = {c[0][1] for c in repo.update_request_status.call_args_list}
        assert DownloadStatus.too_large in statuses

    def test_file_too_large_deletes_file(self):
        big_file = MagicMock(spec=Path)
        big_file.stat.return_value.st_size = 200 * 1024 * 1024
        with _task_ctx(download_result=(big_file, {})) as (repo, _):
            process_download_request.apply(args=[1])
        big_file.unlink.assert_called_once_with(missing_ok=True)

    def test_file_too_large_skips_mark_ready(self):
        big_file = MagicMock(spec=Path)
        big_file.stat.return_value.st_size = 200 * 1024 * 1024
        with _task_ctx(download_result=(big_file, {})) as (repo, _):
            process_download_request.apply(args=[1])
        repo.mark_video_ready.assert_not_called()

    def test_download_exception_calls_handle_failure(self):
        with _task_ctx() as (repo, limiter):
            with patch("app.worker.tasks.download_video", side_effect=RuntimeError("yt-dlp fail")), \
                 patch("app.worker.tasks._handle_task_failure") as mock_fail:
                with pytest.raises(RuntimeError, match="yt-dlp fail"):
                    process_download_request.apply(args=[1])
        mock_fail.assert_called_once()

    def test_download_exception_releases_slot(self):
        req = _make_req()
        with _task_ctx(req=req) as (repo, limiter):
            with patch("app.worker.tasks.download_video", side_effect=RuntimeError("fail")):
                with pytest.raises(RuntimeError):
                    process_download_request.apply(args=[1])
        limiter.release_user_download_slot.assert_called_with(req.user_id)

    def test_download_exception_releases_video_lock(self):
        with _task_ctx(lock_acquired=True) as (repo, limiter):
            with patch("app.worker.tasks.download_video", side_effect=RuntimeError("fail")):
                with pytest.raises(RuntimeError):
                    process_download_request.apply(args=[1])
        limiter.release_video_lock.assert_called_once()


# ---------------------------------------------------------------------------
# progress_hook closure (lines 116-126)
# ---------------------------------------------------------------------------

class TestProgressHook:
    """Tests for the progress_hook inner function defined inside process_download_request."""

    def _capture_hook(self):
        """Run the task once with a download_video mock that captures progress_hook."""
        captured = {}

        def capture(url, quality, settings, progress_hook=None):
            if progress_hook:
                captured["hook"] = progress_hook
            f = MagicMock(spec=Path)
            f.stat.return_value.st_size = 5 * 1024 * 1024
            return f, {}

        with _task_ctx() as (repo, _):
            with patch("app.worker.tasks.download_video", side_effect=capture), \
                 patch("app.worker.tasks.send_file", return_value=("fid", "uid", TelegramFileType.video)):
                process_download_request.apply(args=[1])

        return captured["hook"]

    def test_hook_reports_download_progress(self):
        hook = self._capture_hook()
        with patch("app.worker.tasks.time") as mock_time, \
             patch("app.worker.tasks.edit_status") as mock_edit:
            mock_time.time.return_value = 1000.0  # well past the 5-second throttle
            hook({"status": "downloading", "total_bytes": 200, "downloaded_bytes": 100})
        mock_edit.assert_called_once()
        call_text = mock_edit.call_args[0][2]
        assert "50.0%" in call_text

    def test_hook_skips_non_downloading_status(self):
        hook = self._capture_hook()
        with patch("app.worker.tasks.time") as mock_time, \
             patch("app.worker.tasks.edit_status") as mock_edit:
            mock_time.time.return_value = 1000.0
            hook({"status": "finished"})
        mock_edit.assert_not_called()

    def test_hook_throttled_when_called_too_soon(self):
        hook = self._capture_hook()
        with patch("app.worker.tasks.time") as mock_time, \
             patch("app.worker.tasks.edit_status") as mock_edit:
            # Simulate time just 1 second after the last update (< 5)
            mock_time.time.return_value = 1.0  # last_progress_update is 0.0, diff=1.0 < 5
            hook({"status": "downloading", "total_bytes": 100, "downloaded_bytes": 50})
        mock_edit.assert_not_called()

    def test_hook_skips_when_no_total_bytes(self):
        hook = self._capture_hook()
        with patch("app.worker.tasks.time") as mock_time, \
             patch("app.worker.tasks.edit_status") as mock_edit:
            mock_time.time.return_value = 1000.0
            hook({"status": "downloading", "total_bytes": None, "total_bytes_estimate": None,
                  "downloaded_bytes": 50})
        mock_edit.assert_not_called()

    def test_hook_uses_total_bytes_estimate(self):
        hook = self._capture_hook()
        with patch("app.worker.tasks.time") as mock_time, \
             patch("app.worker.tasks.edit_status") as mock_edit:
            mock_time.time.return_value = 1000.0
            hook({"status": "downloading", "total_bytes": None,
                  "total_bytes_estimate": 400, "downloaded_bytes": 100})
        mock_edit.assert_called_once()
        call_text = mock_edit.call_args[0][2]
        assert "25.0%" in call_text
