from __future__ import annotations

import logging
import tempfile
import time
from pathlib import Path

from app.core.config import get_settings
from app.db.models import DownloadStatus
from app.db.repository import Repository
from app.db.session import get_session
from app.services.rate_limiter import RateLimiter
from app.services.redis_client import get_redis
from app.utils.caption import get_caption
from app.utils.platforms import detect_platform
from app.worker.celery_app import celery_app
from app.worker.downloader import download_video
from app.worker.telegram_sender import delete_status, edit_status, send_cached, send_file

logger = logging.getLogger(__name__)

# Substrings that indicate yt-dlp needs (fresh) cookies / hit an anti-bot wall.
_COOKIE_ERROR_MARKERS = (
    "sign in to confirm",
    "confirm you're not a bot",
    "confirm you are not a bot",
    "confirm you’re not a bot",  # typographic apostrophe from yt-dlp
    "--cookies",
    "cookies-from-browser",
    "login required",
    "private video",
    "this video is private",
)

# YouTube JS-challenge failures often surface as "only images" / missing formats.
_CHALLENGE_ERROR_MARKERS = (
    "challenge solving failed",
    "only images are available",
    "remote components",
    "ejs:",
)

_GENERIC_FAILURE = (
    "❌ Не получилось скачать видео. Возможные причины: приватное видео, "
    "устаревшие cookies, блокировка VPS или изменение защиты сайта."
)

_COOKIE_FAILURE = (
    "❌ YouTube требует ваши cookies (защита от ботов).\n\n"
    "Экспортируйте cookies в формате Netscape (cookies.txt) из браузера, где вы вошли "
    "в аккаунт, и пришлите файл боту под именем <code>youtube.txt</code>.\n"
    "Подробнее: /cookies"
)


def _is_cookie_error(exc: Exception) -> bool:
    text = str(exc).lower()
    return any(marker in text for marker in _COOKIE_ERROR_MARKERS)


def _is_youtube_challenge_error(exc: Exception) -> bool:
    text = str(exc).lower()
    if "requested format is not available" in text and "youtube" in text:
        return True
    return any(marker in text for marker in _CHALLENGE_ERROR_MARKERS)


def _materialize_user_cookies(repo: Repository, user_id: int, url: str) -> Path | None:
    """Write the user's stored cookies for the URL's platform to a temp file.

    Returns the path (caller must delete it) or None if the user has no cookies
    for this platform — in which case the worker falls back to the global file.
    """
    platform = detect_platform(url)
    if not platform:
        return None
    cookies_text = repo.get_user_cookies(user_id, platform)
    if not cookies_text:
        return None
    fd, name = tempfile.mkstemp(prefix=f"cookies_{user_id}_{platform}_", suffix=".txt")
    path = Path(name)
    try:
        with open(fd, "w", encoding="utf-8") as f:
            f.write(cookies_text)
    except OSError:
        path.unlink(missing_ok=True)
        return None
    return path


def _handle_task_failure(
    repo: Repository,
    request_id: int,
    req,
    exc: Exception,
) -> None:
    """DRY: consolidates the status update + Telegram notification on failure."""
    error_msg = f"{type(exc).__name__}: {exc}"
    repo.update_request_status(request_id, DownloadStatus.failed, error=error_msg, finished=True)
    if req.video_id:
        repo.mark_video_failed(req.video_id, error_msg)

    if _is_cookie_error(exc):
        message = _COOKIE_FAILURE
    elif _is_youtube_challenge_error(exc):
        message = _COOKIE_FAILURE
    else:
        message = _GENERIC_FAILURE
    edit_status(req.chat_id, req.status_message_id, message)


@celery_app.task(bind=True, autoretry_for=(ConnectionError,), retry_backoff=True, retry_kwargs={"max_retries": 3})
def process_download_request(self, request_id: int) -> None:
    settings = get_settings()
    redis = get_redis()
    limiter = RateLimiter(redis)

    with get_session() as session:
        repo = Repository(session)
        req = repo.get_request(request_id)

        if not req:
            logger.warning("Request %s not found", request_id)
            return

        if not limiter.acquire_user_download_slot(
            req.user_id,
            settings.max_active_downloads_per_user,
            settings.max_download_duration_seconds,
        ):
            repo.update_request_status(
                request_id,
                DownloadStatus.rate_limited,
                error="Too many active downloads for user",
                finished=True,
            )
            edit_status(req.chat_id, req.status_message_id, "⚠️ У тебя уже есть активная загрузка. Попробуй позже.")
            return

        video_lock_acquired = False
        user_cookie_path: Path | None = None
        try:
            ready_video = repo.get_ready_video(req.url_hash, req.quality)
            if ready_video and ready_video.telegram_file_id and ready_video.telegram_file_type:
                repo.update_request_status(request_id, DownloadStatus.sending)
                edit_status(req.chat_id, req.status_message_id, "⚡ Нашёл готовый Telegram file_id. Отправляю...")
                send_cached(req.chat_id, ready_video.telegram_file_id, ready_video.telegram_file_type, get_caption(settings))
                repo.update_request_status(request_id, DownloadStatus.done, finished=True)
                edit_status(req.chat_id, req.status_message_id, "✅ Отправлено из кэша.")
                return

            video_lock_acquired = limiter.acquire_video_lock(
                req.url_hash,
                req.quality,
                settings.max_download_duration_seconds,
            )

            if not video_lock_acquired:
                repo.update_request_status(
                    request_id,
                    DownloadStatus.rate_limited,
                    error="Same video is already being processed",
                    finished=True,
                )
                edit_status(
                    req.chat_id,
                    req.status_message_id,
                    "⏳ Такое видео уже обрабатывается. Повтори ссылку чуть позже — будет отправлено из кэша.",
                )
                return

            repo.update_request_status(request_id, DownloadStatus.downloading)
            edit_status(req.chat_id, req.status_message_id, "⬇️ Скачиваю видео...")

            last_progress_update = 0.0

            def progress_hook(data: dict) -> None:
                nonlocal last_progress_update
                now = time.time()
                if now - last_progress_update < 5:
                    return
                last_progress_update = now
                if data.get("status") != "downloading":
                    return
                total = data.get("total_bytes") or data.get("total_bytes_estimate")
                downloaded = data.get("downloaded_bytes")
                if total and downloaded:
                    percent = min(100, downloaded / total * 100)
                    edit_status(req.chat_id, req.status_message_id, f"⬇️ Скачано {percent:.1f}%")

            user_cookie_path = _materialize_user_cookies(repo, req.user_id, req.normalized_url)
            file_path, info = download_video(
                req.normalized_url,
                req.quality,
                settings,
                progress_hook=progress_hook,
                cookie_file=user_cookie_path,
            )

            file_size_bytes = file_path.stat().st_size
            size_mb = file_size_bytes / (1024 * 1024)

            if size_mb > settings.max_file_mb:
                repo.update_request_status(
                    request_id,
                    DownloadStatus.too_large,
                    error=f"File too large: {size_mb:.1f} MB",
                    finished=True,
                )
                edit_status(
                    req.chat_id,
                    req.status_message_id,
                    f"⚠️ Файл слишком большой: {size_mb:.1f} MB. Лимит: {settings.max_file_mb} MB.",
                )
                file_path.unlink(missing_ok=True)
                return

            repo.update_request_status(request_id, DownloadStatus.sending)
            edit_status(req.chat_id, req.status_message_id, f"✅ Скачано {size_mb:.1f} MB. Отправляю...")

            title = info.get("title") if isinstance(info, dict) else None
            caption = get_caption(settings)

            file_id, file_unique_id, file_type = send_file(req.chat_id, file_path, caption)

            if req.video_id:
                repo.mark_video_ready(
                    video_id=req.video_id,
                    title=title,
                    telegram_file_id=file_id,
                    telegram_file_unique_id=file_unique_id,
                    telegram_file_type=file_type,
                    local_file_path=str(file_path),
                    file_size_bytes=file_size_bytes,
                )

            repo.update_request_status(request_id, DownloadStatus.done, finished=True)
            delete_status(req.chat_id, req.status_message_id)

            if settings.delete_local_file_after_telegram_cache:
                file_path.unlink(missing_ok=True)

        except Exception as exc:
            logger.exception("Failed request %s", request_id)
            _handle_task_failure(repo, request_id, req, exc)
            raise

        finally:
            if user_cookie_path is not None:
                user_cookie_path.unlink(missing_ok=True)
            limiter.release_user_download_slot(req.user_id)
            if video_lock_acquired:
                limiter.release_video_lock(req.url_hash, req.quality)
