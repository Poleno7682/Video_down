from __future__ import annotations

import logging
import tempfile
import time
from datetime import timedelta
from pathlib import Path
from typing import Callable, Protocol

from sqlalchemy.exc import OperationalError

from app.core.config import Settings, get_settings
from app.db.models import DownloadRequest, DownloadStatus, TelegramFileType, TorrentRequest, UserGoogleToken, Video
from app.db.session import ScopedRepository
from app.db.utils import utcnow
from app.services.cleanup import cleanup_stale_downloads as _cleanup_stale_downloads
from app.services.google_oauth import generate_youtube_cookies, refresh_access_token
from app.services.rate_limiter import RateLimiter
from app.services.redis_client import get_redis
from app.services.runtime_config import get_limit
from app.utils.caption import get_caption
from app.utils.platforms import FACEBOOK, INSTAGRAM, TIKTOK, YOUTUBE, detect_platform
from app.utils.proxy_check import ANTI_BOT_MARKERS
from app.utils.rezka import RezkaResolveError
from app.utils.site_behavior import site_behavior
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
from app.worker.torrent_downloader import (
    TorrentError,
    TorrentProgress,
    TorrentTooLargeError,
    TorrentTooManyFilesError,
    download_torrent,
    remove_and_delete,
)

logger = logging.getLogger(__name__)

# Substrings that indicate yt-dlp needs (fresh) cookies / hit an anti-bot wall.
# Starts from the same base anti-bot markers app.utils.proxy_check uses to
# vet a proxy on /addproxy, extended with markers specific to cookie/auth
# failures during an actual download.
_COOKIE_ERROR_MARKERS = ANTI_BOT_MARKERS + (
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

# Shown by reconcile_stuck_requests, not by process_download_request itself —
# a wedged task's own except/finally never gets to run (see there for why),
# so the user never got any status update at all until this sweep catches it.
_STUCK_REQUEST_FAILURE = (
    "⏱ Загрузка зависла и была прервана по таймауту без ответа от сайта-источника.\n\n"
    "Попробуй отправить ссылку ещё раз."
)

_REZKA_STREAM_FAILURE = (
    "❌ Не удалось получить поток видео с Rezka для выбранной озвучки.\n\n"
    "Обычно это значит, что именно эта озвучка сейчас недоступна на сайте — "
    "другие, как правило, работают. Попробуйте выбрать другую озвучку "
    "(отправьте ссылку заново) или повторите попытку позже."
)

# Distinct from _REZKA_STREAM_FAILURE: shown when the failure happened
# *after* a translator/stream was already resolved successfully — i.e. the
# site itself found a stream, but fetching its video bytes then failed
# (CDN timeout, dead mirror, proxy rejecting the resolved link). Blaming
# the translator choice here would be misleading, since another translator
# would likely hit the very same CDN problem.
_REZKA_CDN_FAILURE = (
    "❌ Не удалось скачать видео с сервера Rezka — похоже, временная проблема "
    "с сервером видео (CDN), а не с выбранной озвучкой.\n\n"
    "Попробуйте повторить попытку через пару минут; если не поможет — "
    "попробуйте другую озвучку (отправьте ссылку заново)."
)

# Proper-case brand names for the cookie-failure messages below. Only
# platforms detect_platform() can return (and that the /cookies upload flow
# accepts, see app.bot.routers.cookies) belong here — anything else falls
# back to _GENERIC_FAILURE rather than naming a platform we can't be sure of.
_PLATFORM_DISPLAY_NAMES: dict[str, str] = {
    YOUTUBE: "YouTube",
    INSTAGRAM: "Instagram",
    TIKTOK: "TikTok",
    FACEBOOK: "Facebook",
}


def _cookie_failure_message(platform: str | None) -> str:
    name = _PLATFORM_DISPLAY_NAMES.get(platform or "")
    if not name:
        return _GENERIC_FAILURE
    return (
        f"❌ {name} требует ваши cookies (защита от ботов).\n\n"
        "Экспортируйте cookies в формате Netscape (cookies.txt) из браузера, где вы вошли "
        f"в аккаунт, и пришлите файл боту под именем <code>{platform}.txt</code>.\n"
        "Подробнее: /cookies"
    )


def _stale_cookie_failure_message(platform: str | None) -> str:
    name = _PLATFORM_DISPLAY_NAMES.get(platform or "")
    if not name:
        return _GENERIC_FAILURE
    return (
        f"❌ Ваши {name} cookies устарели — {name} их сбросил.\n\n"
        "Экспортируйте <b>свежий</b> cookies.txt из браузера, где вы залогинены, "
        f"и пришлите файл <code>{platform}.txt</code> заново.\n"
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

_TORRENT_GENERIC_FAILURE = (
    "❌ Не получилось скачать содержимое торрента. Возможные причины: нет "
    "доступных пиров/сидов, магнет-ссылка недействительна, либо трекеры не отвечают."
)

_TORRENT_METADATA_TIMEOUT_FAILURE = (
    "⏱ Не удалось получить метаданные торрента — нет ответа от пиров/DHT.\n\n"
    "Попробуй другую ссылку или повтори попытку позже."
)

_TORRENT_DURATION_EXCEEDED_FAILURE = (
    "⏱ Скачивание торрента заняло слишком много времени и было прервано."
)

_TORRENT_FILE_TOO_LARGE_FAILURE = "⚠️ Один из файлов торрента слишком большой для Telegram."

_TORRENT_INVALID_FILE_FAILURE = "❌ Файл .torrent повреждён или недействителен."

# Shown by reconcile_stuck_torrent_requests, mirroring _STUCK_REQUEST_FAILURE
# above but for the torrent pipeline's own table/task.
_STUCK_TORRENT_REQUEST_FAILURE = (
    "⏱ Скачивание торрента зависло и было прервано по таймауту.\n\n"
    "Попробуй отправить магнет-ссылку ещё раз."
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

# process_torrent_request needs its own Celery hard/soft time limit rather
# than the video pipeline's task_time_limit/task_soft_time_limit (celery_app's
# global default, derived from max_download_duration_seconds) - a torrent's
# own settings.torrent_max_duration_seconds deadline is checked from inside
# download_torrent's poll loop, but Celery's hard limit would otherwise
# SIGKILL the task before that internal check ever gets a chance to fire
# cleanly (same "wedged task never runs its own except/finally" problem
# reconcile_stuck_requests exists to clean up afterwards - see there). The
# buffer covers metadata wait + uploading every file to Telegram after the
# download itself finishes.
_TORRENT_TASK_TIME_LIMIT_BUFFER_SECONDS = 300
_torrent_task_settings = get_settings()
_TORRENT_TASK_TIME_LIMIT = (
    _torrent_task_settings.torrent_metadata_timeout_seconds
    + _torrent_task_settings.torrent_max_duration_seconds
    + _TORRENT_TASK_TIME_LIMIT_BUFFER_SECONDS
)
_TORRENT_TASK_SOFT_TIME_LIMIT = max(60, _TORRENT_TASK_TIME_LIMIT - 60)


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


# Narrow, per-helper repository protocols instead of typing every helper
# against the full ~24-method Repository facade it's handed at runtime
# (ScopedRepository, structurally compatible but not a Repository
# subclass — these protocols describe exactly what each helper touches,
# so that's true by construction rather than by convention).
class _RequestStatusRepo(Protocol):
    def update_request_status(
        self, request_id: int, status: DownloadStatus, error: str | None = None, finished: bool = False,
    ) -> None: ...


class _UserCookiesRepo(Protocol):
    def get_user_cookies(self, user_id: int, platform: str) -> str | None: ...


class _TaskFailureRepo(_RequestStatusRepo, Protocol):
    def mark_video_failed(self, video_id: int, error: str) -> None: ...


class _ReconcileRepo(_TaskFailureRepo, Protocol):
    def get_stale_active_requests(self, cutoff) -> list[DownloadRequest]: ...


class _CacheServingRepo(_RequestStatusRepo, Protocol):
    def get_ready_video(self, url_hash: str, quality: str) -> Video | None: ...


class _ProxyPoolRepo(Protocol):
    def get_enabled_proxy_urls(self) -> list[str]: ...


class _ProxyResultRepo(Protocol):
    def record_proxy_success(self, url: str) -> None: ...
    def record_proxy_failure(self, url: str) -> None: ...


class _DownloadPrepRepo(_RequestStatusRepo, _UserCookiesRepo, Protocol):
    pass


class _MediaUploadRepo(_RequestStatusRepo, Protocol):
    def mark_video_ready(
        self,
        video_id: int,
        title: str | None,
        telegram_file_id: str,
        telegram_file_unique_id: str | None,
        telegram_file_type: TelegramFileType,
        local_file_path: str | None,
        file_size_bytes: int | None,
    ) -> None: ...


def _materialize_user_cookies(repo: _UserCookiesRepo, user_id: int, url: str) -> Path | None:
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


def _maybe_refresh_google_cookies(
    repo: _GoogleCookieRepo, redis, request_id: int, user_id: int, exc: Exception, cookies_were_used: bool,
) -> bool:
    """Attempt exactly one Google-cookie refresh per _GOOGLE_REFRESH_COOLDOWN
    window when a failed download looks like it was caused by stale
    cookies/a YouTube challenge. Returns whether a refresh actually
    happened, so the caller can tell the user to just retry instead of
    showing a generic failure message.
    """
    refresh_key = _google_refresh_key(user_id)
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
        return cookie_refreshed
    except Exception:
        logger.exception("Cookie-refresh check failed for request %s", request_id)
        return False


def _handle_task_failure(
    sender: TelegramSender,
    repo: _TaskFailureRepo,
    request_id: int,
    chat_id: int,
    status_message_id: int | None,
    video_id: int | None,
    exc: Exception,
    url: str,
    *,
    cookies_were_used: bool = False,
    cookie_refreshed: bool = False,
    is_rezka: bool = False,
    is_rezka_resolve_error: bool = False,
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
        platform = detect_platform(url)
        message = (
            _stale_cookie_failure_message(platform)
            if cookies_were_used
            else _cookie_failure_message(platform)
        )
    elif is_rezka_resolve_error:
        message = _REZKA_STREAM_FAILURE
    elif is_rezka:
        message = _REZKA_CDN_FAILURE
    else:
        message = _GENERIC_FAILURE
    sender.edit_status(chat_id, status_message_id, message)


class _TorrentRequestStatusRepo(Protocol):
    def update_torrent_request_status(
        self, request_id: int, status: DownloadStatus, error: str | None = None, finished: bool = False,
    ) -> None: ...


class _TorrentReconcileRepo(_TorrentRequestStatusRepo, Protocol):
    def get_stale_active_torrent_requests(self, cutoff) -> list[TorrentRequest]: ...


def _handle_torrent_task_failure(
    sender: TelegramSender,
    repo: _TorrentRequestStatusRepo,
    request_id: int,
    chat_id: int,
    status_message_id: int | None,
    exc: Exception,
    settings: Settings,
) -> None:
    """Record the failure in the DB and send a Telegram notification.

    Mirrors _handle_task_failure's shape for the video pipeline, but keyed
    off exception type rather than sniffing yt-dlp error text - the torrent
    pipeline's failure modes (download_torrent's own exceptions) are already
    typed, unlike yt-dlp's plain-string DownloadError.
    """
    error_msg = f"{type(exc).__name__}: {exc}"
    try:
        repo.update_torrent_request_status(request_id, DownloadStatus.failed, error=error_msg, finished=True)
    except Exception:
        logger.exception("Could not persist failure status for torrent request %s", request_id)

    if isinstance(exc, TorrentTooLargeError):
        message = f"⚠️ Содержимое торрента превышает лимит {settings.torrent_max_total_size_mb} MB."
    elif isinstance(exc, TorrentTooManyFilesError):
        message = f"⚠️ В торренте слишком много файлов (лимит {settings.torrent_max_file_count})."
    elif isinstance(exc, TelegramEntityTooLarge):
        message = _TORRENT_FILE_TOO_LARGE_FAILURE
    elif isinstance(exc, TorrentError) and "invalid .torrent file" in str(exc).lower():
        message = _TORRENT_INVALID_FILE_FAILURE
    elif isinstance(exc, TorrentError) and "metadata" in str(exc).lower():
        message = _TORRENT_METADATA_TIMEOUT_FAILURE
    elif isinstance(exc, TorrentError) and "time limit" in str(exc).lower():
        message = _TORRENT_DURATION_EXCEEDED_FAILURE
    else:
        message = _TORRENT_GENERIC_FAILURE
    sender.edit_status(chat_id, status_message_id, message)


def reconcile_stuck_torrent_requests(repo: _TorrentReconcileRepo, sender: TelegramSender, max_age_seconds: int) -> int:
    """Torrent-pipeline counterpart to reconcile_stuck_requests below - same
    reasoning applies (a Celery hard time-limit SIGKILL never lets
    process_torrent_request's own except/finally run), just against
    TorrentRequest instead of DownloadRequest.
    """
    cutoff = utcnow() - timedelta(seconds=max_age_seconds)
    stuck = repo.get_stale_active_torrent_requests(cutoff)
    for req in stuck:
        logger.warning(
            "Reconciling stuck torrent request %s (status=%s, updated_at=%s)",
            req.id, req.status, req.updated_at,
        )
        repo.update_torrent_request_status(
            req.id, DownloadStatus.failed,
            error="Reconciled: exceeded torrent_max_duration_seconds without reaching a terminal status",
            finished=True,
        )
        if req.status_message_id:
            sender.edit_status(req.chat_id, req.status_message_id, _STUCK_TORRENT_REQUEST_FAILURE)
    return len(stuck)


def reconcile_stuck_requests(repo: _ReconcileRepo, sender: TelegramSender, max_age_seconds: int) -> int:
    """Fail out DB requests stuck in queued/downloading/sending long after
    Celery's hard time limit (task_time_limit) would have killed whatever
    worker process was running them.

    That hard limit kills a wedged task by sending SIGKILL straight to its
    child process from the pool supervisor — none of process_download_request's
    own code, including its `except`/`finally`, ever runs, so the DB row is
    abandoned at whatever non-terminal status it had. Nothing else updates it
    afterwards: it stays "active" forever, permanently occupying that user's
    (and the global) active-request slot. This periodic sweep is what
    actually clears it.
    """
    cutoff = utcnow() - timedelta(seconds=max_age_seconds)
    stuck = repo.get_stale_active_requests(cutoff)
    for req in stuck:
        logger.warning(
            "Reconciling stuck request %s (status=%s, updated_at=%s)",
            req.id, req.status, req.updated_at,
        )
        repo.update_request_status(
            req.id, DownloadStatus.failed,
            error="Reconciled: exceeded max_download_duration_seconds without reaching a terminal status",
            finished=True,
        )
        if req.video_id:
            repo.mark_video_failed(req.video_id, "stuck request reconciled")
        if req.status_message_id:
            sender.edit_status(req.chat_id, req.status_message_id, _STUCK_REQUEST_FAILURE)
    return len(stuck)


def _try_serve_from_cache(
    sender: TelegramSender,
    repo: _CacheServingRepo,
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
    caption_title = ready_video.title if site_behavior(req.normalized_url).caption_uses_resolved_title else None
    sender.send_cached(
        req.chat_id, ready_video.telegram_file_id, ready_video.telegram_file_type,
        get_caption(settings, caption_title), reply_to_message_id=_reply_target(req),
    )
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
def _reply_target(req: DownloadRequest) -> int | None:
    """Сообщение, на которое отвечаем видео: только в группах (chat_id < 0).

    В личке видео уходит обычным сообщением, как и раньше.
    """
    return req.message_id if req.chat_id < 0 else None


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


def _format_bytes_per_sec(rate: float) -> str:
    if rate < 1024:
        return f"{rate:.0f} B/s"
    if rate < 1024 * 1024:
        return f"{rate / 1024:.0f} KB/s"
    return f"{rate / (1024 * 1024):.2f} MB/s"


def _format_connected_available(connected: int, available: int) -> str:
    """"connected/available", or just "connected" when the swarm hasn't
    reported a scrape count yet (libtorrent uses -1 for "unknown")."""
    if available is not None and available >= 0:
        return f"{connected}/{available}"
    return str(connected)


def _format_torrent_progress_text(progress: TorrentProgress) -> str:
    percent = progress.progress * 100
    return (
        f"⬇️ Скачиваю торрент: {percent:.1f}%\n"
        f"⚡ Скорость: {_format_bytes_per_sec(progress.download_rate)}\n"
        f"🌱 Сиды (подключено/доступно): "
        f"{_format_connected_available(progress.seeds_connected, progress.seeds_available)}\n"
        f"👥 Пиры (подключено/доступно): "
        f"{_format_connected_available(progress.peers_connected, progress.peers_available)}"
    )


def _build_torrent_progress_hook(
    sender: TelegramSender, chat_id: int, status_message_id: int | None
) -> Callable[[TorrentProgress], None]:
    """Return a download_torrent progress hook, throttled to 1 update per 5 s."""
    last_update = 0.0

    def _hook(progress: TorrentProgress) -> None:
        nonlocal last_update
        now = time.time()
        if now - last_update < 5:
            return
        last_update = now
        sender.edit_status(chat_id, status_message_id, _format_torrent_progress_text(progress))

    return _hook


def _check_user_rate_limit(
    sender: TelegramSender,
    repo: _RequestStatusRepo,
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
    repo: _RequestStatusRepo,
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


def _resolve_proxies(repo: _ProxyPoolRepo, settings: Settings, url: str) -> list[str]:
    """Proxies to try, least-failed first. Falls back to YTDLP_PROXY when the
    admin-managed pool (DB) is empty, so an env-only setup keeps working.

    Only used for YouTube and rezka.ag, and each uses a different route:

    - YouTube: the shared free/cheap pool (DB-managed, falling back to
      YTDLP_PROXY when empty) — routes around its datacenter-IP anti-bot
      check. Every proxy in that pool is unreliable enough (dead, wrong
      scheme, geo-blocked) that routing rezka through it too just adds
      failure modes it never had — hence rezka doesn't use it at all.
    - rezka: only REZKA_VPN_PROXY_URL (the local `vpn` container), a single
      dedicated route, tried after direct has already failed — instead of
      first burning through the whole unrelated YouTube pool.
    """
    dedicated_setting = site_behavior(url).dedicated_proxy_setting
    if dedicated_setting:
        proxy_url = getattr(settings, dedicated_setting)
        return [proxy_url] if proxy_url else []
    if detect_platform(url) != YOUTUBE:
        return []
    db_proxies = repo.get_enabled_proxy_urls()
    return db_proxies if db_proxies else ([settings.ytdlp_proxy] if settings.ytdlp_proxy else [])


def _record_proxy_result(repo: _ProxyResultRepo, request_id: int) -> Callable[[str | None, bool], None]:
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
    repo: _RequestStatusRepo,
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

    Skipped entirely for rezka: it's a VOD-only site (no live streams), and
    its page URLs aren't a yt-dlp-supported extractor to begin with — the
    generic-extractor precheck below can only ever fail on it with
    "Unsupported URL", regardless of proxy, so running it would just burn
    ~30-90s cycling the whole proxy pool for a foregone conclusion on every
    single rezka request.
    """
    if site_behavior(normalized_url).skip_livestream_check or not is_active_livestream(
        normalized_url, proxies, on_proxy_result
    ):
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
    repo: _DownloadPrepRepo,
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
    repo: _RequestStatusRepo,
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
    repo: _MediaUploadRepo,
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
    caption_title = title if site_behavior(req.normalized_url).caption_uses_resolved_title else None
    width, height, duration = probe_video_dimensions(file_path)
    file_id, file_unique_id, file_type = sender.send_file(
        req.chat_id, file_path, get_caption(settings, caption_title),
        width=width, height=height, duration=duration, reply_to_message_id=_reply_target(req),
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
            cookie_refreshed = _maybe_refresh_google_cookies(
                repo, redis, request_id, user_id, exc, cookies_were_used
            )
            is_rezka = site_behavior(normalized_url).has_custom_resolver
            _handle_task_failure(
                sender, repo, request_id, chat_id, status_message_id, video_id, exc, normalized_url,
                cookies_were_used=cookies_were_used,
                cookie_refreshed=cookie_refreshed,
                is_rezka=is_rezka,
                # Only the resolve step (translator/quality lookup on the
                # rezka page itself) raises RezkaResolveError; downloader.py
                # wraps it with `raise ... from exc`, so __cause__ tells
                # apart "this translator has no stream" from a downstream
                # CDN/network failure after resolution already succeeded —
                # see _REZKA_STREAM_FAILURE vs _REZKA_CDN_FAILURE.
                is_rezka_resolve_error=is_rezka and isinstance(exc.__cause__, RezkaResolveError),
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


@celery_app.task(
    bind=True,
    autoretry_for=_AUTORETRY_EXCEPTIONS,
    retry_backoff=True,
    retry_kwargs={"max_retries": 3},
    time_limit=_TORRENT_TASK_TIME_LIMIT,
    soft_time_limit=_TORRENT_TASK_SOFT_TIME_LIMIT,
)
def process_torrent_request(self, request_id: int) -> None:
    """Download every file of a magnet link or uploaded .torrent file, send
    them all to Telegram, then immediately remove the torrent and wipe the
    downloaded bytes - the bot only ever leeches, never seeds past the
    moment the last file is sent (see app.worker.torrent_downloader for how
    that's enforced). Exactly one of req.magnet_uri/req.torrent_file is set;
    download_torrent's torrent_data kwarg takes precedence when present.

    Deliberately much simpler than process_download_request: no quality
    selection, no Telegram file_id caching/reuse (every magnet link or
    torrent file is a fresh job with its own, arbitrary set of files), no proxy/cookie
    handling, no transcoding - just download, send, delete.
    """
    settings = get_settings()
    redis = get_redis()
    limiter = RateLimiter(redis)
    sender = get_default_sender()
    repo = ScopedRepository()

    req = repo.get_torrent_request(request_id)
    if not req:
        logger.warning("Torrent request %s not found", request_id)
        return

    user_id = req.user_id
    chat_id = req.chat_id
    status_message_id = req.status_message_id
    magnet_uri = req.magnet_uri
    torrent_file = req.torrent_file

    # Shares the same Redis slot/key as the video pipeline's download slot:
    # a user's max_active_downloads_per_user quota covers video and torrent
    # jobs together, since both compete for the same worker capacity.
    max_active = get_limit("max_active_downloads_per_user", settings, redis)
    slot_acquired = max_active <= 0 or limiter.acquire_user_download_slot(
        user_id, max_active, settings.torrent_max_duration_seconds
    )
    if not slot_acquired:
        repo.update_torrent_request_status(
            request_id, DownloadStatus.rate_limited,
            error="Too many active downloads for user", finished=True,
        )
        sender.edit_status(chat_id, status_message_id, "⚠️ У тебя уже есть активная загрузка. Попробуй позже.")
        return

    lt_session = None
    handle = None
    work_dir: Path | None = None
    try:
        repo.update_torrent_request_status(request_id, DownloadStatus.downloading)
        sender.edit_status(chat_id, status_message_id, "🧲 Получаю метаданные торрента...")

        def _on_metadata(info_hash: str, name: str, files) -> None:
            repo.set_torrent_request_metadata(request_id, info_hash, name or None)
            total_mb = sum(f.size_bytes for f in files) / (1024 * 1024)
            sender.edit_status(
                chat_id, status_message_id,
                f"🧲 {name or info_hash}\n"
                f"Файлов: {len(files)}, размер: {total_mb:.1f} MB\n"
                "Начинаю скачивание...",
            )

        lt_session, handle, work_dir, file_paths = download_torrent(
            magnet_uri, settings,
            torrent_data=torrent_file,
            on_metadata=_on_metadata,
            on_progress=_build_torrent_progress_hook(sender, chat_id, status_message_id),
        )

        repo.update_torrent_request_status(request_id, DownloadStatus.sending)
        sender.edit_status(chat_id, status_message_id, f"✅ Скачано. Отправляю {len(file_paths)} файл(ов)...")

        max_mb = get_limit("max_file_mb", settings, redis)
        sent = 0
        skipped_names: list[str] = []
        for file_path in file_paths:
            size_mb = file_path.stat().st_size / (1024 * 1024)
            if max_mb > 0 and size_mb > max_mb:
                skipped_names.append(file_path.name)
                continue
            sender.send_file(chat_id, file_path, get_caption(settings, file_path.name))
            sent += 1
            repo.set_torrent_files_sent(request_id, sent)

        repo.update_torrent_request_status(request_id, DownloadStatus.done, finished=True)
        if skipped_names:
            sender.edit_status(
                chat_id, status_message_id,
                f"✅ Отправлено {sent} файл(ов).\n"
                f"⚠️ Пропущено (больше лимита {max_mb} MB): " + ", ".join(skipped_names),
            )
        else:
            sender.delete_status(chat_id, status_message_id)

    except Exception as exc:
        logger.exception("Failed torrent request %s", request_id)
        will_autoretry = isinstance(exc, _AUTORETRY_EXCEPTIONS) and self.request.retries < self.max_retries
        if not will_autoretry:
            _handle_torrent_task_failure(sender, repo, request_id, chat_id, status_message_id, exc, settings)
        raise

    finally:
        # Runs on every path - success, failure, or autoretry - so a magnet
        # download is never left seeding or sitting on disk one moment
        # longer than it takes to send its files.
        if lt_session is not None and work_dir is not None:
            remove_and_delete(lt_session, handle, work_dir)
        limiter.release_user_download_slot(user_id)


@celery_app.task
def cleanup_stale_downloads() -> int:
    """Periodic safety-net sweep of downloads/active/ — see app.services.cleanup."""
    return _cleanup_stale_downloads(get_settings())


@celery_app.task
def reconcile_stuck_download_requests() -> int:
    """Periodic safety-net for DB rows a hard-killed worker never got to update — see reconcile_stuck_requests."""
    settings = get_settings()
    max_age = settings.max_download_duration_seconds + settings.stuck_request_reconcile_buffer_seconds
    return reconcile_stuck_requests(ScopedRepository(), get_default_sender(), max_age)


@celery_app.task
def reconcile_stuck_torrent_download_requests() -> int:
    """Periodic safety-net for TorrentRequest rows a hard-killed worker never got to update — see reconcile_stuck_torrent_requests."""
    settings = get_settings()
    max_age = (
        settings.torrent_metadata_timeout_seconds
        + settings.torrent_max_duration_seconds
        + settings.stuck_request_reconcile_buffer_seconds
    )
    return reconcile_stuck_torrent_requests(ScopedRepository(), get_default_sender(), max_age)
