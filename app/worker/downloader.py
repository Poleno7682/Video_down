from __future__ import annotations

import logging
import shutil
import uuid
from pathlib import Path
from typing import Callable

from yt_dlp import YoutubeDL

from app.core.config import Settings
from app.utils.platforms import PLATFORM_COOKIE_SETTING, detect_platform
from app.utils.quality import format_selector, normalize_quality

logger = logging.getLogger(__name__)


class _YtdlpLogger:
    """Bridges yt-dlp's logger interface to Python's standard logging."""

    def debug(self, msg: str) -> None:
        logger.debug("yt-dlp: %s", msg)

    def info(self, msg: str) -> None:
        logger.info("yt-dlp: %s", msg)

    def warning(self, msg: str) -> None:
        logger.warning("yt-dlp: %s", msg)

    def error(self, msg: str) -> None:
        logger.error("yt-dlp: %s", msg)


def cookie_file_for_url(url: str, settings: Settings) -> Path | None:
    if not settings.use_cookies:
        return None
    platform = detect_platform(url)
    if not platform:
        return None
    attr = PLATFORM_COOKIE_SETTING.get(platform)
    if not attr:
        return None
    path: Path = getattr(settings, attr)
    return path if path.exists() else None


def _build_ydl_opts(
    quality: str,
    work_dir: Path,
    progress_hook: Callable[[dict], None] | None,
    cookie_file: Path | None,
) -> dict:
    """Build the yt-dlp options dict. SRP: isolated so it can be tested independently."""
    opts: dict = {
        "outtmpl": str(work_dir / "%(title).180B_%(id)s.%(ext)s"),
        "noplaylist": True,
        "quiet": True,
        "no_warnings": True,
        "logger": _YtdlpLogger(),
        "retries": 3,
        "fragment_retries": 5,
        "socket_timeout": 30,
        "format": format_selector(quality),
        "merge_output_format": "mp4" if quality != "audio" else "m4a",
        "progress_hooks": [progress_hook] if progress_hook else [],
        "postprocessors": [],
        "windowsfilenames": True,
        "restrictfilenames": True,
        # Avoid ffmpeg re-encoding on merge — keeps native aspect ratio intact.
        "postprocessor_args": {"Merger+ffmpeg": ["-c", "copy"]},
        # YouTube EJS: Deno alone is not enough — yt-dlp must fetch challenge solver scripts.
        # See https://github.com/yt-dlp/yt-dlp/wiki/EJS
        "remote_components": ["ejs:github"],
    }
    if quality == "audio":
        opts["postprocessors"] = [{"key": "FFmpegExtractAudio", "preferredcodec": "m4a"}]
    if cookie_file:
        opts["cookiefile"] = str(cookie_file)
    return opts


def _select_output_file(work_dir: Path, before: set[Path]) -> Path:
    """Return the file created by yt-dlp. SRP: isolated so it can be tested independently."""
    after = set(work_dir.iterdir())
    created = [p for p in after - before if p.is_file()]
    if not created:
        created = [p for p in work_dir.iterdir() if p.is_file()]
    if not created:
        raise RuntimeError("yt-dlp did not create a media file.")
    return max(created, key=lambda p: p.stat().st_size)


def download_video(
    url: str,
    quality: str,
    settings: Settings,
    progress_hook: Callable[[dict], None] | None = None,
    cookie_file: Path | None = None,
) -> tuple[Path, dict]:
    quality = normalize_quality(quality, settings.default_quality)
    # Each download gets its own subdirectory so concurrent workers cannot
    # interfere with each other's _select_output_file result.
    work_dir = settings.download_dir / "active" / uuid.uuid4().hex
    work_dir.mkdir(parents=True, exist_ok=True)
    try:
        # Per-user cookies (cookie_file) take priority over the global shared file.
        if cookie_file is None:
            cookie_file = cookie_file_for_url(url, settings)
        opts = _build_ydl_opts(quality, work_dir, progress_hook, cookie_file)

        with YoutubeDL(opts) as ydl:
            info = ydl.extract_info(url, download=True)

        file_path = _select_output_file(work_dir, set())
        # Move the finished file one level up so callers can clean it up without
        # knowing about the per-download subdirectory.
        dest = settings.download_dir / "active" / file_path.name
        shutil.move(str(file_path), str(dest))
        return dest, info or {}
    finally:
        shutil.rmtree(work_dir, ignore_errors=True)
