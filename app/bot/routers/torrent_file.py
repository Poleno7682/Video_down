from __future__ import annotations

import logging

from aiogram import Bot, F, Router
from aiogram.types import Message

from app.bot.filters import TorrentFileFilter
from app.bot.routers.url_handler import enqueue_torrent_download
from app.core.config import get_settings
from app.db.repository import UserRepository
from app.db.session import run_in_session
from app.services.rate_limiter import RateLimiter, check_rate_limit
from app.services.redis_client import get_redis

router = Router()
logger = logging.getLogger(__name__)

# .torrent files are a few KB to a few hundred KB even for huge multi-file
# torrents (it's just piece hashes + a file list) - this is generous
# headroom, not a real-world size expectation.
_MAX_TORRENT_FILE_BYTES = 5 * 1024 * 1024


# Must be registered ahead of cookies.py's catch-all F.document handler (see
# app.bot.router) so an uploaded .torrent file reaches this handler instead
# of cookies' "unrecognised platform filename" reply.
@router.message(F.document, TorrentFileFilter())
async def handle_torrent_file(message: Message, bot: Bot) -> None:
    settings = get_settings()
    redis = get_redis()
    limiter = RateLimiter(redis)
    user_id = message.from_user.id

    allowed, ban_ttl = check_rate_limit(user_id, settings, redis, limiter)
    if not allowed:
        await message.answer(f"⛔ Слишком много сообщений. Временный бан: {ban_ttl} сек.")
        return

    document = message.document
    if document.file_size and document.file_size > _MAX_TORRENT_FILE_BYTES:
        await message.answer("⚠️ Файл .torrent слишком большой.")
        return

    try:
        buffer = await bot.download(document)
        torrent_data = buffer.read()
    except Exception:
        logger.exception("Failed to download .torrent file from user %s", user_id)
        await message.answer("❌ Не удалось прочитать файл торрента. Попробуйте ещё раз.")
        return

    await run_in_session(lambda session: UserRepository(session).upsert_user(
        user_id=user_id,
        username=message.from_user.username,
        first_name=message.from_user.first_name,
    ))
    await enqueue_torrent_download(
        message, user_id, message.chat.id, message.message_id, torrent_file=torrent_data,
    )
