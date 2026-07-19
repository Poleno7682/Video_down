from __future__ import annotations

import logging

from aiogram import F, Router
from aiogram.exceptions import TelegramBadRequest
from aiogram.types import Message

from app.core.config import get_settings
from app.db.models import DownloadStatus
from app.db.repository import RequestRepository, UserRepository, VideoRepository
from app.db.session import get_session
from app.services.rate_limiter import RateLimiter, check_rate_limit
from app.services.redis_client import get_redis
from app.services.runtime_config import get_limit
from app.utils.caption import get_caption
from app.utils.quality import normalize_quality
from app.utils.url_tools import extract_url, is_valid_url, normalize_url, url_hash
from app.worker.tasks import process_download_request

router = Router()
logger = logging.getLogger(__name__)


async def send_cached_file(message: Message, file_id: str, file_type: str) -> None:
    caption = get_caption(get_settings())
    if file_type == "video":
        await message.answer_video(file_id, caption=caption)
    elif file_type == "audio":
        await message.answer_audio(file_id, caption=caption)
    else:
        await message.answer_document(file_id, caption=caption)


# Must be the last router included — these are the most generic handlers
# and will swallow any message that hasn't been matched by earlier routers.

@router.message(F.text)
async def handle_link(message: Message) -> None:
    await _process_url_message(message, message.text or "", reply_on_no_url=True)


@router.message(F.caption)
async def handle_caption_link(message: Message) -> None:
    await _process_url_message(message, message.caption or "", reply_on_no_url=False)


async def _process_url_message(message: Message, text: str, reply_on_no_url: bool) -> None:
    settings = get_settings()
    redis = get_redis()
    limiter = RateLimiter(redis)

    user_id = message.from_user.id

    allowed, ban_ttl = check_rate_limit(user_id, settings, redis, limiter)
    if not allowed:
        await message.answer(f"⛔ Слишком много сообщений. Временный бан: {ban_ttl} сек.")
        return

    raw_url = extract_url(text)
    if not raw_url:
        if reply_on_no_url:
            await message.answer("Пришли обычную ссылку на видео.")
        return

    if not is_valid_url(raw_url):
        await message.answer("Ссылка выглядит некорректно.")
        return

    normalized = normalize_url(raw_url)
    h = url_hash(normalized)
    quality_value = redis.get(f"user_quality:{user_id}") or settings.default_quality
    quality_value = normalize_quality(quality_value, settings.default_quality)

    with get_session() as session:
        user_repo = UserRepository(session)
        req_repo = RequestRepository(session)
        video_repo = VideoRepository(session)

        user_repo.upsert_user(
            user_id=user_id,
            username=message.from_user.username,
            first_name=message.from_user.first_name,
        )

        daily_limit = get_limit("user_daily_limit", settings, redis)
        if daily_limit > 0 and req_repo.count_user_today_requests(user_id) >= daily_limit:
            await message.answer("⚠️ Дневной лимит запросов исчерпан.")
            return

        queue_limit = get_limit("user_queue_limit", settings, redis)
        if queue_limit > 0 and req_repo.count_user_active_requests(user_id) >= queue_limit:
            await message.answer("⚠️ У тебя слишком много активных задач в очереди.")
            return

        global_limit = get_limit("global_queue_limit", settings, redis)
        if global_limit > 0 and req_repo.count_global_active_requests() >= global_limit:
            await message.answer("⚠️ Сервер сейчас перегружен. Попробуй позже.")
            return

        ready_video = video_repo.get_ready_video(h, quality_value)
        if ready_video and ready_video.telegram_file_id and ready_video.telegram_file_type:
            try:
                await send_cached_file(message, ready_video.telegram_file_id, ready_video.telegram_file_type.value)
                return
            except TelegramBadRequest:
                logger.warning(
                    "Cached file_id for video %s is invalid, clearing and re-queuing",
                    ready_video.id,
                )
                video_repo.invalidate_video_cache(ready_video.id)
                await message.answer("⚠️ Кэш устарел, скачиваю заново...")

        video = video_repo.get_or_create_video(
            original_url=raw_url,
            normalized_url=normalized,
            url_hash=h,
            quality=quality_value,
        )

        status_msg = await message.answer(
            "🧾 Задача добавлена в очередь.\n"
            f"Качество: {quality_value}"
        )

        req = req_repo.create_request(
            user_id=user_id,
            chat_id=message.chat.id,
            message_id=message.message_id,
            status_message_id=status_msg.message_id,
            video_id=video.id,
            original_url=raw_url,
            normalized_url=normalized,
            url_hash=h,
            quality=quality_value,
            status=DownloadStatus.queued,
        )

        task = process_download_request.delay(req.id)
        req_repo.set_request_task_id(req.id, task.id)
