"""Выбор H.264-кодировщика: NVENC (GPU NVIDIA), если он реально доступен, иначе libx264 (CPU).

Доступность проверяется один раз пробным кодированием: наличие h264_nvenc в списке
кодировщиков ffmpeg ничего не гарантирует, если GPU не проброшен в контейнер.
Отключить GPU принудительно: FFMPEG_HWACCEL=off.
"""
from __future__ import annotations

import logging
import os
import subprocess
from functools import lru_cache

logger = logging.getLogger(__name__)

_PROBE_COMMAND = [
    "ffmpeg", "-hide_banner", "-loglevel", "error", "-nostdin",
    "-f", "lavfi", "-i", "color=c=black:s=256x256:d=0.2",
    "-c:v", "h264_nvenc", "-f", "null", "-",
]


@lru_cache(maxsize=1)
def nvenc_available() -> bool:
    if os.environ.get("FFMPEG_HWACCEL", "auto").strip().lower() in ("off", "0", "false", "no"):
        logger.info("Аппаратное кодирование отключено (FFMPEG_HWACCEL=off), используется libx264")
        return False
    try:
        result = subprocess.run(_PROBE_COMMAND, capture_output=True, text=True, timeout=20)
    except (OSError, subprocess.TimeoutExpired) as exc:
        logger.info("Проверка NVENC не удалась (%s), используется libx264", exc)
        return False
    if result.returncode != 0:
        logger.info("NVENC недоступен, используется libx264: %s", (result.stderr or "").strip()[-200:])
        return False
    logger.info("NVENC доступен — H.264 кодируется на GPU")
    return True


def quality_video_args(*, gpu: bool, cpu_preset: str, crf: int = 20, pix_fmt: str | None = None) -> list[str]:
    """Кодирование с постоянным качеством (аналог -crf)."""
    if gpu:
        args = ["-c:v", "h264_nvenc", "-preset", "p4", "-rc", "vbr", "-cq", str(crf + 4), "-b:v", "0"]
    else:
        args = ["-c:v", "libx264", "-preset", cpu_preset, "-crf", str(crf)]
    return args + (["-pix_fmt", pix_fmt] if pix_fmt else [])


def bitrate_video_args(*, gpu: bool, cpu_preset: str, video_kbps: int) -> list[str]:
    """Кодирование под целевой битрейт (сжатие под лимит размера)."""
    rate = ["-b:v", f"{video_kbps}k", "-maxrate", f"{video_kbps}k", "-bufsize", f"{video_kbps * 2}k"]
    if gpu:
        return ["-c:v", "h264_nvenc", "-preset", "p4", "-rc", "vbr"] + rate
    return ["-c:v", "libx264", "-preset", cpu_preset] + rate
