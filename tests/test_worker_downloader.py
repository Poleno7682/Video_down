from __future__ import annotations

import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from app.worker.downloader import (
    _YtdlpLogger,
    _build_ydl_opts,
    _select_output_file,
    cookie_file_for_url,
    download_video,
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
    assert opts["postprocessor_args"] == {"Merger+ffmpeg": ["-c", "copy"]}
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

        fake_file = Path(tmp) / "active" / "video.mp4"
        fake_file.parent.mkdir(parents=True, exist_ok=True)

        def fake_extract_info(url, download):
            fake_file.write_bytes(b"x" * 100)
            return {"title": "Test Video"}

        mock_ydl = MagicMock()
        mock_ydl.__enter__ = MagicMock(return_value=mock_ydl)
        mock_ydl.__exit__ = MagicMock(return_value=False)
        mock_ydl.extract_info.side_effect = fake_extract_info

        with patch("app.worker.downloader.YoutubeDL", return_value=mock_ydl):
            path, info = download_video("https://youtube.com/watch?v=x", "720p", settings)

        assert path == fake_file
        assert info["title"] == "Test Video"


def test_download_video_empty_info():
    with tempfile.TemporaryDirectory() as tmp:
        settings = MagicMock()
        settings.download_dir = Path(tmp)
        settings.default_quality = "720p"
        settings.use_cookies = False

        fake_file = Path(tmp) / "active" / "audio.m4a"
        fake_file.parent.mkdir(parents=True, exist_ok=True)

        def fake_extract_info(url, download):
            fake_file.write_bytes(b"a" * 50)
            return None  # yt-dlp can return None

        mock_ydl = MagicMock()
        mock_ydl.__enter__ = MagicMock(return_value=mock_ydl)
        mock_ydl.__exit__ = MagicMock(return_value=False)
        mock_ydl.extract_info.side_effect = fake_extract_info

        with patch("app.worker.downloader.YoutubeDL", return_value=mock_ydl):
            path, info = download_video("https://youtube.com/watch?v=x", "audio", settings)

        assert info == {}
