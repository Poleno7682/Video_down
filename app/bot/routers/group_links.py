from __future__ import annotations

import logging
from urllib.parse import urlparse

from aiogram import F, Router
from aiogram.exceptions import TelegramBadRequest
from aiogram.enums import ChatMemberStatus
from aiogram.types import ChatMemberUpdated, Message

from app.bot.access import _check_access
from app.core.config import get_settings
from app.db.models import DownloadStatus
from app.db.repository import RequestRepository, UserRepository, VideoRepository
from app.db.session import get_session, run_in_session
from app.services.rate_limiter import RateLimiter, check_rate_limit
from app.services.redis_client import get_redis
from app.services.runtime_config import get_limit
from app.utils.caption import get_caption
from app.utils.platforms import YOUTUBE, detect_platform
from app.utils.quality import normalize_quality
from app.utils.site_behavior import site_behavior
from app.utils.url_tools import URL_RE, is_valid_url, normalize_url, url_hash
from app.worker.tasks import process_download_request

logger = logging.getLogger(__name__)

router = Router()

# Групповые чаты: автоскачивание по ссылкам. Бот в группе молчит всегда,
# кроме самого видео (ответом на сообщение со ссылкой) — ни статусов
# очереди/прогресса, ни ошибок, ни отказов по лимитам.
router.message.filter(F.chat.type.in_({"group", "supergroup"}))

_GREETING = (
    "Привет. Я могу скачать видео с соцсетей. Отправь ссылку в чат и я пришлю скачанное видео. "
    "Поддерживаю TikTok, Instagram, Facebook, YouTube Shorts и множество других соцсетей"
)
_PRESENT = {ChatMemberStatus.MEMBER, ChatMemberStatus.ADMINISTRATOR}


@router.my_chat_member(F.chat.type.in_({"group", "supergroup"}))
async def greet_on_added(event: ChatMemberUpdated) -> None:
    """Одно приветствие, когда бота добавили в группу (не при смене прав)."""
    was_present = event.old_chat_member.status in _PRESENT
    is_present = event.new_chat_member.status in _PRESENT
    if is_present and not was_present:
        try:
            await event.bot.send_message(event.chat.id, _GREETING)
        except Exception:
            logger.warning("Could not greet chat %s", event.chat.id, exc_info=True)


def _extract_supported_url(text: str) -> str | None:
    """Первая ссылка на Facebook / Instagram / TikTok / YouTube Shorts."""
    for match in URL_RE.finditer(text or ""):
        url = match.group(0).strip().rstrip(").,;]")
        if not is_valid_url(url):
            continue
        platform = detect_platform(url)
        if platform is None:
            continue
        # Из YouTube берём только Shorts, обычные ролики в чате игнорируем.
        if platform == YOUTUBE and "/shorts/" not in urlparse(url).path:
            continue
        return url
    return None


@router.message(F.text | F.caption)
async def handle_group_message(message: Message) -> None:
    user = message.from_user
    if user is None or user.is_bot:
        return

    raw_url = _extract_supported_url(message.text or message.caption or "")
    if not raw_url:
        return

    settings = get_settings()
    redis = get_redis()

    allowed, _ = _check_access(user.id, settings, redis)
    if not allowed:
        return
    rate_ok, _ = check_rate_limit(user.id, settings, redis, RateLimiter(redis))
    if not rate_ok:
        return

    try:
        await _enqueue_silently(message, raw_url)
    except Exception:
        logger.exception("Group auto-download failed to enqueue (chat=%s)", message.chat.id)


async def _enqueue_silently(message: Message, raw_url: str) -> None:
    settings = get_settings()
    redis = get_redis()
    user = message.from_user
    normalized = normalize_url(raw_url)
    h = url_hash(normalized)
    quality_value = normalize_quality(settings.default_quality, "720p")

    await run_in_session(lambda session: UserRepository(session).upsert_user(
        user_id=user.id,
        username=user.username,
        first_name=user.first_name,
    ))

    with get_session() as session:
        req_repo = RequestRepository(session)
        video_repo = VideoRepository(session)

        for key, counter in (
            ("user_daily_limit", lambda: req_repo.count_user_today_requests(user.id)),
            ("user_queue_limit", lambda: req_repo.count_user_active_requests(user.id)),
            ("global_queue_limit", req_repo.count_global_active_requests),
        ):
            limit = get_limit(key, settings, redis)
            if limit > 0 and counter() >= limit:
                return

        ready_video = video_repo.get_ready_video(h, quality_value)
        if ready_video and ready_video.telegram_file_id and ready_video.telegram_file_type:
            title = ready_video.title if site_behavior(normalized).caption_uses_resolved_title else None
            method = "reply_video" if ready_video.telegram_file_type.value == "video" else "reply_document"
            try:
                await getattr(message, method)(ready_video.telegram_file_id, caption=get_caption(settings, title))
                return
            except TelegramBadRequest:
                video_repo.invalidate_video_cache(ready_video.id)

        video = video_repo.get_or_create_video(
            original_url=raw_url,
            normalized_url=normalized,
            url_hash=h,
            quality=quality_value,
        )
        # status_message_id=None: воркер не пишет статусы и ошибки в чат.
        req = req_repo.create_request(
            user_id=user.id,
            chat_id=message.chat.id,
            message_id=message.message_id,
            status_message_id=None,
            video_id=video.id,
            original_url=raw_url,
            normalized_url=normalized,
            url_hash=h,
            quality=quality_value,
            status=DownloadStatus.queued,
        )
        task = process_download_request.delay(req.id)
        req_repo.set_request_task_id(req.id, task.id)
