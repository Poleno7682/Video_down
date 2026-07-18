from __future__ import annotations

import json
import logging
import shutil
import subprocess
import uuid
from pathlib import Path
from typing import Callable

from yt_dlp import YoutubeDL
from yt_dlp.utils import DownloadError

from app.core.config import Settings
from app.utils.platforms import PLATFORM_COOKIE_SETTING, detect_platform
from app.utils.quality import format_selector, normalize_quality

logger = logging.getLogger(__name__)

_FFPROBE_TIMEOUT = 20
_DECODE_CHECK_SECONDS = 2
_COMPRESSION_TIMEOUT = 600
_SUBTITLE_TIMEOUT = 600
_SUBTITLE_LANGS = ["ru", "en"]
_SUBTITLE_EXTENSIONS = {".srt", ".vtt", ".ass"}

# yt-dlp raises these when a site blocks the request but a browser-like
# request often succeeds (anti-bot walls, geo-fencing, expired signed URLs).
_ACCESS_RETRY_MARKERS = (
    "http error 403",
    "http error 410",
    "http error 429",
    "forbidden",
    "gone",
    "not available in your country",
)

_BROWSER_RETRY_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "en-US,en;q=0.9",
}

# Mini-compression attempts, tried in order until the file fits max_mb.
# Bitrate for each attempt is derived from the clip duration and target size.
_COMPRESSION_ATTEMPTS = [
    {"width": 854, "fps": 24, "audio_kbps": 96, "safety": 0.92},
    {"width": 640, "fps": 20, "audio_kbps": 64, "safety": 0.85},
    {"width": 426, "fps": 15, "audio_kbps": 32, "safety": 0.78},
]


class MediaValidationError(RuntimeError):
    """A downloaded file has no decodable video/audio streams."""


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
    embed_subtitles: bool = False,
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
        # +faststart moves the moov atom (container metadata, incl. frame size)
        # to the front of the file. Without it Telegram's servers can't quickly
        # probe width/height from a freshly muxed file and fall back to a
        # square placeholder player even though the video itself is fine.
        "postprocessor_args": {"Merger+ffmpeg": ["-c", "copy", "-movflags", "+faststart"]},
        # YouTube EJS: Deno alone is not enough — yt-dlp must fetch challenge solver scripts.
        # See https://github.com/yt-dlp/yt-dlp/wiki/EJS
        "remote_components": ["ejs:github"],
    }
    if quality == "audio":
        opts["postprocessors"] = [{"key": "FFmpegExtractAudio", "preferredcodec": "m4a"}]
    if cookie_file:
        opts["cookiefile"] = str(cookie_file)
    if embed_subtitles and quality != "audio":
        opts["writesubtitles"] = True
        opts["writeautomaticsub"] = True
        opts["subtitleslangs"] = _SUBTITLE_LANGS
        opts["subtitlesformat"] = "vtt/srt/best"
        opts["postprocessors"].append({"key": "FFmpegSubtitlesConvertor", "format": "srt"})
    return opts


def _is_access_retry_error(exc: Exception) -> bool:
    text = str(exc).lower()
    return any(marker in text for marker in _ACCESS_RETRY_MARKERS)


def _extract_with_retry(url: str, opts: dict) -> dict:
    """Download via yt-dlp, retrying once with browser-like headers on 403/410/429/geo errors."""
    try:
        with YoutubeDL(opts) as ydl:
            return ydl.extract_info(url, download=True) or {}
    except DownloadError as exc:
        if not _is_access_retry_error(exc):
            raise
        logger.info("Access error for %s, retrying with browser-like headers", url)
        retry_opts = dict(opts)
        retry_opts["http_headers"] = {**opts.get("http_headers", {}), **_BROWSER_RETRY_HEADERS}
        retry_opts["geo_bypass"] = True
        with YoutubeDL(retry_opts) as ydl:
            return ydl.extract_info(url, download=True) or {}


def is_active_livestream(url: str) -> bool:
    """Pre-check whether the URL points to an unfinished live stream.

    Downloading an active stream either runs forever or produces a partial
    file, so we reject it before starting the real download.
    """
    opts = {
        "quiet": True,
        "no_warnings": True,
        "skip_download": True,
        "noplaylist": True,
        "logger": _YtdlpLogger(),
    }
    try:
        with YoutubeDL(opts) as ydl:
            info = ydl.extract_info(url, download=False)
    except Exception as exc:
        logger.warning("Livestream pre-check failed for %s: %s", url, exc)
        return False
    if not isinstance(info, dict):
        return False
    return info.get("is_live") is True or str(info.get("live_status") or "").lower() == "is_live"


def _select_output_file(work_dir: Path, before: set[Path]) -> Path:
    """Return the file created by yt-dlp. SRP: isolated so it can be tested independently."""
    after = set(work_dir.iterdir())
    created = [p for p in after - before if p.is_file()]
    if not created:
        created = [p for p in work_dir.iterdir() if p.is_file()]
    if not created:
        raise RuntimeError("yt-dlp did not create a media file.")
    return max(created, key=lambda p: p.stat().st_size)


def _select_subtitle_file(work_dir: Path, video_stem: str) -> Path | None:
    candidates = sorted(
        p for p in work_dir.iterdir()
        if p.is_file() and p.suffix.lower() in _SUBTITLE_EXTENSIONS and p.stem.startswith(video_stem)
    )
    return candidates[0] if candidates else None


def _run_ffmpeg(command: list[str], *, timeout: int, cwd: str | None = None) -> subprocess.CompletedProcess:
    return subprocess.run(command, capture_output=True, text=True, timeout=timeout, cwd=cwd)


def _burn_subtitles(video_path: Path, subtitle_path: Path) -> Path | None:
    """Hard-burn subtitle_path into video_path's picture, returning the new file or None on failure."""
    output_path = video_path.with_name(f"{video_path.stem}.subtitled{video_path.suffix}")
    output_path.unlink(missing_ok=True)
    work_dir = video_path.parent
    # Copy under a fixed, extension-preserving name so the ffmpeg subtitles
    # filter never has to escape special characters from the real title.
    safe_subtitle_name = f"__vd_subtitles{subtitle_path.suffix.lower()}"
    safe_subtitle_path = work_dir / safe_subtitle_name
    try:
        shutil.copy2(subtitle_path, safe_subtitle_path)
        subtitle_filter = (
            f"subtitles={safe_subtitle_name}:force_style="
            "'FontName=Arial,FontSize=22,PrimaryColour=&H00FFFFFF&,"
            "OutlineColour=&H00000000&,BorderStyle=1,Outline=2,Shadow=1,MarginV=28'"
        )
        command = [
            "ffmpeg", "-y", "-nostdin", "-hide_banner", "-loglevel", "warning",
            "-i", video_path.name, "-vf", subtitle_filter,
            "-c:v", "libx264", "-preset", "veryfast", "-crf", "20",
            "-c:a", "aac", "-b:a", "160k", "-movflags", "+faststart",
            output_path.name,
        ]
        try:
            result = _run_ffmpeg(command, timeout=_SUBTITLE_TIMEOUT, cwd=str(work_dir))
        except (OSError, subprocess.TimeoutExpired) as exc:
            logger.warning("Subtitle burn-in failed for %s: %s", video_path, exc)
            return None
        if result.returncode != 0 or not output_path.exists():
            logger.warning("ffmpeg subtitle burn-in error: %s", (result.stderr or "").strip()[-500:])
            return None
        return output_path
    finally:
        safe_subtitle_path.unlink(missing_ok=True)


def _embed_subtitles_if_present(file_path: Path, work_dir: Path) -> Path:
    """Burn any downloaded subtitle track into file_path. Returns the (possibly new) file path."""
    subtitle_path = _select_subtitle_file(work_dir, file_path.stem)
    if not subtitle_path:
        return file_path
    burned = _burn_subtitles(file_path, subtitle_path)
    subtitle_path.unlink(missing_ok=True)
    if not burned:
        return file_path
    file_path.unlink(missing_ok=True)
    return burned


def _probe_stream_types(file_path: Path) -> set[str]:
    command = [
        "ffprobe", "-v", "error", "-show_entries", "stream=codec_type",
        "-of", "json", str(file_path),
    ]
    try:
        result = _run_ffmpeg(command, timeout=_FFPROBE_TIMEOUT)
    except (OSError, subprocess.TimeoutExpired) as exc:
        logger.warning("ffprobe failed for %s: %s", file_path, exc)
        return set()
    if result.returncode != 0:
        logger.warning("ffprobe error for %s: %s", file_path, (result.stderr or "").strip())
        return set()
    try:
        data = json.loads(result.stdout)
    except json.JSONDecodeError:
        return set()
    return {
        str(stream.get("codec_type") or "").lower()
        for stream in data.get("streams") or []
        if isinstance(stream, dict)
    }


def _streams_are_decodable(file_path: Path) -> bool:
    command = [
        "ffmpeg", "-v", "error", "-xerror", "-i", str(file_path),
        "-t", str(_DECODE_CHECK_SECONDS), "-f", "null", "-",
    ]
    try:
        result = _run_ffmpeg(command, timeout=_FFPROBE_TIMEOUT)
    except (OSError, subprocess.TimeoutExpired) as exc:
        logger.warning("ffmpeg decode check failed for %s: %s", file_path, exc)
        return False
    if result.returncode != 0:
        logger.warning("ffmpeg decode check error for %s: %s", file_path, (result.stderr or "").strip())
        return False
    return True


def validate_media_file(file_path: Path, quality: str) -> None:
    """Raise MediaValidationError when file_path has no decodable media streams.

    Catches the class of bug where yt-dlp merges an incompatible codec (e.g.
    VP9-in-MP4) and Telegram clients render only a static frame while audio
    plays — ffprobe alone would report "video" present but not that the
    stream is actually decodable.
    """
    stream_types = _probe_stream_types(file_path)
    missing = []
    if quality != "audio" and "video" not in stream_types:
        missing.append("video")
    if "audio" not in stream_types:
        missing.append("audio")
    if missing:
        raise MediaValidationError(f"Downloaded file is missing stream(s): {', '.join(missing)}")
    if not _streams_are_decodable(file_path):
        raise MediaValidationError("Downloaded file has a corrupt or non-decodable stream")


def probe_video_dimensions(file_path: Path) -> tuple[int | None, int | None, int | None]:
    """Return (width, height, duration_seconds) from the actual file on disk.

    Passed to Telegram's send_video/send_audio so its clients render the
    correct aspect ratio immediately instead of falling back to a square
    placeholder while they probe the file themselves. Reads the real,
    possibly-compressed file rather than yt-dlp's info dict, since Mini
    compression changes the pixel dimensions.
    """
    command = [
        "ffprobe", "-v", "error", "-select_streams", "v:0",
        "-show_entries", "stream=width,height:format=duration",
        "-of", "json", str(file_path),
    ]
    try:
        result = _run_ffmpeg(command, timeout=_FFPROBE_TIMEOUT)
    except (OSError, subprocess.TimeoutExpired) as exc:
        logger.warning("ffprobe dimension probe failed for %s: %s", file_path, exc)
        return None, None, None
    if result.returncode != 0:
        return None, None, None
    try:
        data = json.loads(result.stdout)
    except json.JSONDecodeError:
        return None, None, None

    width = height = None
    streams = data.get("streams") or []
    if streams and isinstance(streams[0], dict):
        width = streams[0].get("width")
        height = streams[0].get("height")

    duration = None
    try:
        duration = int(float((data.get("format") or {}).get("duration")))
    except (TypeError, ValueError):
        duration = None

    return width, height, duration


def _probe_duration_seconds(file_path: Path) -> float | None:
    command = [
        "ffprobe", "-v", "error", "-show_entries", "format=duration",
        "-of", "default=noprint_wrappers=1:nokey=1", str(file_path),
    ]
    try:
        result = _run_ffmpeg(command, timeout=_FFPROBE_TIMEOUT)
    except (OSError, subprocess.TimeoutExpired):
        return None
    if result.returncode != 0:
        return None
    try:
        duration = float(result.stdout.strip())
    except ValueError:
        return None
    return duration if duration > 0 else None


def compress_to_size_limit(file_path: Path, max_mb: int, timeout: int = _COMPRESSION_TIMEOUT) -> Path | None:
    """Re-encode file_path with decreasing quality until it fits under max_mb.

    Tries a few resolution/bitrate presets, keeping the smallest successful
    output. Returns the path to the compressed file (the caller is
    responsible for replacing/deleting the original), or None when the
    duration can't be probed or every attempt fails.
    """
    max_bytes = max_mb * 1024 * 1024
    duration = _probe_duration_seconds(file_path)
    if not duration:
        return None

    best_path: Path | None = None
    best_size = float("inf")
    for attempt in _COMPRESSION_ATTEMPTS:
        total_kbps = max(24, int((max_bytes * 8 * attempt["safety"]) / duration / 1000))
        audio_kbps = min(attempt["audio_kbps"], max(12, total_kbps // 4))
        video_kbps = max(12, total_kbps - audio_kbps)
        out_path = file_path.with_name(f"{file_path.stem}.compressed_{attempt['width']}{file_path.suffix}")
        out_path.unlink(missing_ok=True)

        command = [
            "ffmpeg", "-y", "-nostats", "-i", str(file_path),
            "-map", "0:v:0", "-map", "0:a?",
            # setsar=1 forces square pixels on the output: some source clips
            # carry a non-1:1 sample aspect ratio (anamorphic encodes), which
            # would otherwise leave stale SAR metadata pointing at the
            # pre-scale pixel grid and make players stretch/squash the frame.
            "-vf", f"scale='min({attempt['width']},iw)':-2,setsar=1,fps={attempt['fps']}",
            "-c:v", "libx264", "-preset", "veryfast",
            "-b:v", f"{video_kbps}k", "-maxrate", f"{video_kbps}k", "-bufsize", f"{video_kbps * 2}k",
            "-c:a", "aac", "-b:a", f"{audio_kbps}k",
            "-movflags", "+faststart",
            str(out_path),
        ]
        try:
            result = _run_ffmpeg(command, timeout=timeout)
        except (OSError, subprocess.TimeoutExpired) as exc:
            logger.warning("Compression attempt failed for %s: %s", file_path, exc)
            continue
        if result.returncode != 0 or not out_path.exists():
            logger.warning("ffmpeg compression attempt failed: %s", (result.stderr or "").strip()[-500:])
            out_path.unlink(missing_ok=True)
            continue

        size = out_path.stat().st_size
        if size < best_size:
            if best_path and best_path.exists():
                best_path.unlink(missing_ok=True)
            best_path, best_size = out_path, size
        else:
            out_path.unlink(missing_ok=True)
        if size <= max_bytes:
            break

    return best_path


def download_video(
    url: str,
    quality: str,
    settings: Settings,
    progress_hook: Callable[[dict], None] | None = None,
    cookie_file: Path | None = None,
    embed_subtitles: bool = False,
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
        opts = _build_ydl_opts(quality, work_dir, progress_hook, cookie_file, embed_subtitles)

        info = _extract_with_retry(url, opts)

        file_path = _select_output_file(work_dir, set())
        if embed_subtitles and quality != "audio":
            file_path = _embed_subtitles_if_present(file_path, work_dir)
        # Move the finished file one level up so callers can clean it up without
        # knowing about the per-download subdirectory.
        dest = settings.download_dir / "active" / file_path.name
        shutil.move(str(file_path), str(dest))
        return dest, info or {}
    finally:
        shutil.rmtree(work_dir, ignore_errors=True)
