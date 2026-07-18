from __future__ import annotations

import logging
import tempfile
import time
from pathlib import Path
from typing import Callable, Protocol

from app.core.config import Settings, get_settings
from app.db.models import DownloadRequest, DownloadStatus, UserGoogleToken
from app.db.repository import CookieRepository, Repository
from app.db.session import get_session
from app.services.google_oauth import generate_youtube_cookies, refresh_access_token
from app.services.rate_limiter import RateLimiter
from app.services.redis_client import get_redis
from app.services.runtime_config import get_limit
from app.utils.caption import get_caption
from app.utils.platforms import detect_platform
from app.worker.celery_app import celery_app
from app.worker.downloader import (
    MediaValidationError,
    compress_to_size_limit,
    cookie_file_for_url,
    download_video,
    ensure_telegram_compatible_video,
    is_active_livestream,
    log_media_debug_info,
    probe_video_dimensions,
    validate_media_file,
)
from aiogram.exceptions import TelegramEntityTooLarge

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
    "no longer valid",
    "have likely been rotated",
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

_STALE_COOKIE_FAILURE = (
    "❌ Ваши YouTube cookies устарели — YouTube их сбросил.\n\n"
    "Экспортируйте <b>свежий</b> cookies.txt из браузера, где вы залогинены, "
    "и пришлите файл <code>youtube.txt</code> заново.\n"
    "Подробнее: /cookies"
)

_TOO_LARGE_FAILURE = (
    "⚠️ Файл слишком большой для Telegram (лимит 50 МБ).\n\n"
    "Попробуй выбрать качество пониже — /quality."
)

_LIVESTREAM_FAILURE = (
    "🔴 Это активная трансляция, а не готовая запись.\n\n"
    "Бот не скачивает незавершённые эфиры — попробуй ссылку ещё раз после окончания трансляции."
)

_CORRUPT_MEDIA_FAILURE = (
    "❌ Скачанный файл повреждён (сайт отдал несовместимый видео/аудио поток).\n\n"
    "Попробуй ещё раз или выбери другое качество — /quality."
)

# Redis key TTL after a Google OAuth cookie refresh to prevent refresh loops.
_GOOGLE_REFRESH_COOLDOWN = 300  # seconds


def _google_refresh_key(user_id: int) -> str:
    return f"google_cookie_refresh:{user_id}"


def _is_cookie_error(exc: Exception) -> bool:
    text = str(exc).lower()
    return any(marker in text for marker in _COOKIE_ERROR_MARKERS)


def _is_youtube_challenge_error(exc: Exception) -> bool:
    text = str(exc).lower()
    if "requested format is not available" in text and "youtube" in text:
        return True
    return any(marker in text for marker in _CHALLENGE_ERROR_MARKERS)


class _GoogleCookieRepo(Protocol):
    def get_google_token(self, user_id: int) -> UserGoogleToken | None: ...
    def set_user_cookies(self, user_id: int, platform: str, cookies_text: str) -> None: ...


def _materialize_user_cookies(repo: CookieRepository, user_id: int, url: str) -> Path | None:
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


def _try_refresh_google_cookies(repo: _GoogleCookieRepo, user_id: int) -> bool:
    """Refresh YouTube cookies from the stored Google refresh_token.

    Returns True if cookies were successfully refreshed.
    """
    try:
        token_rec = repo.get_google_token(user_id)
        if not token_rec:
            return False
        new_tokens = refresh_access_token(token_rec.refresh_token)
        new_cookies = generate_youtube_cookies(new_tokens["access_token"])
        repo.set_user_cookies(user_id, "youtube", new_cookies)
        return True
    except Exception:
        return False


def _handle_task_failure(
    repo: Repository,
    request_id: int,
    req: DownloadRequest,
    exc: Exception,
    *,
    cookies_were_used: bool = False,
    cookie_refreshed: bool = False,
) -> None:
    """Record the failure in the DB and send a Telegram notification."""
    error_msg = f"{type(exc).__name__}: {exc}"
    repo.update_request_status(request_id, DownloadStatus.failed, error=error_msg, finished=True)
    if req.video_id:
        repo.mark_video_failed(req.video_id, error_msg)

    if cookie_refreshed:
        message = "🔄 YouTube cookies автоматически обновлены. Отправь ссылку ещё раз."
    elif isinstance(exc, TelegramEntityTooLarge):
        message = _TOO_LARGE_FAILURE
    elif isinstance(exc, MediaValidationError):
        message = _CORRUPT_MEDIA_FAILURE
    elif _is_cookie_error(exc) or _is_youtube_challenge_error(exc):
        message = _STALE_COOKIE_FAILURE if cookies_were_used else _COOKIE_FAILURE
    else:
        message = _GENERIC_FAILURE
    edit_status(req.chat_id, req.status_message_id, message)


def _try_serve_from_cache(
    repo: Repository,
    req: DownloadRequest,
    request_id: int,
    settings: Settings,
) -> bool:
    """Send a cached Telegram file if available. Returns True when request is fulfilled."""
    ready_video = repo.get_ready_video(req.url_hash, req.quality)
    if not (ready_video and ready_video.telegram_file_id and ready_video.telegram_file_type):
        return False
    repo.update_request_status(request_id, DownloadStatus.sending)
    edit_status(req.chat_id, req.status_message_id, "⚡ Нашёл готовый Telegram file_id. Отправляю...")
    send_cached(req.chat_id, ready_video.telegram_file_id, ready_video.telegram_file_type, get_caption(settings))
    repo.update_request_status(request_id, DownloadStatus.done, finished=True)
    edit_status(req.chat_id, req.status_message_id, "✅ Отправлено из кэша.")
    return True


def _build_progress_hook(chat_id: int, status_message_id: int | None) -> Callable[[dict], None]:
    """Return a yt-dlp progress hook that throttles Telegram status updates to 1 per 5 s."""
    last_update = 0.0

    def _hook(data: dict) -> None:
        nonlocal last_update
        now = time.time()
        if now - last_update < 5:
            return
        last_update = now
        if data.get("status") != "downloading":
            return
        total = data.get("total_bytes") or data.get("total_bytes_estimate")
        downloaded = data.get("downloaded_bytes")
        if total and downloaded:
            percent = min(100, downloaded / total * 100)
            edit_status(chat_id, status_message_id, f"⬇️ Скачано {percent:.1f}%")

    return _hook


def _upload_and_cache(
    repo: Repository,
    req: DownloadRequest,
    request_id: int,
    file_path: Path,
    info: dict | None,
    file_size_bytes: int,
    settings: Settings,
) -> None:
    """Send the downloaded file to Telegram, persist the file_id, and clean up."""
    size_mb = file_size_bytes / (1024 * 1024)
    repo.update_request_status(request_id, DownloadStatus.sending)
    edit_status(req.chat_id, req.status_message_id, f"✅ Скачано {size_mb:.1f} MB. Отправляю...")

    title = info.get("title") if isinstance(info, dict) else None
    width, height, duration = probe_video_dimensions(file_path)
    file_id, file_unique_id, file_type = send_file(
        req.chat_id, file_path, get_caption(settings), width=width, height=height, duration=duration
    )

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

        max_active = get_limit("max_active_downloads_per_user", settings, redis)
        max_duration = get_limit("max_download_duration_seconds", settings, redis)
        if max_active > 0 and not limiter.acquire_user_download_slot(
            req.user_id, max_active, max_duration
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
        cookies_were_used = False
        try:
            if _try_serve_from_cache(repo, req, request_id, settings):
                return

            video_lock_acquired = limiter.acquire_video_lock(
                req.url_hash,
                req.quality,
                get_limit("max_download_duration_seconds", settings, redis),
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

            if is_active_livestream(req.normalized_url):
                repo.update_request_status(
                    request_id,
                    DownloadStatus.failed,
                    error="Active livestream, not a finished recording",
                    finished=True,
                )
                edit_status(req.chat_id, req.status_message_id, _LIVESTREAM_FAILURE)
                return

            repo.update_request_status(request_id, DownloadStatus.downloading)
            edit_status(req.chat_id, req.status_message_id, "⬇️ Скачиваю видео...")

            user_cookie_path = _materialize_user_cookies(repo, req.user_id, req.normalized_url)
            cookies_were_used = user_cookie_path is not None or cookie_file_for_url(
                req.normalized_url, settings
            ) is not None
            file_path, info = download_video(
                req.normalized_url,
                req.quality,
                settings,
                progress_hook=_build_progress_hook(req.chat_id, req.status_message_id),
                cookie_file=user_cookie_path,
                embed_subtitles=settings.embed_subtitles,
            )

            validate_media_file(file_path, req.quality)
            debug_context = f"request={request_id} url={req.normalized_url} quality={req.quality}"
            codecs = log_media_debug_info(file_path, context=debug_context)
            if req.quality != "audio":
                file_path = ensure_telegram_compatible_video(file_path, codecs)

            file_size_bytes = file_path.stat().st_size
            size_mb = file_size_bytes / (1024 * 1024)

            max_mb = get_limit("max_file_mb", settings, redis)
            if max_mb > 0 and size_mb > max_mb:
                edit_status(
                    req.chat_id,
                    req.status_message_id,
                    f"⚠️ Файл слишком большой ({size_mb:.1f} MB). Пробую сжать под лимит {max_mb} MB...",
                )
                compressed_path = compress_to_size_limit(file_path, max_mb)
                if compressed_path is not None:
                    file_path.unlink(missing_ok=True)
                    file_path = compressed_path
                    file_size_bytes = file_path.stat().st_size
                    size_mb = file_size_bytes / (1024 * 1024)

            if max_mb > 0 and size_mb > max_mb:
                repo.update_request_status(
                    request_id,
                    DownloadStatus.too_large,
                    error=f"File too large: {size_mb:.1f} MB",
                    finished=True,
                )
                edit_status(
                    req.chat_id,
                    req.status_message_id,
                    f"⚠️ Файл слишком большой: {size_mb:.1f} MB. Лимит: {max_mb} MB.",
                )
                file_path.unlink(missing_ok=True)
                return

            _upload_and_cache(repo, req, request_id, file_path, info, file_size_bytes, settings)

        except Exception as exc:
            logger.exception("Failed request %s", request_id)
            refresh_key = _google_refresh_key(req.user_id)
            recently_refreshed = bool(redis.exists(refresh_key))
            cookie_refreshed = (
                not recently_refreshed
                and cookies_were_used
                and (_is_cookie_error(exc) or _is_youtube_challenge_error(exc))
                and _try_refresh_google_cookies(repo, req.user_id)
            )
            if cookie_refreshed:
                redis.setex(refresh_key, _GOOGLE_REFRESH_COOLDOWN, "1")
            _handle_task_failure(
                repo, request_id, req, exc,
                cookies_were_used=cookies_were_used,
                cookie_refreshed=cookie_refreshed,
            )
            raise

        finally:
            if user_cookie_path is not None:
                user_cookie_path.unlink(missing_ok=True)
            limiter.release_user_download_slot(req.user_id)
            if video_lock_acquired:
                limiter.release_video_lock(req.url_hash, req.quality)
