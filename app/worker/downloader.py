from __future__ import annotations

import logging
from pathlib import Path
from typing import Callable

from yt_dlp import YoutubeDL

from app.core.config import Settings
from app.utils.quality import format_selector, normalize_quality
from app.utils.url_tools import domain_name

logger = logging.getLogger(__name__)

# OCP: add new platform support by extending this list — no need to touch
# cookie_file_for_url itself.
_DOMAIN_COOKIE_FIELDS: list[tuple[str, str]] = [
    ("facebook.", "facebook_cookies_file"),
    ("fb.watch", "facebook_cookies_file"),
    ("instagram.", "instagram_cookies_file"),
    ("tiktok.", "tiktok_cookies_file"),
]


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
    domain = domain_name(url)
    for pattern, attr in _DOMAIN_COOKIE_FIELDS:
        if pattern in domain:
            path: Path = getattr(settings, attr)
            return path if path.exists() else None
    return None


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
) -> tuple[Path, dict]:
    quality = normalize_quality(quality, settings.default_quality)
    work_dir = settings.download_dir / "active"
    work_dir.mkdir(parents=True, exist_ok=True)

    before = set(work_dir.iterdir())
    cookie_file = cookie_file_for_url(url, settings)
    opts = _build_ydl_opts(quality, work_dir, progress_hook, cookie_file)

    with YoutubeDL(opts) as ydl:
        info = ydl.extract_info(url, download=True)

    return _select_output_file(work_dir, before), info or {}
