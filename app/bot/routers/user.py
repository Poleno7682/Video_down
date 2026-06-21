from __future__ import annotations

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.types import CallbackQuery, Message

from app.bot.access import _check_access
from app.core.config import get_settings
from app.db.repository import RequestRepository, UserRepository
from app.db.session import get_session
from app.keyboards.quality import quality_keyboard
from app.services.rate_limiter import RateLimiter, check_rate_limit
from app.services.redis_client import get_redis
from app.services.runtime_config import get_limit
from app.utils.quality import normalize_quality

router = Router()

HELP_TEXT = (
    "Пришли ссылку на видео, и я скачаю его через очередь.\n\n"
    "Команды:\n"
    "/quality — выбрать качество\n"
    "/status — краткая информация\n"
    "/link_google — привязать Google-аккаунт (YouTube)\n"
    "/unlink_google — отвязать Google-аккаунт"
)


@router.message(Command("start", "help"))
async def start(message: Message) -> None:
    with get_session() as session:
        UserRepository(session).upsert_user(
            user_id=message.from_user.id,
            username=message.from_user.username,
            first_name=message.from_user.first_name,
        )
    await message.answer(HELP_TEXT)


@router.message(Command("quality"))
async def quality(message: Message) -> None:
    await message.answer("Выбери качество:", reply_markup=quality_keyboard())


@router.callback_query(F.data.startswith("quality:"))
async def set_quality(callback: CallbackQuery) -> None:
    settings = get_settings()
    redis = get_redis()

    allowed, denial_msg = _check_access(callback.from_user.id, settings, redis)
    if not allowed:
        await callback.answer(denial_msg, show_alert=True)
        return

    limiter = RateLimiter(redis)
    allowed, ban_ttl = check_rate_limit(callback.from_user.id, settings, redis, limiter)
    if not allowed:
        await callback.answer(f"⛔ Слишком быстро. Подожди {ban_ttl} сек.", show_alert=True)
        return

    quality_value = normalize_quality(callback.data.split(":", 1)[1])
    redis.setex(
        f"user_quality:{callback.from_user.id}",
        settings.cache_ttl_hours * 3600,
        quality_value,
    )
    await callback.answer("Сохранено")
    await callback.message.edit_text(f"✅ Качество выбрано: {quality_value}")


@router.message(Command("status"))
async def status(message: Message) -> None:
    settings = get_settings()
    redis = get_redis()

    allowed, denial_msg = _check_access(message.from_user.id, settings, redis)
    if not allowed:
        await message.answer(denial_msg)
        return

    with get_session() as session:
        repo = RequestRepository(session)
        user_active = repo.count_user_active_requests(message.from_user.id)
        global_active = repo.count_global_active_requests()
        today = repo.count_user_today_requests(message.from_user.id)

    queue_limit = get_limit("user_queue_limit", settings, redis)
    global_limit = get_limit("global_queue_limit", settings, redis)
    daily_limit = get_limit("user_daily_limit", settings, redis)

    await message.answer(
        f"Твои активные задачи: {user_active}/{queue_limit}\n"
        f"Глобальная очередь: {global_active}/{global_limit}\n"
        f"Твои запросы за 24 часа: {today}/{daily_limit}"
    )
