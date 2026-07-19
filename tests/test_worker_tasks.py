from __future__ import annotations

from contextlib import contextmanager
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from app.db.models import DownloadRequest, DownloadStatus, TelegramFileType
from app.utils.caption import DEFAULT_CAPTION, get_caption
from app.worker.tasks import (
    _COOKIE_FAILURE,
    _STALE_COOKIE_FAILURE,
    _GENERIC_FAILURE,
    _is_cookie_error,
    _is_youtube_challenge_error,
    _materialize_user_cookies,
    _handle_task_failure,
    process_download_request,
)


# ---------------------------------------------------------------------------
# get_caption
# ---------------------------------------------------------------------------

class TestGetCaption:
    def _settings(self, path):
        s = MagicMock()
        s.caption_file = path
        return s

    def test_reads_text_from_file(self, tmp_path):
        f = tmp_path / "caption.txt"
        f.write_text("Спасибо за использование @fbtt_download_bot", encoding="utf-8")
        assert get_caption(self._settings(f)) == "Спасибо за использование @fbtt_download_bot"

    def test_strips_whitespace(self, tmp_path):
        f = tmp_path / "caption.txt"
        f.write_text("\n  Привет  \n", encoding="utf-8")
        assert get_caption(self._settings(f)) == "Привет"

    def test_missing_file_falls_back_to_default(self, tmp_path):
        assert get_caption(self._settings(tmp_path / "nope.txt")) == DEFAULT_CAPTION

    def test_empty_file_falls_back_to_default(self, tmp_path):
        f = tmp_path / "caption.txt"
        f.write_text("   \n", encoding="utf-8")
        assert get_caption(self._settings(f)) == DEFAULT_CAPTION

    def test_truncated_to_telegram_limit(self, tmp_path):
        f = tmp_path / "caption.txt"
        f.write_text("A" * 2000, encoding="utf-8")
        assert len(get_caption(self._settings(f))) == 1024


# ---------------------------------------------------------------------------
# _handle_task_failure
# ---------------------------------------------------------------------------

class TestHandleTaskFailure:
    def test_updates_status_and_notifies(self):
        repo = MagicMock()
        sender = MagicMock()
        exc = ValueError("test error")

        _handle_task_failure(sender, repo, 42, 100, 200, 1, exc)

        repo.update_request_status.assert_called_once_with(
            42, DownloadStatus.failed, error="ValueError: test error", finished=True
        )
        repo.mark_video_failed.assert_called_once_with(1, "ValueError: test error")
        sender.edit_status.assert_called_once()

    def test_no_video_id_skips_mark_failed(self):
        repo = MagicMock()
        sender = MagicMock()
        _handle_task_failure(sender, repo, 1, 100, 200, None, RuntimeError("oops"))
        repo.mark_video_failed.assert_not_called()

    def test_error_message_format(self):
        repo = MagicMock()
        sender = MagicMock()
        _handle_task_failure(sender, repo, 5, 100, 200, None, RuntimeError("bad network"))
        assert repo.update_request_status.call_args[1]["error"] == "RuntimeError: bad network"

    def test_cookie_error_shows_cookie_message(self):
        repo = MagicMock()
        sender = MagicMock()
        exc = RuntimeError("ERROR: Sign in to confirm you're not a bot. Use --cookies")
        _handle_task_failure(sender, repo, 1, 100, 200, None, exc)
        assert sender.edit_status.call_args[0][2] == _COOKIE_FAILURE

    def test_stale_cookie_error_when_cookies_were_used(self):
        repo = MagicMock()
        sender = MagicMock()
        exc = RuntimeError("ERROR: Sign in to confirm you're not a bot. Use --cookies")
        _handle_task_failure(sender, repo, 1, 100, 200, None, exc, cookies_were_used=True)
        assert sender.edit_status.call_args[0][2] == _STALE_COOKIE_FAILURE

    def test_generic_error_shows_generic_message(self):
        repo = MagicMock()
        sender = MagicMock()
        _handle_task_failure(sender, repo, 1, 100, 200, None, RuntimeError("disk full"))
        assert sender.edit_status.call_args[0][2] == _GENERIC_FAILURE

    def test_db_write_failure_still_notifies_user(self):
        """If persisting the failure itself throws (DB still down), the
        Telegram notification — which needs no DB access — must still fire."""
        repo = MagicMock()
        sender = MagicMock()
        repo.update_request_status.side_effect = RuntimeError("db still down")
        _handle_task_failure(sender, repo, 1, 100, 200, None, RuntimeError("original error"))
        sender.edit_status.assert_called_once_with(100, 200, _GENERIC_FAILURE)


class TestIsCookieError:
    def test_sign_in_marker(self):
        assert _is_cookie_error(RuntimeError("Sign in to confirm you're not a bot")) is True

    def test_cookies_flag_marker(self):
        assert _is_cookie_error(RuntimeError("Use --cookies for authentication")) is True

    def test_unrelated_error(self):
        assert _is_cookie_error(RuntimeError("Network unreachable")) is False


class TestIsYoutubeChallengeError:
    def test_format_not_available(self):
        exc = RuntimeError("[youtube] abc: Requested format is not available.")
        assert _is_youtube_challenge_error(exc) is True

    def test_challenge_marker(self):
        assert _is_youtube_challenge_error(RuntimeError("n challenge solving failed")) is True

    def test_unrelated_error(self):
        assert _is_youtube_challenge_error(RuntimeError("Network unreachable")) is False


class TestMaterializeUserCookies:
    def test_unknown_platform_returns_none(self):
        repo = MagicMock()
        assert _materialize_user_cookies(repo, 1, "https://vimeo.com/1") is None
        repo.get_user_cookies.assert_not_called()

    def test_no_cookies_returns_none(self):
        repo = MagicMock()
        repo.get_user_cookies.return_value = None
        assert _materialize_user_cookies(repo, 1, "https://youtube.com/watch?v=x") is None

    def test_writes_temp_file(self):
        repo = MagicMock()
        repo.get_user_cookies.return_value = "# Netscape HTTP Cookie File\n"
        path = _materialize_user_cookies(repo, 1, "https://youtu.be/x")
        try:
            assert path is not None
            assert path.exists()
            assert path.read_text(encoding="utf-8") == "# Netscape HTTP Cookie File\n"
        finally:
            if path is not None:
                path.unlink(missing_ok=True)


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


def _make_sender():
    sender = MagicMock()
    sender.send_file.return_value = ("fid", "uid", TelegramFileType.video)
    return sender


@contextmanager
def _task_ctx(req=None, settings=None, ready_video=None,
              slot_acquired=True, lock_acquired=True, download_result=None, sender=None):
    """Patch all task externals and yield (repo, limiter, sender)."""
    if settings is None:
        settings = _make_settings()
    if req is None:
        req = _make_req()
    if sender is None:
        sender = _make_sender()

    session = _make_session()
    repo = MagicMock()
    repo.get_request.return_value = req
    repo.get_ready_video.return_value = ready_video

    repo.get_user_cookies.return_value = None

    limiter = MagicMock()
    limiter.acquire_user_download_slot.return_value = slot_acquired
    limiter.acquire_video_lock.return_value = lock_acquired

    fake_file = MagicMock(spec=Path)
    fake_file.stat.return_value.st_size = 10 * 1024 * 1024
    dl_result = download_result if download_result is not None else (fake_file, {"title": "T"})

    redis_mock = MagicMock()
    redis_mock.get.return_value = None  # no runtime_config overrides in tests

    with patch("app.worker.tasks.get_settings", return_value=settings), \
         patch("app.worker.tasks.get_redis", return_value=redis_mock), \
         patch("app.worker.tasks.RateLimiter", return_value=limiter), \
         patch("app.worker.tasks.get_session", return_value=session), \
         patch("app.worker.tasks.Repository", return_value=repo), \
         patch("app.worker.tasks.get_default_sender", return_value=sender), \
         patch("app.worker.tasks.is_active_livestream", return_value=False), \
         patch("app.worker.tasks.validate_media_file"), \
         patch("app.worker.tasks.download_video", return_value=dl_result):
        yield repo, limiter, sender


# ---------------------------------------------------------------------------
# process_download_request tests
# ---------------------------------------------------------------------------

class TestProcessDownloadRequest:

    def test_request_not_found(self):
        with _task_ctx() as (repo, _, _sender):
            repo.get_request.return_value = None
            process_download_request.apply(args=[999])
        repo.update_request_status.assert_not_called()

    def test_slot_not_acquired_sets_rate_limited(self):
        with _task_ctx(slot_acquired=False) as (repo, _, _sender):
            process_download_request.apply(args=[1])
        repo.update_request_status.assert_called_once_with(
            1, DownloadStatus.rate_limited,
            error="Too many active downloads for user", finished=True,
        )

    def test_slot_not_acquired_skips_lock(self):
        with _task_ctx(slot_acquired=False) as (repo, limiter, _sender):
            process_download_request.apply(args=[1])
        limiter.acquire_video_lock.assert_not_called()

    def test_slot_not_acquired_no_lock_release(self):
        with _task_ctx(slot_acquired=False) as (repo, limiter, _sender):
            process_download_request.apply(args=[1])
        limiter.release_video_lock.assert_not_called()

    def test_cache_hit_skips_lock_acquire(self):
        ready = MagicMock()
        ready.telegram_file_id = "fid"
        ready.telegram_file_type = TelegramFileType.video
        with _task_ctx(ready_video=ready) as (repo, limiter, _sender):
            process_download_request.apply(args=[1])
        limiter.acquire_video_lock.assert_not_called()

    def test_cache_hit_marks_done(self):
        ready = MagicMock()
        ready.telegram_file_id = "fid"
        ready.telegram_file_type = TelegramFileType.video
        with _task_ctx(ready_video=ready) as (repo, _, _sender):
            process_download_request.apply(args=[1])
        statuses = [c[0][1] for c in repo.update_request_status.call_args_list]
        assert DownloadStatus.done in statuses

    def test_cache_hit_calls_send_cached(self):
        ready = MagicMock()
        ready.telegram_file_id = "fid"
        ready.telegram_file_type = TelegramFileType.video
        with _task_ctx(ready_video=ready) as (repo, limiter, sender):
            process_download_request.apply(args=[1])
        sender.send_cached.assert_called_once()

    def test_video_lock_not_acquired_sets_rate_limited(self):
        with _task_ctx(lock_acquired=False) as (repo, _, _sender):
            process_download_request.apply(args=[1])
        statuses = {c[0][1] for c in repo.update_request_status.call_args_list}
        assert DownloadStatus.rate_limited in statuses

    def test_video_lock_not_acquired_releases_slot(self):
        req = _make_req()
        with _task_ctx(req=req, lock_acquired=False) as (repo, limiter, _sender):
            process_download_request.apply(args=[1])
        limiter.release_user_download_slot.assert_called_with(req.user_id)

    def test_successful_download_marks_video_ready(self):
        fake_file = MagicMock(spec=Path)
        fake_file.stat.return_value.st_size = 10 * 1024 * 1024
        with _task_ctx(download_result=(fake_file, {"title": "Vid"})) as (repo, _, _sender):
            process_download_request.apply(args=[1])
        repo.mark_video_ready.assert_called_once()

    def test_successful_download_logs_media_debug_info(self):
        fake_file = MagicMock(spec=Path)
        fake_file.stat.return_value.st_size = 10 * 1024 * 1024
        with _task_ctx(download_result=(fake_file, {"title": "Vid"})) as (repo, _, _sender):
            with patch("app.worker.tasks.log_media_debug_info") as mock_log:
                process_download_request.apply(args=[1])
        mock_log.assert_called_once()
        assert mock_log.call_args[0][0] is fake_file
        assert "request=1" in mock_log.call_args.kwargs["context"]

    def test_risky_codec_gets_transcoded_before_upload(self):
        fake_file = MagicMock(spec=Path)
        fake_file.stat.return_value.st_size = 10 * 1024 * 1024
        transcoded_file = MagicMock(spec=Path)
        transcoded_file.stat.return_value.st_size = 12 * 1024 * 1024

        with _task_ctx(download_result=(fake_file, {"title": "Vid"})) as (repo, _, _sender):
            with patch("app.worker.tasks.log_media_debug_info", return_value={"video": "av1", "audio": "aac"}), \
                 patch("app.worker.tasks.ensure_telegram_compatible_video", return_value=transcoded_file) as mock_ensure:
                process_download_request.apply(args=[1])

        mock_ensure.assert_called_once_with(fake_file, {"video": "av1", "audio": "aac"})
        transcoded_file.unlink.assert_called()  # uploaded then cleaned up

    def test_audio_quality_skips_telegram_compat_transcode(self):
        req = _make_req(quality="audio")
        fake_file = MagicMock(spec=Path)
        fake_file.stat.return_value.st_size = 5 * 1024 * 1024

        with _task_ctx(req=req, download_result=(fake_file, {})) as (repo, _, _sender):
            with patch("app.worker.tasks.ensure_telegram_compatible_video") as mock_ensure:
                process_download_request.apply(args=[1])

        mock_ensure.assert_not_called()

    def test_user_cookies_passed_to_download(self):
        fake_file = MagicMock(spec=Path)
        fake_file.stat.return_value.st_size = 5 * 1024 * 1024
        captured = {}

        def capture(url, quality, settings, progress_hook=None, cookie_file=None, embed_subtitles=False):
            captured["cookie_file"] = cookie_file
            return fake_file, {}

        with _task_ctx() as (repo, _, _sender):
            repo.get_user_cookies.return_value = "# Netscape HTTP Cookie File\n"
            with patch("app.worker.tasks.download_video", side_effect=capture):
                process_download_request.apply(args=[1])

        assert captured["cookie_file"] is not None
        # Temp cookie file must be cleaned up afterwards.
        assert not captured["cookie_file"].exists()

    def test_successful_download_marks_done(self):
        fake_file = MagicMock(spec=Path)
        fake_file.stat.return_value.st_size = 5 * 1024 * 1024
        with _task_ctx(download_result=(fake_file, {})) as (repo, _, _sender):
            process_download_request.apply(args=[1])
        statuses = [c[0][1] for c in repo.update_request_status.call_args_list]
        assert DownloadStatus.done in statuses

    def test_successful_download_deletes_file_when_flag_set(self):
        fake_file = MagicMock(spec=Path)
        fake_file.stat.return_value.st_size = 5 * 1024 * 1024
        settings = _make_settings(delete_local_file_after_telegram_cache=True)
        with _task_ctx(settings=settings, download_result=(fake_file, {})) as (repo, _, _sender):
            process_download_request.apply(args=[1])
        fake_file.unlink.assert_called_once_with(missing_ok=True)

    def test_no_delete_when_flag_false(self):
        fake_file = MagicMock(spec=Path)
        fake_file.stat.return_value.st_size = 5 * 1024 * 1024
        settings = _make_settings(delete_local_file_after_telegram_cache=False)
        with _task_ctx(settings=settings, download_result=(fake_file, {})) as (repo, _, _sender):
            process_download_request.apply(args=[1])
        fake_file.unlink.assert_not_called()

    def test_no_video_id_skips_mark_ready(self):
        req = _make_req(video_id=None)
        fake_file = MagicMock(spec=Path)
        fake_file.stat.return_value.st_size = 3 * 1024 * 1024
        with _task_ctx(req=req, download_result=(fake_file, {})) as (repo, _, _sender):
            process_download_request.apply(args=[1])
        repo.mark_video_ready.assert_not_called()

    def test_file_too_large_sets_too_large_status(self):
        big_file = MagicMock(spec=Path)
        big_file.stat.return_value.st_size = 100 * 1024 * 1024
        with _task_ctx(download_result=(big_file, {})) as (repo, _, _sender):
            process_download_request.apply(args=[1])
        statuses = {c[0][1] for c in repo.update_request_status.call_args_list}
        assert DownloadStatus.too_large in statuses

    def test_file_too_large_deletes_file(self):
        big_file = MagicMock(spec=Path)
        big_file.stat.return_value.st_size = 200 * 1024 * 1024
        with _task_ctx(download_result=(big_file, {})) as (repo, _, _sender):
            process_download_request.apply(args=[1])
        big_file.unlink.assert_called_once_with(missing_ok=True)

    def test_file_too_large_skips_mark_ready(self):
        big_file = MagicMock(spec=Path)
        big_file.stat.return_value.st_size = 200 * 1024 * 1024
        with _task_ctx(download_result=(big_file, {})) as (repo, _, _sender):
            process_download_request.apply(args=[1])
        repo.mark_video_ready.assert_not_called()

    def test_active_livestream_rejected_before_download(self):
        with _task_ctx() as (repo, _, _sender):
            with patch("app.worker.tasks.is_active_livestream", return_value=True) as mock_live, \
                 patch("app.worker.tasks.download_video") as mock_download:
                process_download_request.apply(args=[1])
        mock_live.assert_called_once()
        mock_download.assert_not_called()
        statuses = {c[0][1] for c in repo.update_request_status.call_args_list}
        assert DownloadStatus.failed in statuses

    def test_corrupt_media_marks_failed(self):
        from app.worker.downloader import MediaValidationError

        with _task_ctx() as (repo, _, _sender):
            with patch("app.worker.tasks.validate_media_file", side_effect=MediaValidationError("bad")):
                with pytest.raises(MediaValidationError):
                    process_download_request.apply(args=[1])
        statuses = {c[0][1] for c in repo.update_request_status.call_args_list}
        assert DownloadStatus.failed in statuses

    def test_oversized_file_gets_compressed_under_limit(self):
        big_file = MagicMock(spec=Path)
        big_file.stat.return_value.st_size = 100 * 1024 * 1024
        small_file = MagicMock(spec=Path)
        small_file.stat.return_value.st_size = 10 * 1024 * 1024

        with _task_ctx(download_result=(big_file, {})) as (repo, _, _sender):
            with patch("app.worker.tasks.compress_to_size_limit", return_value=small_file) as mock_compress:
                process_download_request.apply(args=[1])

        mock_compress.assert_called_once()
        statuses = {c[0][1] for c in repo.update_request_status.call_args_list}
        assert DownloadStatus.too_large not in statuses
        assert DownloadStatus.done in statuses
        big_file.unlink.assert_called_once_with(missing_ok=True)

    def test_compression_failure_still_reports_too_large(self):
        big_file = MagicMock(spec=Path)
        big_file.stat.return_value.st_size = 100 * 1024 * 1024

        with _task_ctx(download_result=(big_file, {})) as (repo, _, _sender):
            with patch("app.worker.tasks.compress_to_size_limit", return_value=None) as mock_compress:
                process_download_request.apply(args=[1])

        mock_compress.assert_called_once()
        statuses = {c[0][1] for c in repo.update_request_status.call_args_list}
        assert DownloadStatus.too_large in statuses

    def test_download_exception_calls_handle_failure(self):
        with _task_ctx() as (repo, limiter, _sender):
            with patch("app.worker.tasks.download_video", side_effect=RuntimeError("yt-dlp fail")), \
                 patch("app.worker.tasks._handle_task_failure") as mock_fail:
                with pytest.raises(RuntimeError, match="yt-dlp fail"):
                    process_download_request.apply(args=[1])
        mock_fail.assert_called_once()

    def test_download_exception_releases_slot(self):
        req = _make_req()
        with _task_ctx(req=req) as (repo, limiter, _sender):
            with patch("app.worker.tasks.download_video", side_effect=RuntimeError("fail")):
                with pytest.raises(RuntimeError):
                    process_download_request.apply(args=[1])
        limiter.release_user_download_slot.assert_called_with(req.user_id)

    def test_download_exception_releases_video_lock(self):
        with _task_ctx(lock_acquired=True) as (repo, limiter, _sender):
            with patch("app.worker.tasks.download_video", side_effect=RuntimeError("fail")):
                with pytest.raises(RuntimeError):
                    process_download_request.apply(args=[1])
        limiter.release_video_lock.assert_called_once()

    def test_db_commit_failure_during_upload_still_cleans_up(self):
        """Reproduces a transient DB blip (e.g. Postgres SSL connection drop)
        during _upload_and_cache's status update. Before the fix, the
        SQLAlchemy session would be left in "pending rollback" state and any
        subsequent lazy-load of req.user_id/req.url_hash inside the except/
        finally blocks raised PendingRollbackError — masking the original
        error, skipping the user notification, and leaking the download slot
        and video lock for the full rate-limit window."""
        req = _make_req()
        settings = _make_settings()
        session = _make_session()
        repo = MagicMock()
        repo.get_request.return_value = req
        repo.get_ready_video.return_value = None
        repo.get_user_cookies.return_value = None
        db_error = RuntimeError("SSL connection has been closed unexpectedly")
        # 1st call = DownloadStatus.downloading (succeeds), 2nd call =
        # DownloadStatus.sending inside _upload_and_cache — this is exactly
        # where the real incident's traceback failed.
        repo.update_request_status.side_effect = [None, db_error]

        limiter = MagicMock()
        limiter.acquire_user_download_slot.return_value = True
        limiter.acquire_video_lock.return_value = True

        fake_file = MagicMock(spec=Path)
        fake_file.stat.return_value.st_size = 5 * 1024 * 1024

        redis_mock = MagicMock()
        redis_mock.get.return_value = None
        redis_mock.exists.return_value = False

        sender = _make_sender()

        with patch("app.worker.tasks.get_settings", return_value=settings), \
             patch("app.worker.tasks.get_redis", return_value=redis_mock), \
             patch("app.worker.tasks.RateLimiter", return_value=limiter), \
             patch("app.worker.tasks.get_session", return_value=session), \
             patch("app.worker.tasks.Repository", return_value=repo), \
             patch("app.worker.tasks.get_default_sender", return_value=sender), \
             patch("app.worker.tasks.is_active_livestream", return_value=False), \
             patch("app.worker.tasks.validate_media_file"), \
             patch("app.worker.tasks.log_media_debug_info", return_value={}), \
             patch("app.worker.tasks.download_video", return_value=(fake_file, {"title": "T"})):
            with pytest.raises(RuntimeError, match="SSL connection"):
                process_download_request.apply(args=[1])

        # Session must be rolled back before any further DB access is attempted.
        session.rollback.assert_called_once()
        # Cleanup must use the plain values snapshotted up front, not a
        # lazy-load through the now-recovering session.
        limiter.release_user_download_slot.assert_called_once_with(req.user_id)
        limiter.release_video_lock.assert_called_once_with(req.url_hash, req.quality)
        # The user must still be notified despite the DB failure.
        sender.edit_status.assert_called_with(req.chat_id, req.status_message_id, _GENERIC_FAILURE)


# ---------------------------------------------------------------------------
# progress_hook closure (lines 116-126)
# ---------------------------------------------------------------------------

class TestProgressHook:
    """Tests for the progress_hook inner function defined inside process_download_request."""

    def _capture_hook(self):
        """Run the task once with a download_video mock that captures progress_hook."""
        captured = {}

        def capture(url, quality, settings, progress_hook=None, cookie_file=None, embed_subtitles=False):
            if progress_hook:
                captured["hook"] = progress_hook
            f = MagicMock(spec=Path)
            f.stat.return_value.st_size = 5 * 1024 * 1024
            return f, {}

        sender = _make_sender()
        with _task_ctx(sender=sender) as (repo, _, _sender):
            with patch("app.worker.tasks.download_video", side_effect=capture):
                process_download_request.apply(args=[1])

        return captured["hook"], sender

    def test_hook_reports_download_progress(self):
        hook, sender = self._capture_hook()
        sender.edit_status.reset_mock()
        with patch("app.worker.tasks.time") as mock_time:
            mock_time.time.return_value = 1000.0  # well past the 5-second throttle
            hook({"status": "downloading", "total_bytes": 200, "downloaded_bytes": 100})
        sender.edit_status.assert_called_once()
        call_text = sender.edit_status.call_args[0][2]
        assert "50.0%" in call_text

    def test_hook_skips_non_downloading_status(self):
        hook, sender = self._capture_hook()
        sender.edit_status.reset_mock()
        with patch("app.worker.tasks.time") as mock_time:
            mock_time.time.return_value = 1000.0
            hook({"status": "finished"})
        sender.edit_status.assert_not_called()

    def test_hook_throttled_when_called_too_soon(self):
        hook, sender = self._capture_hook()
        sender.edit_status.reset_mock()
        with patch("app.worker.tasks.time") as mock_time:
            # Simulate time just 1 second after the last update (< 5)
            mock_time.time.return_value = 1.0  # last_progress_update is 0.0, diff=1.0 < 5
            hook({"status": "downloading", "total_bytes": 100, "downloaded_bytes": 50})
        sender.edit_status.assert_not_called()

    def test_hook_skips_when_no_total_bytes(self):
        hook, sender = self._capture_hook()
        sender.edit_status.reset_mock()
        with patch("app.worker.tasks.time") as mock_time:
            mock_time.time.return_value = 1000.0
            hook({"status": "downloading", "total_bytes": None, "total_bytes_estimate": None,
                  "downloaded_bytes": 50})
        sender.edit_status.assert_not_called()

    def test_hook_uses_total_bytes_estimate(self):
        hook, sender = self._capture_hook()
        sender.edit_status.reset_mock()
        with patch("app.worker.tasks.time") as mock_time:
            mock_time.time.return_value = 1000.0
            hook({"status": "downloading", "total_bytes": None,
                  "total_bytes_estimate": 400, "downloaded_bytes": 100})
        sender.edit_status.assert_called_once()
        call_text = sender.edit_status.call_args[0][2]
        assert "25.0%" in call_text
