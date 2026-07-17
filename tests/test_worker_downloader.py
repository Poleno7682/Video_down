from __future__ import annotations

import subprocess
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from yt_dlp.utils import DownloadError

from app.worker.downloader import (
    MediaValidationError,
    _YtdlpLogger,
    _build_ydl_opts,
    _burn_subtitles,
    _embed_subtitles_if_present,
    _extract_with_retry,
    _is_access_retry_error,
    _probe_duration_seconds,
    _probe_stream_types,
    _select_output_file,
    _select_subtitle_file,
    _streams_are_decodable,
    compress_to_size_limit,
    cookie_file_for_url,
    download_video,
    is_active_livestream,
    probe_video_dimensions,
    validate_media_file,
)


# ---------------------------------------------------------------------------
# _YtdlpLogger
# ---------------------------------------------------------------------------

def test_ytdlp_logger_debug(caplog):
    import logging
    with caplog.at_level(logging.DEBUG, logger="app.worker.downloader"):
        _YtdlpLogger().debug("debug msg")
    assert "debug msg" in caplog.text


def test_ytdlp_logger_info(caplog):
    import logging
    with caplog.at_level(logging.INFO, logger="app.worker.downloader"):
        _YtdlpLogger().info("info msg")
    assert "info msg" in caplog.text


def test_ytdlp_logger_warning(caplog):
    import logging
    with caplog.at_level(logging.WARNING, logger="app.worker.downloader"):
        _YtdlpLogger().warning("warn msg")
    assert "warn msg" in caplog.text


def test_ytdlp_logger_error(caplog):
    import logging
    with caplog.at_level(logging.ERROR, logger="app.worker.downloader"):
        _YtdlpLogger().error("err msg")
    assert "err msg" in caplog.text


# ---------------------------------------------------------------------------
# cookie_file_for_url
# ---------------------------------------------------------------------------

def _make_settings(use_cookies=True, **kwargs):
    s = MagicMock()
    s.use_cookies = use_cookies
    s.facebook_cookies_file = MagicMock(spec=Path)
    s.instagram_cookies_file = MagicMock(spec=Path)
    s.tiktok_cookies_file = MagicMock(spec=Path)
    for k, v in kwargs.items():
        setattr(s, k, v)
    return s


def test_cookie_file_use_cookies_false():
    s = _make_settings(use_cookies=False)
    assert cookie_file_for_url("https://facebook.com/video", s) is None


def test_cookie_file_facebook_exists():
    s = _make_settings()
    s.facebook_cookies_file.exists.return_value = True
    result = cookie_file_for_url("https://facebook.com/video", s)
    assert result is s.facebook_cookies_file


def test_cookie_file_facebook_missing():
    s = _make_settings()
    s.facebook_cookies_file.exists.return_value = False
    assert cookie_file_for_url("https://facebook.com/video", s) is None


def test_cookie_file_fb_watch():
    s = _make_settings()
    s.facebook_cookies_file.exists.return_value = True
    result = cookie_file_for_url("https://fb.watch/abc", s)
    assert result is s.facebook_cookies_file


def test_cookie_file_instagram():
    s = _make_settings()
    s.instagram_cookies_file.exists.return_value = True
    result = cookie_file_for_url("https://instagram.com/p/abc", s)
    assert result is s.instagram_cookies_file


def test_cookie_file_tiktok():
    s = _make_settings()
    s.tiktok_cookies_file.exists.return_value = True
    result = cookie_file_for_url("https://tiktok.com/@user/video/123", s)
    assert result is s.tiktok_cookies_file


def test_cookie_file_youtube():
    s = _make_settings()
    s.youtube_cookies_file.exists.return_value = True
    result = cookie_file_for_url("https://youtube.com/watch?v=abc", s)
    assert result is s.youtube_cookies_file


def test_cookie_file_youtu_be():
    s = _make_settings()
    s.youtube_cookies_file.exists.return_value = True
    result = cookie_file_for_url("https://youtu.be/abc", s)
    assert result is s.youtube_cookies_file


def test_cookie_file_unknown_domain():
    s = _make_settings()
    assert cookie_file_for_url("https://vimeo.com/123456", s) is None


# ---------------------------------------------------------------------------
# _build_ydl_opts
# ---------------------------------------------------------------------------

def test_build_ydl_opts_basic():
    work_dir = Path("/tmp")
    opts = _build_ydl_opts("720p", work_dir, None, None)
    assert "outtmpl" in opts
    assert opts["noplaylist"] is True
    assert opts["progress_hooks"] == []
    assert opts["postprocessors"] == []
    assert opts["remote_components"] == ["ejs:github"]
    assert opts["postprocessor_args"] == {"Merger+ffmpeg": ["-c", "copy", "-movflags", "+faststart"]}
    assert "cookiefile" not in opts


def test_build_ydl_opts_audio_postprocessor():
    opts = _build_ydl_opts("audio", Path("/tmp"), None, None)
    assert any(p["key"] == "FFmpegExtractAudio" for p in opts["postprocessors"])
    assert opts["merge_output_format"] == "m4a"


def test_build_ydl_opts_video_merge_format():
    opts = _build_ydl_opts("720p", Path("/tmp"), None, None)
    assert opts["merge_output_format"] == "mp4"


def test_build_ydl_opts_with_cookie_file():
    cookie = Path("/cookies/fb.txt")
    opts = _build_ydl_opts("720p", Path("/tmp"), None, cookie)
    assert opts["cookiefile"] == str(cookie)


def test_build_ydl_opts_with_progress_hook():
    hook = MagicMock()
    opts = _build_ydl_opts("720p", Path("/tmp"), hook, None)
    assert opts["progress_hooks"] == [hook]


def test_build_ydl_opts_has_logger():
    opts = _build_ydl_opts("720p", Path("/tmp"), None, None)
    assert isinstance(opts["logger"], _YtdlpLogger)


# ---------------------------------------------------------------------------
# _select_output_file
# ---------------------------------------------------------------------------

def test_select_output_file_new_file():
    with tempfile.TemporaryDirectory() as tmp:
        work_dir = Path(tmp)
        before = set(work_dir.iterdir())
        new_file = work_dir / "video.mp4"
        new_file.write_bytes(b"x" * 100)
        result = _select_output_file(work_dir, before)
        assert result == new_file


def test_select_output_file_largest_wins():
    with tempfile.TemporaryDirectory() as tmp:
        work_dir = Path(tmp)
        before = set(work_dir.iterdir())
        small = work_dir / "small.mp4"
        large = work_dir / "large.mp4"
        small.write_bytes(b"x" * 10)
        large.write_bytes(b"x" * 1000)
        result = _select_output_file(work_dir, before)
        assert result == large


def test_select_output_file_fallback_to_workdir():
    with tempfile.TemporaryDirectory() as tmp:
        work_dir = Path(tmp)
        # "Before" already includes the file (simulates yt-dlp overwrite)
        existing = work_dir / "existing.mp4"
        existing.write_bytes(b"x" * 50)
        before = set(work_dir.iterdir())  # file already there
        result = _select_output_file(work_dir, before)
        assert result == existing


def test_select_output_file_raises_when_empty():
    with tempfile.TemporaryDirectory() as tmp:
        work_dir = Path(tmp)
        before = set(work_dir.iterdir())
        with pytest.raises(RuntimeError, match="did not create"):
            _select_output_file(work_dir, before)


# ---------------------------------------------------------------------------
# download_video (integration with yt-dlp mocked)
# ---------------------------------------------------------------------------

def test_download_video_success():
    with tempfile.TemporaryDirectory() as tmp:
        settings = MagicMock()
        settings.download_dir = Path(tmp)
        settings.default_quality = "720p"
        settings.use_cookies = False

        fixed_hex = "aabbccdd11223344aabbccdd11223344"
        work_subdir = Path(tmp) / "active" / fixed_hex

        def fake_extract_info(url, download):
            work_subdir.mkdir(parents=True, exist_ok=True)
            (work_subdir / "video.mp4").write_bytes(b"x" * 100)
            return {"title": "Test Video"}

        mock_ydl = MagicMock()
        mock_ydl.__enter__ = MagicMock(return_value=mock_ydl)
        mock_ydl.__exit__ = MagicMock(return_value=False)
        mock_ydl.extract_info.side_effect = fake_extract_info

        mock_uuid = MagicMock()
        mock_uuid.hex = fixed_hex

        with patch("app.worker.downloader.uuid.uuid4", return_value=mock_uuid):
            with patch("app.worker.downloader.YoutubeDL", return_value=mock_ydl):
                path, info = download_video("https://youtube.com/watch?v=x", "720p", settings)

        expected = Path(tmp) / "active" / "video.mp4"
        assert path == expected
        assert info["title"] == "Test Video"


def test_download_video_empty_info():
    with tempfile.TemporaryDirectory() as tmp:
        settings = MagicMock()
        settings.download_dir = Path(tmp)
        settings.default_quality = "720p"
        settings.use_cookies = False

        fixed_hex = "ccdd11223344aabbccdd11223344aabb"
        work_subdir = Path(tmp) / "active" / fixed_hex

        def fake_extract_info(url, download):
            work_subdir.mkdir(parents=True, exist_ok=True)
            (work_subdir / "audio.m4a").write_bytes(b"a" * 50)
            return None  # yt-dlp can return None

        mock_ydl = MagicMock()
        mock_ydl.__enter__ = MagicMock(return_value=mock_ydl)
        mock_ydl.__exit__ = MagicMock(return_value=False)
        mock_ydl.extract_info.side_effect = fake_extract_info

        mock_uuid = MagicMock()
        mock_uuid.hex = fixed_hex

        with patch("app.worker.downloader.uuid.uuid4", return_value=mock_uuid):
            with patch("app.worker.downloader.YoutubeDL", return_value=mock_ydl):
                path, info = download_video("https://youtube.com/watch?v=x", "audio", settings)

        assert info == {}


# ---------------------------------------------------------------------------
# _is_access_retry_error / _extract_with_retry
# ---------------------------------------------------------------------------

def test_is_access_retry_error_403():
    assert _is_access_retry_error(RuntimeError("HTTP Error 403: Forbidden")) is True


def test_is_access_retry_error_geo():
    assert _is_access_retry_error(RuntimeError("This video is not available in your country")) is True


def test_is_access_retry_error_unrelated():
    assert _is_access_retry_error(RuntimeError("Network unreachable")) is False


def test_extract_with_retry_success_first_try():
    mock_ydl = MagicMock()
    mock_ydl.__enter__ = MagicMock(return_value=mock_ydl)
    mock_ydl.__exit__ = MagicMock(return_value=False)
    mock_ydl.extract_info.return_value = {"title": "ok"}
    with patch("app.worker.downloader.YoutubeDL", return_value=mock_ydl):
        result = _extract_with_retry("https://x.test/v", {"format": "best"})
    assert result == {"title": "ok"}


def test_extract_with_retry_reraises_non_access_error():
    mock_ydl = MagicMock()
    mock_ydl.__enter__ = MagicMock(return_value=mock_ydl)
    mock_ydl.__exit__ = MagicMock(return_value=False)
    mock_ydl.extract_info.side_effect = DownloadError("Private video")
    with patch("app.worker.downloader.YoutubeDL", return_value=mock_ydl):
        with pytest.raises(DownloadError):
            _extract_with_retry("https://x.test/v", {"format": "best"})


def test_extract_with_retry_retries_with_browser_headers_on_403():
    first_ydl = MagicMock()
    first_ydl.__enter__ = MagicMock(return_value=first_ydl)
    first_ydl.__exit__ = MagicMock(return_value=False)
    first_ydl.extract_info.side_effect = DownloadError("HTTP Error 403: Forbidden")

    second_ydl = MagicMock()
    second_ydl.__enter__ = MagicMock(return_value=second_ydl)
    second_ydl.__exit__ = MagicMock(return_value=False)
    second_ydl.extract_info.return_value = {"title": "retried"}

    with patch("app.worker.downloader.YoutubeDL", side_effect=[first_ydl, second_ydl]) as mock_cls:
        result = _extract_with_retry("https://x.test/v", {"format": "best"})

    assert result == {"title": "retried"}
    retry_opts = mock_cls.call_args_list[1][0][0]
    assert retry_opts["geo_bypass"] is True
    assert "User-Agent" in retry_opts["http_headers"]


# ---------------------------------------------------------------------------
# is_active_livestream
# ---------------------------------------------------------------------------

def test_is_active_livestream_true():
    mock_ydl = MagicMock()
    mock_ydl.__enter__ = MagicMock(return_value=mock_ydl)
    mock_ydl.__exit__ = MagicMock(return_value=False)
    mock_ydl.extract_info.return_value = {"is_live": True}
    with patch("app.worker.downloader.YoutubeDL", return_value=mock_ydl):
        assert is_active_livestream("https://x.test/live") is True


def test_is_active_livestream_via_live_status():
    mock_ydl = MagicMock()
    mock_ydl.__enter__ = MagicMock(return_value=mock_ydl)
    mock_ydl.__exit__ = MagicMock(return_value=False)
    mock_ydl.extract_info.return_value = {"is_live": False, "live_status": "is_live"}
    with patch("app.worker.downloader.YoutubeDL", return_value=mock_ydl):
        assert is_active_livestream("https://x.test/live") is True


def test_is_active_livestream_false_for_vod():
    mock_ydl = MagicMock()
    mock_ydl.__enter__ = MagicMock(return_value=mock_ydl)
    mock_ydl.__exit__ = MagicMock(return_value=False)
    mock_ydl.extract_info.return_value = {"is_live": False, "live_status": "was_live"}
    with patch("app.worker.downloader.YoutubeDL", return_value=mock_ydl):
        assert is_active_livestream("https://x.test/vod") is False


def test_is_active_livestream_returns_false_on_error():
    mock_ydl = MagicMock()
    mock_ydl.__enter__ = MagicMock(return_value=mock_ydl)
    mock_ydl.__exit__ = MagicMock(return_value=False)
    mock_ydl.extract_info.side_effect = RuntimeError("boom")
    with patch("app.worker.downloader.YoutubeDL", return_value=mock_ydl):
        assert is_active_livestream("https://x.test/v") is False


def test_is_active_livestream_false_for_non_dict_info():
    mock_ydl = MagicMock()
    mock_ydl.__enter__ = MagicMock(return_value=mock_ydl)
    mock_ydl.__exit__ = MagicMock(return_value=False)
    mock_ydl.extract_info.return_value = None
    with patch("app.worker.downloader.YoutubeDL", return_value=mock_ydl):
        assert is_active_livestream("https://x.test/v") is False


# ---------------------------------------------------------------------------
# _probe_stream_types / _streams_are_decodable / validate_media_file
# ---------------------------------------------------------------------------

def _completed(returncode=0, stdout="", stderr=""):
    return subprocess.CompletedProcess(args=["x"], returncode=returncode, stdout=stdout, stderr=stderr)


def test_probe_stream_types_parses_json():
    stdout = '{"streams": [{"codec_type": "video"}, {"codec_type": "audio"}]}'
    with patch("app.worker.downloader._run_ffmpeg", return_value=_completed(stdout=stdout)):
        assert _probe_stream_types(Path("/tmp/x.mp4")) == {"video", "audio"}


def test_probe_stream_types_empty_on_error():
    with patch("app.worker.downloader._run_ffmpeg", return_value=_completed(returncode=1, stderr="bad")):
        assert _probe_stream_types(Path("/tmp/x.mp4")) == set()


def test_probe_stream_types_empty_on_timeout():
    with patch("app.worker.downloader._run_ffmpeg", side_effect=subprocess.TimeoutExpired("ffprobe", 20)):
        assert _probe_stream_types(Path("/tmp/x.mp4")) == set()


def test_streams_are_decodable_true():
    with patch("app.worker.downloader._run_ffmpeg", return_value=_completed(returncode=0)):
        assert _streams_are_decodable(Path("/tmp/x.mp4")) is True


def test_streams_are_decodable_false_on_nonzero_exit():
    with patch("app.worker.downloader._run_ffmpeg", return_value=_completed(returncode=1, stderr="corrupt")):
        assert _streams_are_decodable(Path("/tmp/x.mp4")) is False


def test_validate_media_file_passes_when_all_present_and_decodable():
    with patch("app.worker.downloader._probe_stream_types", return_value={"video", "audio"}), \
         patch("app.worker.downloader._streams_are_decodable", return_value=True):
        validate_media_file(Path("/tmp/x.mp4"), "720p")  # no exception


def test_validate_media_file_raises_when_video_missing():
    with patch("app.worker.downloader._probe_stream_types", return_value={"audio"}), \
         patch("app.worker.downloader._streams_are_decodable", return_value=True):
        with pytest.raises(MediaValidationError, match="video"):
            validate_media_file(Path("/tmp/x.mp4"), "720p")


def test_validate_media_file_audio_quality_does_not_require_video():
    with patch("app.worker.downloader._probe_stream_types", return_value={"audio"}), \
         patch("app.worker.downloader._streams_are_decodable", return_value=True):
        validate_media_file(Path("/tmp/x.m4a"), "audio")  # no exception


def test_validate_media_file_raises_when_audio_missing():
    with patch("app.worker.downloader._probe_stream_types", return_value={"video"}), \
         patch("app.worker.downloader._streams_are_decodable", return_value=True):
        with pytest.raises(MediaValidationError, match="audio"):
            validate_media_file(Path("/tmp/x.mp4"), "720p")


def test_validate_media_file_raises_when_not_decodable():
    with patch("app.worker.downloader._probe_stream_types", return_value={"video", "audio"}), \
         patch("app.worker.downloader._streams_are_decodable", return_value=False):
        with pytest.raises(MediaValidationError, match="corrupt"):
            validate_media_file(Path("/tmp/x.mp4"), "720p")


# ---------------------------------------------------------------------------
# probe_video_dimensions
# ---------------------------------------------------------------------------

def test_probe_video_dimensions_parses_width_height_duration():
    stdout = '{"streams": [{"width": 1080, "height": 1920}], "format": {"duration": "12.7"}}'
    with patch("app.worker.downloader._run_ffmpeg", return_value=_completed(stdout=stdout)):
        width, height, duration = probe_video_dimensions(Path("/tmp/x.mp4"))
    assert (width, height, duration) == (1080, 1920, 12)


def test_probe_video_dimensions_none_on_ffprobe_error():
    with patch("app.worker.downloader._run_ffmpeg", return_value=_completed(returncode=1, stderr="bad")):
        assert probe_video_dimensions(Path("/tmp/x.mp4")) == (None, None, None)


def test_probe_video_dimensions_none_on_timeout():
    with patch("app.worker.downloader._run_ffmpeg", side_effect=subprocess.TimeoutExpired("ffprobe", 20)):
        assert probe_video_dimensions(Path("/tmp/x.mp4")) == (None, None, None)


def test_probe_video_dimensions_no_video_stream_audio_only():
    stdout = '{"streams": [], "format": {"duration": "180.0"}}'
    with patch("app.worker.downloader._run_ffmpeg", return_value=_completed(stdout=stdout)):
        width, height, duration = probe_video_dimensions(Path("/tmp/x.m4a"))
    assert (width, height, duration) == (None, None, 180)


def test_probe_video_dimensions_missing_duration_field():
    stdout = '{"streams": [{"width": 640, "height": 360}], "format": {}}'
    with patch("app.worker.downloader._run_ffmpeg", return_value=_completed(stdout=stdout)):
        width, height, duration = probe_video_dimensions(Path("/tmp/x.mp4"))
    assert (width, height, duration) == (640, 360, None)


# ---------------------------------------------------------------------------
# compress_to_size_limit
# ---------------------------------------------------------------------------

def test_compress_to_size_limit_returns_none_without_duration():
    with patch("app.worker.downloader._probe_duration_seconds", return_value=None):
        assert compress_to_size_limit(Path("/tmp/x.mp4"), 50) is None


def test_compress_to_size_limit_returns_smallest_successful_attempt():
    with tempfile.TemporaryDirectory() as tmp:
        src = Path(tmp) / "video.mp4"
        src.write_bytes(b"x" * (100 * 1024 * 1024))

        # Each ffmpeg attempt "creates" its output file with a decreasing size,
        # simulating successive lower-quality compression attempts.
        sizes_mb = [40, 20]

        def fake_run(command, *, timeout, cwd=None):
            out_path = Path(command[-1])
            size = sizes_mb.pop(0) if sizes_mb else 60
            out_path.write_bytes(b"y" * (size * 1024 * 1024))
            return _completed(returncode=0)

        with patch("app.worker.downloader._probe_duration_seconds", return_value=60.0), \
             patch("app.worker.downloader._run_ffmpeg", side_effect=fake_run):
            result = compress_to_size_limit(src, max_mb=30)

        assert result is not None
        assert result.stat().st_size == 20 * 1024 * 1024


def test_compress_to_size_limit_returns_none_when_all_attempts_fail():
    with tempfile.TemporaryDirectory() as tmp:
        src = Path(tmp) / "video.mp4"
        src.write_bytes(b"x" * 1024)
        with patch("app.worker.downloader._probe_duration_seconds", return_value=60.0), \
             patch("app.worker.downloader._run_ffmpeg", return_value=_completed(returncode=1, stderr="fail")):
            result = compress_to_size_limit(src, max_mb=30)
        assert result is None


def test_probe_duration_seconds_parses_stdout():
    with patch("app.worker.downloader._run_ffmpeg", return_value=_completed(stdout="12.5\n")):
        assert _probe_duration_seconds(Path("/tmp/x.mp4")) == 12.5


def test_probe_duration_seconds_none_on_bad_output():
    with patch("app.worker.downloader._run_ffmpeg", return_value=_completed(stdout="not-a-number")):
        assert _probe_duration_seconds(Path("/tmp/x.mp4")) is None


# ---------------------------------------------------------------------------
# Subtitle burn-in
# ---------------------------------------------------------------------------

def test_select_subtitle_file_matches_video_stem():
    with tempfile.TemporaryDirectory() as tmp:
        work_dir = Path(tmp)
        (work_dir / "Title_id.ru.srt").write_bytes(b"1\n00:00:00,000 --> 00:00:01,000\nHi\n")
        (work_dir / "Title_id.mp4").write_bytes(b"x")
        result = _select_subtitle_file(work_dir, "Title_id")
        assert result == work_dir / "Title_id.ru.srt"


def test_select_subtitle_file_none_when_absent():
    with tempfile.TemporaryDirectory() as tmp:
        assert _select_subtitle_file(Path(tmp), "Title_id") is None


def test_burn_subtitles_success():
    with tempfile.TemporaryDirectory() as tmp:
        work_dir = Path(tmp)
        video = work_dir / "video.mp4"
        video.write_bytes(b"x")
        sub = work_dir / "video.srt"
        sub.write_bytes(b"1\n00:00:00,000 --> 00:00:01,000\nHi\n")

        def fake_run(command, *, timeout, cwd=None):
            (work_dir / "video.subtitled.mp4").write_bytes(b"burned")
            return _completed(returncode=0)

        with patch("app.worker.downloader._run_ffmpeg", side_effect=fake_run):
            result = _burn_subtitles(video, sub)

        assert result == work_dir / "video.subtitled.mp4"
        assert not (work_dir / "__vd_subtitles.srt").exists()  # temp copy cleaned up


def test_burn_subtitles_returns_none_on_ffmpeg_failure():
    with tempfile.TemporaryDirectory() as tmp:
        work_dir = Path(tmp)
        video = work_dir / "video.mp4"
        video.write_bytes(b"x")
        sub = work_dir / "video.srt"
        sub.write_bytes(b"1\n00:00:00,000 --> 00:00:01,000\nHi\n")

        with patch("app.worker.downloader._run_ffmpeg", return_value=_completed(returncode=1, stderr="bad filter")):
            result = _burn_subtitles(video, sub)

        assert result is None
        assert not (work_dir / "__vd_subtitles.srt").exists()


def test_embed_subtitles_if_present_no_subtitle_returns_original():
    with tempfile.TemporaryDirectory() as tmp:
        work_dir = Path(tmp)
        video = work_dir / "video.mp4"
        video.write_bytes(b"x")
        assert _embed_subtitles_if_present(video, work_dir) == video


def test_embed_subtitles_if_present_burns_and_replaces():
    with tempfile.TemporaryDirectory() as tmp:
        work_dir = Path(tmp)
        video = work_dir / "video.mp4"
        video.write_bytes(b"x")
        sub = work_dir / "video.srt"
        sub.write_bytes(b"1\n00:00:00,000 --> 00:00:01,000\nHi\n")
        burned = work_dir / "video.subtitled.mp4"

        with patch("app.worker.downloader._burn_subtitles", return_value=burned) as mock_burn:
            burned.write_bytes(b"burned")
            result = _embed_subtitles_if_present(video, work_dir)

        mock_burn.assert_called_once()
        assert result == burned
        assert not video.exists()
        assert not sub.exists()
