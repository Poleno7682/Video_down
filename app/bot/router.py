from __future__ import annotations

import logging

from aiogram import F, Router
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import Command
from aiogram.types import CallbackQuery, Message
from redis import Redis

from app.core.config import Settings, get_settings
from app.db.models import DownloadStatus
from app.db.repository import Repository
from app.db.session import get_session
from app.keyboards.admin import admin_keyboard
from app.keyboards.quality import quality_keyboard
from app.services.rate_limiter import RateLimiter
from app.services.redis_client import get_redis
from app.utils.quality import normalize_quality
from app.utils.url_tools import extract_url, is_valid_url, normalize_url, url_hash
from app.worker.tasks import process_download_request


router = Router()
logger = logging.getLogger(__name__)

# Redis keys
_KEY_BOT_DISABLED = "bot:disabled"
_KEY_TRUSTED_USERS = "trusted_users"

HELP_TEXT = (
    "Пришли ссылку на видео, и я скачаю его через очередь.\n\n"
    "Команды:\n"
    "/quality — выбрать качество\n"
    "/status — краткая информация\n\n"
    "Если ролик уже был скачан, я сразу отправлю сохранённый Telegram file_id без повторного скачивания."
)


# ---------------------------------------------------------------------------
# Access helpers
# ---------------------------------------------------------------------------

def _is_admin(user_id: int, settings: Settings) -> bool:
    return user_id in settings.admin_user_ids


def _check_access(user_id: int, settings: Settings, redis: Redis) -> tuple[bool, str]:
    """Return (is_allowed, denial_message).

    Priority order:
    1. Admins → always allowed.
    2. Global kill switch (bot:disabled) → deny all non-admins.
    3. Static whitelist (ALLOWED_USERS in .env) → check membership.
    4. Dynamic trusted list (Redis SET trusted_users) → check membership.
    5. Neither list populated → public bot, everyone allowed.
    """
    if _is_admin(user_id, settings):
        return True, ""

    if redis.exists(_KEY_BOT_DISABLED):
        return False, "🔴 Бот временно недоступен. Попробуй позже."

    if settings.allowed_user_ids:
        if user_id in settings.allowed_user_ids:
            return True, ""
        return False, "⛔ У тебя нет доступа к этому боту."

    if redis.scard(_KEY_TRUSTED_USERS) > 0:
        if redis.sismember(_KEY_TRUSTED_USERS, str(user_id)):
            return True, ""
        return False, "⛔ У тебя нет доступа к этому боту."

    return True, ""  # public bot


# Kept for backward compatibility with existing tests.
def _is_allowed(user_id: int, allowed: set[int]) -> bool:
    return not allowed or user_id in allowed


# ---------------------------------------------------------------------------
# Admin panel helpers
# ---------------------------------------------------------------------------

def _admin_panel_text(settings: Settings, redis: Redis) -> str:
    is_disabled = bool(redis.exists(_KEY_BOT_DISABLED))
    trusted_count = redis.scard(_KEY_TRUSTED_USERS)

    status_line = "🔴 Выключен (только администраторы)" if is_disabled else "🟢 Включён"

    if settings.allowed_user_ids:
        mode = f"📋 Статичный список .env ({len(settings.allowed_user_ids)} польз.)"
    elif trusted_count > 0:
        mode = f"👥 Доверенные пользователи ({trusted_count} польз.)"
    else:
        mode = "🌐 Публичный (без ограничений)"

    return (
        "⚙️ <b>Панель администратора</b>\n\n"
        f"Статус бота: {status_line}\n"
        f"Режим доступа: {mode}\n\n"
        "<b>Управление пользователями:</b>\n"
        "  /adduser <code>&lt;id&gt;</code> — добавить доверенного\n"
        "  /removeuser <code>&lt;id&gt;</code> — удалить из доверенных\n"
        "  /listusers — список доверенных пользователей\n\n"
        "<i>Кнопка ниже включает/выключает бот для всех пользователей.</i>"
    )


# ---------------------------------------------------------------------------
# Standard handlers
# ---------------------------------------------------------------------------

@router.message(Command("start", "help"))
async def start(message: Message) -> None:
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
    allowed, ban_ttl = limiter.hit_or_ban(
        user_id=callback.from_user.id,
        window_seconds=settings.rate_limit_window_seconds,
        max_messages=settings.rate_limit_max_messages,
        ban_seconds=settings.ban_seconds,
    )
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
        repo = Repository(session)
        user_active = repo.count_user_active_requests(message.from_user.id)
        global_active = repo.count_global_active_requests()
        today = repo.count_user_today_requests(message.from_user.id)

    await message.answer(
        f"Твои активные задачи: {user_active}/{settings.user_queue_limit}\n"
        f"Глобальная очередь: {global_active}/{settings.global_queue_limit}\n"
        f"Твои запросы за 24 часа: {today}/{settings.user_daily_limit}"
    )


# ---------------------------------------------------------------------------
# Admin handlers
# ---------------------------------------------------------------------------

@router.message(Command("admin"))
async def admin_panel(message: Message) -> None:
    settings = get_settings()
    if not _is_admin(message.from_user.id, settings):
        return

    redis = get_redis()
    is_disabled = bool(redis.exists(_KEY_BOT_DISABLED))
    await message.answer(
        _admin_panel_text(settings, redis),
        reply_markup=admin_keyboard(is_disabled),
    )


@router.callback_query(F.data == "admin:toggle_access")
async def toggle_bot_access(callback: CallbackQuery) -> None:
    settings = get_settings()
    if not _is_admin(callback.from_user.id, settings):
        await callback.answer("⛔ Нет доступа", show_alert=True)
        return

    redis = get_redis()
    if redis.exists(_KEY_BOT_DISABLED):
        redis.delete(_KEY_BOT_DISABLED)
        alert_text = "🟢 Бот включён для всех пользователей."
        is_disabled = False
    else:
        redis.set(_KEY_BOT_DISABLED, "1")
        alert_text = "🔴 Бот выключен. Доступен только администраторам."
        is_disabled = True

    await callback.answer(alert_text, show_alert=True)
    try:
        await callback.message.edit_text(
            _admin_panel_text(settings, redis),
            reply_markup=admin_keyboard(is_disabled),
        )
    except TelegramBadRequest:
        pass  # message unchanged — ignore


@router.message(Command("adduser"))
async def add_trusted_user(message: Message) -> None:
    settings = get_settings()
    if not _is_admin(message.from_user.id, settings):
        return

    parts = (message.text or "").split(maxsplit=1)
    if len(parts) < 2 or not parts[1].strip().lstrip("-").isdigit():
        await message.answer(
            "Использование: /adduser <code>&lt;telegram_id&gt;</code>\n"
            "Пример: <code>/adduser 123456789</code>"
        )
        return

    uid = int(parts[1].strip())
    get_redis().sadd(_KEY_TRUSTED_USERS, str(uid))
    await message.answer(f"✅ Пользователь <code>{uid}</code> добавлен в доверенные.")


@router.message(Command("removeuser"))
async def remove_trusted_user(message: Message) -> None:
    settings = get_settings()
    if not _is_admin(message.from_user.id, settings):
        return

    parts = (message.text or "").split(maxsplit=1)
    if len(parts) < 2 or not parts[1].strip().lstrip("-").isdigit():
        await message.answer(
            "Использование: /removeuser <code>&lt;telegram_id&gt;</code>\n"
            "Пример: <code>/removeuser 123456789</code>"
        )
        return

    uid = int(parts[1].strip())
    removed = get_redis().srem(_KEY_TRUSTED_USERS, str(uid))
    if removed:
        await message.answer(f"✅ Пользователь <code>{uid}</code> удалён из доверенных.")
    else:
        await message.answer(f"⚠️ Пользователь <code>{uid}</code> не найден в списке доверенных.")


@router.message(Command("listusers"))
async def list_trusted_users(message: Message) -> None:
    settings = get_settings()
    if not _is_admin(message.from_user.id, settings):
        return

    members = get_redis().smembers(_KEY_TRUSTED_USERS)
    if not members:
        await message.answer("Список доверенных пользователей пуст.\nДобавьте: /adduser &lt;id&gt;")
        return

    lines = "\n".join(f"• <code>{uid}</code>" for uid in sorted(members, key=int))
    await message.answer(f"👥 <b>Доверенные пользователи</b> ({len(members)}):\n\n{lines}")


# ---------------------------------------------------------------------------
# URL message handlers
# ---------------------------------------------------------------------------

@router.message(F.text)
async def handle_link(message: Message) -> None:
    await _process_url_message(message, message.text or "", reply_on_no_url=True)


@router.message(F.caption)
async def handle_caption_link(message: Message) -> None:
    """Handle media messages that have a video URL in the caption."""
    await _process_url_message(message, message.caption or "", reply_on_no_url=False)


async def _process_url_message(message: Message, text: str, reply_on_no_url: bool) -> None:
    settings = get_settings()
    redis = get_redis()
    limiter = RateLimiter(redis)

    user_id = message.from_user.id

    # Access check first — before consuming rate-limit quota
    allowed, denial_msg = _check_access(user_id, settings, redis)
    if not allowed:
        await message.answer(denial_msg)
        return

    allowed, ban_ttl = limiter.hit_or_ban(
        user_id=user_id,
        window_seconds=settings.rate_limit_window_seconds,
        max_messages=settings.rate_limit_max_messages,
        ban_seconds=settings.ban_seconds,
    )
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
        repo = Repository(session)

        repo.upsert_user(
            user_id=user_id,
            username=message.from_user.username,
            first_name=message.from_user.first_name,
        )

        if repo.count_user_today_requests(user_id) >= settings.user_daily_limit:
            await message.answer("⚠️ Дневной лимит запросов исчерпан.")
            return

        if repo.count_user_active_requests(user_id) >= settings.user_queue_limit:
            await message.answer("⚠️ У тебя слишком много активных задач в очереди.")
            return

        if repo.count_global_active_requests() >= settings.global_queue_limit:
            await message.answer("⚠️ Сервер сейчас перегружен. Попробуй позже.")
            return

        ready_video = repo.get_ready_video(h, quality_value)
        if ready_video and ready_video.telegram_file_id and ready_video.telegram_file_type:
            try:
                await send_cached_file(message, ready_video.telegram_file_id, ready_video.telegram_file_type.value)
                return
            except TelegramBadRequest:
                logger.warning(
                    "Cached file_id for video %s is invalid, clearing and re-queuing",
                    ready_video.id,
                )
                repo.invalidate_video_cache(ready_video.id)
                await message.answer("⚠️ Кэш устарел, скачиваю заново...")

        video = repo.get_or_create_video(
            original_url=raw_url,
            normalized_url=normalized,
            url_hash=h,
            quality=quality_value,
        )

        status_msg = await message.answer(
            "🧾 Задача добавлена в очередь.\n"
            f"Качество: {quality_value}"
        )

        req = repo.create_request(
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
        repo.set_request_task_id(req.id, task.id)


async def send_cached_file(message: Message, file_id: str, file_type: str) -> None:
    if file_type == "video":
        await message.answer_video(file_id, caption="⚡ Готово из Telegram-кэша")
    elif file_type == "audio":
        await message.answer_audio(file_id, caption="⚡ Готово из Telegram-кэша")
    else:
        await message.answer_document(file_id, caption="⚡ Готово из Telegram-кэша")
