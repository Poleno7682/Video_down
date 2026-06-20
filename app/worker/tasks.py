from __future__ import annotations

import logging
import time

from app.core.config import get_settings
from app.db.models import DownloadStatus
from app.db.repository import Repository
from app.db.session import get_session
from app.services.rate_limiter import RateLimiter
from app.services.redis_client import get_redis
from app.worker.celery_app import celery_app
from app.worker.downloader import download_video
from app.worker.telegram_sender import edit_status, send_cached, send_file

logger = logging.getLogger(__name__)

_MAX_CAPTION = 1024


def _build_caption(title: str | None, quality: str) -> str:
    suffix = f"Готово ✅ | {quality}"
    if not title:
        return suffix
    max_title = _MAX_CAPTION - len(suffix) - 2  # 2 for "\n\n"
    if len(title) > max_title:
        title = title[: max_title - 1] + "…"
    return f"{title}\n\n{suffix}"


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
    edit_status(
        req.chat_id,
        req.status_message_id,
        "❌ Не получилось скачать видео. Возможные причины: приватное видео, "
        "устаревшие cookies, блокировка VPS или изменение защиты сайта.",
    )


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
        try:
            ready_video = repo.get_ready_video(req.url_hash, req.quality)
            if ready_video and ready_video.telegram_file_id and ready_video.telegram_file_type:
                repo.update_request_status(request_id, DownloadStatus.sending)
                edit_status(req.chat_id, req.status_message_id, "⚡ Нашёл готовый Telegram file_id. Отправляю...")
                send_cached(req.chat_id, ready_video.telegram_file_id, ready_video.telegram_file_type, "⚡ Готово из Telegram-кэша")
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

            file_path, info = download_video(req.normalized_url, req.quality, settings, progress_hook=progress_hook)

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
            caption = _build_caption(title, req.quality)

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
            edit_status(req.chat_id, req.status_message_id, "✅ Готово. Telegram file_id сохранён в PostgreSQL.")

            if settings.delete_local_file_after_telegram_cache:
                file_path.unlink(missing_ok=True)

        except Exception as exc:
            logger.exception("Failed request %s", request_id)
            _handle_task_failure(repo, request_id, req, exc)
            raise

        finally:
            limiter.release_user_download_slot(req.user_id)
            if video_lock_acquired:
                limiter.release_video_lock(req.url_hash, req.quality)
