from __future__ import annotations

import logging
import tempfile
import time
from pathlib import Path
from typing import Callable, Protocol

from sqlalchemy.exc import OperationalError

from app.core.config import Settings, get_settings
from app.db.models import DownloadRequest, DownloadStatus, UserGoogleToken
from app.db.repository import CookieRepository, Repository
from app.db.session import ScopedRepository
from app.services.cleanup import cleanup_stale_downloads as _cleanup_stale_downloads
from app.services.google_oauth import generate_youtube_cookies, refresh_access_token
from app.services.rate_limiter import RateLimiter
from app.services.redis_client import get_redis
from app.services.runtime_config import get_limit
from app.utils.caption import get_caption
from app.utils.platforms import YOUTUBE, detect_platform
from app.utils.rezka import is_rezka_url
from app.worker.celery_app import celery_app
from app.worker.downloader import (
    COMPRESSION_TIMEOUT,
    TRANSCODE_TIMEOUT,
    MediaValidationError,
    compress_to_size_limit,
    cookie_file_for_url,
    is_active_livestream,
    prepare_media_for_telegram,
    probe_video_dimensions,
)
from aiogram.exceptions import TelegramEntityTooLarge

from app.worker.telegram_sender import TelegramSender, get_default_sender

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
# Each marker is a substring, or a tuple of substrings that must ALL be
# present (used where a single substring alone would false-positive on
# unrelated errors, e.g. "requested format is not available" by itself).
_CHALLENGE_ERROR_MARKERS = (
    "challenge solving failed",
    "only images are available",
    "remote components",
    "ejs:",
    ("requested format is not available", "youtube"),
)

_GENERIC_FAILURE = (
    "❌ Не получилось скачать видео. Возможные причины: приватное видео, "
    "устаревшие cookies, блокировка VPS или изменение защиты сайта."
)

_REZKA_STREAM_FAILURE = (
    "❌ Не удалось получить поток видео с Rezka для выбранной озвучки.\n\n"
    "Обычно это значит, что именно эта озвучка сейчас недоступна на сайте — "
    "другие, как правило, работают. Попробуйте выбрать другую озвучку "
    "(отправьте ссылку заново) или повторите попытку позже."
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

# max_download_duration_seconds only bounds the download itself; after that,
# _upload_and_cache's pipeline can still run ensure_telegram_compatible_video's
# H.264 transcode (up to TRANSCODE_TIMEOUT) and, separately, compress_to_size_limit
# for an oversized file (up to another COMPRESSION_TIMEOUT). Without this buffer
# the video dedup lock could expire mid-processing and let a duplicate concurrent
# download of the same video/quality start.
_VIDEO_LOCK_POST_PROCESSING_BUFFER_SECONDS = TRANSCODE_TIMEOUT + COMPRESSION_TIMEOUT


def _google_refresh_key(user_id: int) -> str:
    return f"google_cookie_refresh:{user_id}"


def _text_matches_markers(text: str, markers: tuple[str | tuple[str, ...], ...]) -> bool:
    """A marker matches if it's a substring present in text, or (for a tuple
    marker) if every substring in it is present."""
    for marker in markers:
        if isinstance(marker, tuple):
            if all(part in text for part in marker):
                return True
        elif marker in text:
            return True
    return False


def _is_cookie_error(exc: Exception) -> bool:
    return _text_matches_markers(str(exc).lower(), _COOKIE_ERROR_MARKERS)


def _is_youtube_challenge_error(exc: Exception) -> bool:
    return _text_matches_markers(str(exc).lower(), _CHALLENGE_ERROR_MARKERS)


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
    except Exception:
        # Broad on purpose: whatever fails after mkstemp() (not just OSError),
        # the temp file it created must not be left behind — the caller falls
        # back to the global cookie file when this returns None.
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
        logger.exception("Google cookie refresh failed for user %s", user_id)
        return False


def _handle_task_failure(
    sender: TelegramSender,
    repo: Repository,
    request_id: int,
    chat_id: int,
    status_message_id: int | None,
    video_id: int | None,
    exc: Exception,
    *,
    cookies_were_used: bool = False,
    cookie_refreshed: bool = False,
    is_rezka: bool = False,
) -> None:
    """Record the failure in the DB and send a Telegram notification.

    Takes plain values rather than the DownloadRequest ORM object: by the
    time this runs the session may have just failed a commit (e.g. a
    transient DB connection drop), and touching an ORM attribute that isn't
    already loaded would trigger a lazy-load on a broken session — masking
    the real error behind a PendingRollbackError and skipping the user
    notification below entirely.
    """
    error_msg = f"{type(exc).__name__}: {exc}"
    try:
        repo.update_request_status(request_id, DownloadStatus.failed, error=error_msg, finished=True)
        if video_id:
            repo.mark_video_failed(video_id, error_msg)
    except Exception:
        # DB is still unhappy — the user still deserves a Telegram message
        # below, which needs no DB access at all.
        logger.exception("Could not persist failure status for request %s", request_id)

    if cookie_refreshed:
        message = "🔄 YouTube cookies автоматически обновлены. Отправь ссылку ещё раз."
    elif isinstance(exc, TelegramEntityTooLarge):
        message = _TOO_LARGE_FAILURE
    elif isinstance(exc, MediaValidationError):
        message = _CORRUPT_MEDIA_FAILURE
    elif _is_cookie_error(exc) or _is_youtube_challenge_error(exc):
        message = _STALE_COOKIE_FAILURE if cookies_were_used else _COOKIE_FAILURE
    elif is_rezka:
        message = _REZKA_STREAM_FAILURE
    else:
        message = _GENERIC_FAILURE
    sender.edit_status(chat_id, status_message_id, message)


def _try_serve_from_cache(
    sender: TelegramSender,
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
    sender.edit_status(req.chat_id, req.status_message_id, "⚡ Нашёл готовый Telegram file_id. Отправляю...")
    caption_title = ready_video.title if is_rezka_url(req.normalized_url) else None
    sender.send_cached(req.chat_id, ready_video.telegram_file_id, ready_video.telegram_file_type, get_caption(settings, caption_title))
    repo.update_request_status(request_id, DownloadStatus.done, finished=True)
    sender.edit_status(req.chat_id, req.status_message_id, "✅ Отправлено из кэша.")
    return True


def _build_progress_hook(
    sender: TelegramSender, chat_id: int, status_message_id: int | None
) -> Callable[[dict], None]:
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
            sender.edit_status(chat_id, status_message_id, f"⬇️ Скачано {percent:.1f}%")

    return _hook


def _build_transcode_progress_hook(
    sender: TelegramSender, chat_id: int, status_message_id: int | None
) -> Callable[[float], None]:
    """Return an ffmpeg transcode progress hook, throttled to 1 update per 5 s."""
    last_update = 0.0

    def _hook(percent: float) -> None:
        nonlocal last_update
        now = time.time()
        if now - last_update < 5:
            return
        last_update = now
        sender.edit_status(
            chat_id, status_message_id, f"🔄 Конвертирую видео для совместимости с Telegram: {percent:.0f}%"
        )

    return _hook


def _check_user_rate_limit(
    sender: TelegramSender,
    repo: Repository,
    limiter: RateLimiter,
    settings: Settings,
    redis,
    request_id: int,
    user_id: int,
    chat_id: int,
    status_message_id: int | None,
    max_duration: int,
) -> bool:
    """Reject the request if the user already has too many active downloads.

    Returns True when the request may proceed.
    """
    max_active = get_limit("max_active_downloads_per_user", settings, redis)
    if max_active <= 0 or limiter.acquire_user_download_slot(user_id, max_active, max_duration):
        return True
    repo.update_request_status(
        request_id,
        DownloadStatus.rate_limited,
        error="Too many active downloads for user",
        finished=True,
    )
    sender.edit_status(chat_id, status_message_id, "⚠️ У тебя уже есть активная загрузка. Попробуй позже.")
    return False


def _acquire_video_lock_or_reject(
    sender: TelegramSender,
    repo: Repository,
    limiter: RateLimiter,
    request_id: int,
    chat_id: int,
    status_message_id: int | None,
    url_hash: str,
    quality: str,
    max_duration: int,
) -> bool:
    """Reject the request if the same video/quality is already being processed.

    Returns True when the lock was acquired and the request may proceed.
    """
    if limiter.acquire_video_lock(url_hash, quality, max_duration):
        return True
    repo.update_request_status(
        request_id,
        DownloadStatus.rate_limited,
        error="Same video is already being processed",
        finished=True,
    )
    sender.edit_status(
        chat_id,
        status_message_id,
        "⏳ Такое видео уже обрабатывается. Повтори ссылку чуть позже — будет отправлено из кэша.",
    )
    return False


def _resolve_proxies(repo: Repository, settings: Settings, url: str) -> list[str]:
    """Proxies to try, least-failed first. Falls back to YTDLP_PROXY when the
    admin-managed pool (DB) is empty, so an env-only setup keeps working.

    Only used for YouTube and rezka.ag: the pool exists to route around
    IP-based blocks/throttling — YouTube's datacenter-IP anti-bot check,
    and rezka's resolved CDN hosts (e.g. stream.voidboost.cc) timing out
    outright for some VPS IP ranges (seen in production: every attempt
    times out at 90s, direct, so it isn't a transient blip a longer
    timeout alone can fix). Every proxy in a typical free/cheap pool is
    unreliable enough (dead, wrong scheme, geo-blocked) that routing sites
    without this specific problem through it too just adds failure modes
    those sites never had in the first place — hence not "every URL".
    """
    if detect_platform(url) != YOUTUBE and not is_rezka_url(url):
        return []
    db_proxies = repo.get_enabled_proxy_urls()
    if db_proxies:
        return db_proxies
    return [settings.ytdlp_proxy] if settings.ytdlp_proxy else []


def _record_proxy_result(repo: Repository, request_id: int) -> Callable[[str | None, bool], None]:
    def _record(proxy: str | None, success: bool) -> None:
        if not proxy:
            return
        if success:
            logger.info("request=%s: proxy=%s succeeded", request_id, proxy)
            repo.record_proxy_success(proxy)
        else:
            logger.info("request=%s: proxy=%s failed, trying next", request_id, proxy)
            repo.record_proxy_failure(proxy)

    return _record


def _reject_active_livestream(
    sender: TelegramSender,
    repo: Repository,
    request_id: int,
    chat_id: int,
    status_message_id: int | None,
    normalized_url: str,
    settings: Settings,
    proxies: list[str],
    on_proxy_result: Callable[[str | None, bool], None],
) -> bool:
    """Reject the request if the URL points at an ongoing livestream.

    Returns True when the request may proceed.
    """
    if not is_active_livestream(normalized_url, proxies, on_proxy_result):
        return True
    repo.update_request_status(
        request_id,
        DownloadStatus.failed,
        error="Active livestream, not a finished recording",
        finished=True,
    )
    sender.edit_status(chat_id, status_message_id, _LIVESTREAM_FAILURE)
    return False


def _download_and_prepare_media(
    sender: TelegramSender,
    repo: Repository,
    request_id: int,
    user_id: int,
    chat_id: int,
    status_message_id: int | None,
    normalized_url: str,
    quality: str,
    settings: Settings,
    proxies: list[str],
    on_proxy_result: Callable[[str | None, bool], None],
    redis,
) -> tuple[Path, dict | None, Path | None, bool]:
    """Download the video, validate it, and make it Telegram-compatible.

    Returns (file_path, info, user_cookie_path, cookies_were_used). The
    caller owns cleanup of user_cookie_path.
    """
    repo.update_request_status(request_id, DownloadStatus.downloading)
    sender.edit_status(chat_id, status_message_id, "⬇️ Скачиваю видео...")

    user_cookie_path = _materialize_user_cookies(repo, user_id, normalized_url)
    cookies_were_used = user_cookie_path is not None or cookie_file_for_url(
        normalized_url, settings
    ) is not None
    file_path, info, _codecs = prepare_media_for_telegram(
        normalized_url,
        quality,
        settings,
        progress_hook=_build_progress_hook(sender, chat_id, status_message_id),
        cookie_file=user_cookie_path,
        embed_subtitles=settings.embed_subtitles,
        debug_context=f"request={request_id} url={normalized_url} quality={quality}",
        on_transcode_start=lambda: sender.edit_status(
            chat_id,
            status_message_id,
            "🔄 Конвертирую видео для совместимости с Telegram — это может занять несколько минут...",
        ),
        on_transcode_progress=_build_transcode_progress_hook(sender, chat_id, status_message_id),
        proxies=proxies,
        on_proxy_result=on_proxy_result,
        redis=redis,
    )

    return file_path, info, user_cookie_path, cookies_were_used


def _enforce_size_limit(
    sender: TelegramSender,
    repo: Repository,
    request_id: int,
    chat_id: int,
    status_message_id: int | None,
    file_path: Path,
    settings: Settings,
    redis,
) -> tuple[Path, int] | None:
    """Compress the file if it exceeds the configured limit; reject if still too large.

    Returns (file_path, file_size_bytes) to continue with, or None when the
    request was rejected (status/message already sent, file already cleaned up).
    """
    file_size_bytes = file_path.stat().st_size
    size_mb = file_size_bytes / (1024 * 1024)

    max_mb = get_limit("max_file_mb", settings, redis)
    if max_mb > 0 and size_mb > max_mb:
        sender.edit_status(
            chat_id,
            status_message_id,
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
        sender.edit_status(
            chat_id,
            status_message_id,
            f"⚠️ Файл слишком большой: {size_mb:.1f} MB. Лимит: {max_mb} MB.",
        )
        file_path.unlink(missing_ok=True)
        return None

    return file_path, file_size_bytes


def _upload_and_cache(
    sender: TelegramSender,
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
    sender.edit_status(req.chat_id, req.status_message_id, f"✅ Скачано {size_mb:.1f} MB. Отправляю...")

    title = info.get("title") if isinstance(info, dict) else None
    caption_title = title if is_rezka_url(req.normalized_url) else None
    width, height, duration = probe_video_dimensions(file_path)
    file_id, file_unique_id, file_type = sender.send_file(
        req.chat_id, file_path, get_caption(settings, caption_title), width=width, height=height, duration=duration
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
    sender.delete_status(req.chat_id, req.status_message_id)

    if settings.delete_local_file_after_telegram_cache:
        file_path.unlink(missing_ok=True)


_AUTORETRY_EXCEPTIONS = (ConnectionError, OperationalError)


@celery_app.task(bind=True, autoretry_for=_AUTORETRY_EXCEPTIONS, retry_backoff=True, retry_kwargs={"max_retries": 3})
def process_download_request(self, request_id: int) -> None:
    settings = get_settings()
    redis = get_redis()
    limiter = RateLimiter(redis)
    sender = get_default_sender()

    # ScopedRepository opens a fresh, short-lived DB session per call instead
    # of one held open for the task's whole duration. Download + ffmpeg
    # transcode/compress can take several minutes with no DB activity at
    # all; holding a single session open across that gap risks its
    # connection going idle long enough for Postgres or the network to close
    # it, which only surfaces as an OperationalError on the next query deep
    # in the task. A fresh session per call re-triggers pool_pre_ping every
    # time, so a dead pooled connection gets transparently replaced instead.
    repo = ScopedRepository()
    req = repo.get_request(request_id)

    if not req:
        logger.warning("Request %s not found", request_id)
        return

    # Snapshot the plain values cleanup/failure-handling needs up front so
    # they're available even if a later DB call fails.
    user_id = req.user_id
    chat_id = req.chat_id
    status_message_id = req.status_message_id
    video_id = req.video_id
    url_hash = req.url_hash
    quality = req.quality
    normalized_url = req.normalized_url

    proxies = _resolve_proxies(repo, settings, normalized_url)
    on_proxy_result = _record_proxy_result(repo, request_id)

    max_duration = get_limit("max_download_duration_seconds", settings, redis)
    if not _check_user_rate_limit(
        sender, repo, limiter, settings, redis, request_id, user_id, chat_id, status_message_id, max_duration
    ):
        return

    video_lock_acquired = False
    user_cookie_path: Path | None = None
    cookies_were_used = False
    # Tracks whichever file on disk this request is currently responsible
    # for, so it can be removed in `finally` if anything fails after it's
    # downloaded — _upload_and_cache already handles cleanup on the success
    # path (per DELETE_LOCAL_FILE_AFTER_TELEGRAM_CACHE), so this is set back
    # to None right after that call succeeds.
    current_media_path: Path | None = None
    try:
        if _try_serve_from_cache(sender, repo, req, request_id, settings):
            return

        video_lock_acquired = _acquire_video_lock_or_reject(
            sender, repo, limiter, request_id, chat_id, status_message_id, url_hash, quality,
            max_duration + _VIDEO_LOCK_POST_PROCESSING_BUFFER_SECONDS,
        )
        if not video_lock_acquired:
            return

        if not _reject_active_livestream(
            sender, repo, request_id, chat_id, status_message_id, normalized_url, settings,
            proxies, on_proxy_result,
        ):
            return

        file_path, info, user_cookie_path, cookies_were_used = _download_and_prepare_media(
            sender, repo, request_id, user_id, chat_id, status_message_id, normalized_url, quality, settings,
            proxies, on_proxy_result, redis,
        )
        current_media_path = file_path

        sized = _enforce_size_limit(
            sender, repo, request_id, chat_id, status_message_id, file_path, settings, redis
        )
        if sized is None:
            current_media_path = None  # _enforce_size_limit already cleaned it up
            return
        file_path, file_size_bytes = sized
        current_media_path = file_path

        _upload_and_cache(sender, repo, req, request_id, file_path, info, file_size_bytes, settings)
        current_media_path = None  # _upload_and_cache already handled cleanup

    except Exception as exc:
        logger.exception("Failed request %s", request_id)

        # autoretry_for makes Celery transparently retry this task after it
        # re-raises below. Only treat the request as permanently failed —
        # and only tell the user — once no more retries are coming;
        # otherwise a transient blip would mark the request "failed" and
        # notify the user before the retry that fixes itself even runs.
        will_autoretry = isinstance(exc, _AUTORETRY_EXCEPTIONS) and self.request.retries < self.max_retries
        if not will_autoretry:
            refresh_key = _google_refresh_key(user_id)
            cookie_refreshed = False
            try:
                recently_refreshed = bool(redis.exists(refresh_key))
                cookie_refreshed = (
                    not recently_refreshed
                    and cookies_were_used
                    and (_is_cookie_error(exc) or _is_youtube_challenge_error(exc))
                    and _try_refresh_google_cookies(repo, user_id)
                )
                if cookie_refreshed:
                    redis.setex(refresh_key, _GOOGLE_REFRESH_COOLDOWN, "1")
            except Exception:
                logger.exception("Cookie-refresh check failed for request %s", request_id)

            _handle_task_failure(
                sender, repo, request_id, chat_id, status_message_id, video_id, exc,
                cookies_were_used=cookies_were_used,
                cookie_refreshed=cookie_refreshed,
                is_rezka=is_rezka_url(normalized_url),
            )
        raise

    finally:
        if user_cookie_path is not None:
            user_cookie_path.unlink(missing_ok=True)
        if current_media_path is not None:
            current_media_path.unlink(missing_ok=True)
        # Redis-only cleanup — must never depend on the DB.
        limiter.release_user_download_slot(user_id)
        if video_lock_acquired:
            limiter.release_video_lock(url_hash, quality)


@celery_app.task
def cleanup_stale_downloads() -> int:
    """Periodic safety-net sweep of downloads/active/ — see app.services.cleanup."""
    return _cleanup_stale_downloads(get_settings())
