from __future__ import annotations

import asyncio
import logging

from aiogram import Bot, F, Router
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import BaseFilter, Command
from aiogram.types import CallbackQuery, Message
from redis import Redis

from app.core.config import Settings, get_settings
from app.db.models import DownloadStatus
from app.db.repository import Repository
from app.db.session import get_session
from app.keyboards.admin import admin_keyboard, broadcast_cancel_keyboard, limits_keyboard
from app.keyboards.quality import quality_keyboard
from app.services.rate_limiter import RateLimiter
from app.services.redis_client import get_redis
from app.services.runtime_config import (
    EDITABLE_LIMITS,
    clear_awaiting,
    format_value,
    get_awaiting,
    get_limit,
    reset_all_limits,
    reset_limit,
    set_awaiting,
    set_limit,
)
from app.services.google_oauth import (
    DeviceFlowExpired,
    DeviceFlowPending,
    generate_youtube_cookies,
    poll_token,
    revoke_token,
    start_device_flow,
)
from app.utils.broadcast import parse_buttons
from app.utils.caption import get_caption
from app.utils.platforms import PLATFORMS, platform_from_filename
from app.utils.quality import normalize_quality
from app.utils.url_tools import extract_url, is_valid_url, normalize_url, url_hash
from app.worker.tasks import process_download_request


router = Router()
logger = logging.getLogger(__name__)

# Redis keys
_KEY_BOT_DISABLED = "bot:disabled"
_KEY_TRUSTED_USERS = "trusted_users"


def _broadcast_key(admin_id: int) -> str:
    return f"broadcast_mode:{admin_id}"


# Max size for an uploaded cookies .txt file (Netscape cookie files are tiny).
_MAX_COOKIE_FILE_BYTES = 2 * 1024 * 1024

HELP_TEXT = (
    "Пришли ссылку на видео, и я скачаю его через очередь.\n\n"
    "Команды:\n"
    "/quality — выбрать качество\n"
    "/status — краткая информация\n"
    "/link_google — привязать Google-аккаунт (YouTube)\n"
    "/unlink_google — отвязать Google-аккаунт"
)

_GOOGLE_LINKING_KEY = "google_linking:{}"


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
        "<b>Рассылка:</b> /broadcast или кнопка ниже.\n\n"
        "<i>Кнопки ниже: вкл/выкл бот для всех и запуск рассылки.</i>"
    )


# ---------------------------------------------------------------------------
# Standard handlers
# ---------------------------------------------------------------------------

@router.message(Command("start", "help"))
async def start(message: Message) -> None:
    # Persist every user that ever launched the bot (used for admin broadcasts).
    with get_session() as session:
        Repository(session).upsert_user(
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


@router.callback_query(F.data == "admin:limits")
async def show_limits(callback: CallbackQuery) -> None:
    settings = get_settings()
    if not _is_admin(callback.from_user.id, settings):
        await callback.answer("⛔ Нет доступа", show_alert=True)
        return
    await callback.answer()
    redis = get_redis()
    effective = {f: get_limit(f, settings, redis) for f in EDITABLE_LIMITS}
    await callback.message.edit_text(
        "⚙️ <b>Лимиты</b>\n\nНажмите на лимит, чтобы изменить его значение.",
        reply_markup=limits_keyboard(effective),
    )


@router.callback_query(F.data.startswith("limits:edit:"))
async def limits_start_edit(callback: CallbackQuery) -> None:
    settings = get_settings()
    if not _is_admin(callback.from_user.id, settings):
        await callback.answer("⛔ Нет доступа", show_alert=True)
        return
    await callback.answer()

    field = callback.data.split(":", 2)[2]
    if field not in EDITABLE_LIMITS:
        await callback.message.answer("⚠️ Неизвестный лимит.")
        return

    spec = EDITABLE_LIMITS[field]
    redis = get_redis()
    current = get_limit(field, settings, redis)
    display = format_value(field, current)

    zero_hint = "\n0 = отключить лимит (без ограничений)" if spec.zero_disables else ""
    await callback.message.answer(
        f"✏️ <b>{spec.emoji} {spec.label}</b>\n\n"
        f"Текущее значение: <b>{display}</b>\n\n"
        f"Введите новое значение — целое число от {spec.min_val} до {spec.max_val}.{zero_hint}\n\n"
        "Или введите <code>сброс</code>, чтобы вернуть значение из .env.\n"
        "Или введите <code>/cancel</code> для отмены."
    )
    set_awaiting(callback.from_user.id, field, redis)


@router.callback_query(F.data == "limits:reset_all")
async def limits_reset_all(callback: CallbackQuery) -> None:
    settings = get_settings()
    if not _is_admin(callback.from_user.id, settings):
        await callback.answer("⛔ Нет доступа", show_alert=True)
        return
    redis = get_redis()
    reset_all_limits(redis)
    await callback.answer("✅ Все лимиты сброшены к значениям .env", show_alert=True)
    effective = {f: get_limit(f, settings, redis) for f in EDITABLE_LIMITS}
    try:
        await callback.message.edit_text(
            "⚙️ <b>Лимиты</b>\n\nНажмите на лимит, чтобы изменить его значение.",
            reply_markup=limits_keyboard(effective),
        )
    except Exception:
        pass


@router.callback_query(F.data == "limits:back")
async def limits_back(callback: CallbackQuery) -> None:
    settings = get_settings()
    if not _is_admin(callback.from_user.id, settings):
        await callback.answer("⛔ Нет доступа", show_alert=True)
        return
    await callback.answer()
    redis = get_redis()
    is_disabled = bool(redis.exists(_KEY_BOT_DISABLED))
    try:
        await callback.message.edit_text(
            _admin_panel_text(settings, redis),
            reply_markup=admin_keyboard(is_disabled),
        )
    except Exception:
        pass


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
# Per-user cookies
# ---------------------------------------------------------------------------

_COOKIES_HELP = (
    "🍪 <b>Личные cookies</b>\n\n"
    "Некоторые сайты (особенно YouTube) требуют cookies авторизованного аккаунта.\n\n"
    "Экспортируйте cookies в формате <b>Netscape</b> (cookies.txt) и пришлите файл "
    "боту. <b>Имя файла задаёт платформу:</b>\n"
    "• <code>youtube.txt</code>\n"
    "• <code>instagram.txt</code>\n"
    "• <code>tiktok.txt</code>\n"
    "• <code>facebook.txt</code>\n\n"
    "Удалить: <code>/delcookies youtube</code>"
)


def _looks_like_netscape(text: str) -> bool:
    head = text.lstrip()
    if head.startswith("# Netscape HTTP Cookie File") or head.startswith("# HTTP Cookie File"):
        return True
    for line in text.splitlines():
        if line and not line.startswith("#") and line.count("\t") >= 5:
            return True
    return False


@router.message(Command("cookies"))
async def cookies_info(message: Message) -> None:
    settings = get_settings()
    redis = get_redis()
    allowed, denial_msg = _check_access(message.from_user.id, settings, redis)
    if not allowed:
        await message.answer(denial_msg)
        return

    with get_session() as session:
        platforms = Repository(session).list_user_platforms(message.from_user.id)

    if platforms:
        status = "Загружены: " + ", ".join(sorted(platforms))
    else:
        status = "Пока не загружены."
    await message.answer(f"{_COOKIES_HELP}\n\n<b>Статус:</b> {status}")


@router.message(Command("delcookies"))
async def delete_cookies(message: Message) -> None:
    settings = get_settings()
    redis = get_redis()
    allowed, denial_msg = _check_access(message.from_user.id, settings, redis)
    if not allowed:
        await message.answer(denial_msg)
        return

    parts = (message.text or "").split(maxsplit=1)
    platform = parts[1].strip().lower() if len(parts) > 1 else ""
    if platform not in PLATFORMS:
        await message.answer(
            "Использование: <code>/delcookies &lt;platform&gt;</code>\n"
            f"Платформы: {', '.join(PLATFORMS)}"
        )
        return

    with get_session() as session:
        removed = Repository(session).delete_user_cookies(message.from_user.id, platform)
    if removed:
        await message.answer(f"✅ Cookies для <b>{platform}</b> удалены.")
    else:
        await message.answer(f"⚠️ Cookies для <b>{platform}</b> не найдены.")


# ---------------------------------------------------------------------------
# Google OAuth2 device flow — /link_google and /unlink_google
# ---------------------------------------------------------------------------

@router.message(Command("link_google"))
async def link_google(message: Message) -> None:
    settings = get_settings()
    redis = get_redis()
    allowed, denial_msg = _check_access(message.from_user.id, settings, redis)
    if not allowed:
        await message.answer(denial_msg)
        return

    user_id = message.from_user.id
    link_key = _GOOGLE_LINKING_KEY.format(user_id)
    if redis.exists(link_key):
        await message.answer(
            "⏳ Авторизация уже в процессе.\n"
            "Подтверди код в браузере или подожди, пока он не истечёт."
        )
        return

    try:
        flow_info = await asyncio.to_thread(start_device_flow)
    except Exception as e:
        await message.answer(f"❌ Не удалось начать авторизацию Google: {e}")
        return

    user_code = flow_info.get("user_code", "N/A")
    verification_url = flow_info.get("verification_url", "https://google.com/device")
    expires_in = int(flow_info.get("expires_in", 1800))
    device_code = flow_info["device_code"]
    interval = max(5, int(flow_info.get("interval", 5)))

    redis.setex(link_key, expires_in, "1")

    await message.answer(
        "🔗 <b>Привяжи свой Google-аккаунт</b>\n\n"
        f"1️⃣ Открой: <a href=\"{verification_url}\">{verification_url}</a>\n"
        f"2️⃣ Введи код: <code>{user_code}</code>\n\n"
        f"⏱ Код действует {expires_in // 60} мин.\n\n"
        "После подтверждения бот автоматически получит YouTube cookies.",
        disable_web_page_preview=True,
    )

    asyncio.create_task(
        _poll_google_link(message, device_code, interval, expires_in, link_key)
    )


async def _poll_google_link(
    message: Message,
    device_code: str,
    interval: int,
    expires_in: int,
    link_key: str,
) -> None:
    """Background polling loop for the Google OAuth device flow."""
    import time as _time

    redis = get_redis()
    user_id = message.from_user.id
    deadline = _time.monotonic() + expires_in

    try:
        while _time.monotonic() < deadline:
            await asyncio.sleep(interval)
            try:
                token_info = await asyncio.to_thread(poll_token, device_code)
            except DeviceFlowPending:
                continue
            except DeviceFlowExpired:
                await message.answer(
                    "❌ Код авторизации истёк. Запусти /link_google заново."
                )
                return
            except Exception:
                continue

            access_tok = token_info.get("access_token")
            refresh_tok = token_info.get("refresh_token")
            if not access_tok:
                await message.answer("❌ Неожиданный ответ от Google. Попробуй /link_google снова.")
                return

            try:
                cookie_text = await asyncio.to_thread(generate_youtube_cookies, access_tok)
            except Exception as e:
                await message.answer(
                    f"❌ Авторизация прошла, но не удалось получить YouTube cookies: {e}\n\n"
                    "Попробуй /link_google ещё раз."
                )
                return

            with get_session() as session:
                repo = Repository(session)
                repo.set_user_cookies(user_id, "youtube", cookie_text)
                if refresh_tok:
                    repo.set_google_token(user_id, refresh_tok)

            await message.answer(
                "✅ <b>Google-аккаунт привязан!</b>\n\n"
                "YouTube cookies сохранены и будут использоваться при скачивании.\n"
                "Для отвязки: /unlink_google"
            )
            return

        await message.answer("❌ Время ожидания истекло. Запусти /link_google заново.")
    finally:
        redis.delete(link_key)


@router.message(Command("unlink_google"))
async def unlink_google(message: Message) -> None:
    settings = get_settings()
    redis = get_redis()
    allowed, denial_msg = _check_access(message.from_user.id, settings, redis)
    if not allowed:
        await message.answer(denial_msg)
        return

    user_id = message.from_user.id
    redis.delete(_GOOGLE_LINKING_KEY.format(user_id))

    with get_session() as session:
        repo = Repository(session)
        token_rec = repo.get_google_token(user_id)
        if token_rec:
            await asyncio.to_thread(revoke_token, token_rec.refresh_token)
            repo.delete_google_token(user_id)
        removed_cookies = repo.delete_user_cookies(user_id, "youtube")

    if token_rec or removed_cookies:
        await message.answer("✅ Google-аккаунт отвязан. YouTube cookies удалены.")
    else:
        await message.answer("ℹ️ Привязки к Google-аккаунту не найдено.")


# ---------------------------------------------------------------------------
# Admin broadcast
# ---------------------------------------------------------------------------

class BroadcastModeFilter(BaseFilter):
    """Matches only when an admin currently has an active broadcast session."""

    async def __call__(self, message: Message) -> bool:
        settings = get_settings()
        if not _is_admin(message.from_user.id, settings):
            return False
        return bool(get_redis().exists(_broadcast_key(message.from_user.id)))


async def _enter_broadcast_mode(target: Message, admin_id: int) -> None:
    settings = get_settings()
    get_redis().setex(_broadcast_key(admin_id), settings.broadcast_timeout_seconds, "1")
    minutes = settings.broadcast_timeout_seconds // 60
    await target.answer(
        "📢 <b>Режим рассылки включён.</b>\n\n"
        "Отправьте сообщение (текст, фото, GIF, видео, музыку) — оно будет разослано "
        "всем пользователям бота.\n\n"
        "<b>Inline-кнопки:</b> добавьте в конце сообщения строку <code>---</code>, "
        "а далее по одной кнопке в строке в формате <code>Текст | https://ссылка</code>.\n\n"
        f"⏱ Режим автоматически выключится через {minutes} мин без активности "
        "(таймер сбрасывается после каждой рассылки).",
        reply_markup=broadcast_cancel_keyboard(),
    )


@router.message(Command("broadcast"))
async def broadcast_command(message: Message) -> None:
    if not _is_admin(message.from_user.id, get_settings()):
        return
    await _enter_broadcast_mode(message, message.from_user.id)


@router.callback_query(F.data == "admin:broadcast")
async def broadcast_start_callback(callback: CallbackQuery) -> None:
    if not _is_admin(callback.from_user.id, get_settings()):
        await callback.answer("⛔ Нет доступа", show_alert=True)
        return
    await callback.answer()
    await _enter_broadcast_mode(callback.message, callback.from_user.id)


@router.callback_query(F.data == "broadcast:cancel")
async def broadcast_cancel_callback(callback: CallbackQuery) -> None:
    if not _is_admin(callback.from_user.id, get_settings()):
        await callback.answer("⛔ Нет доступа", show_alert=True)
        return
    get_redis().delete(_broadcast_key(callback.from_user.id))
    await callback.answer("Рассылка отменена")
    try:
        await callback.message.edit_text("❌ Режим рассылки выключен.")
    except TelegramBadRequest:
        pass


async def _broadcast_to_all(bot: Bot, source: Message) -> tuple[int, int, int]:
    """Send a copy of `source` to every user. Returns (ok, failed, total)."""
    with get_session() as session:
        user_ids = Repository(session).get_all_user_ids()

    from_chat_id = source.chat.id
    message_id = source.message_id

    if source.text is not None:
        clean_text, markup = parse_buttons(source.html_text)
        is_text = True
    else:
        clean_text, markup = parse_buttons(source.caption)
        is_text = False

    ok = 0
    failed = 0
    for uid in user_ids:
        try:
            if markup is None:
                await bot.copy_message(chat_id=uid, from_chat_id=from_chat_id, message_id=message_id)
            elif is_text:
                await bot.send_message(uid, clean_text, reply_markup=markup)
            else:
                await bot.copy_message(
                    chat_id=uid,
                    from_chat_id=from_chat_id,
                    message_id=message_id,
                    caption=clean_text or None,
                    reply_markup=markup,
                )
            ok += 1
        except Exception:
            failed += 1
        # Stay well under Telegram's ~30 msg/sec broadcast limit.
        await asyncio.sleep(0.05)

    return ok, failed, len(user_ids)


@router.message(BroadcastModeFilter())
async def broadcast_message(message: Message, bot: Bot) -> None:
    settings = get_settings()
    # Reset the 5-minute protection timer on every broadcast.
    get_redis().setex(_broadcast_key(message.from_user.id), settings.broadcast_timeout_seconds, "1")

    ok, failed, total = await _broadcast_to_all(bot, message)
    await message.answer(
        f"📢 Рассылка завершена.\n"
        f"✅ Доставлено: {ok}\n"
        f"⚠️ Не доставлено: {failed}\n"
        f"👥 Всего пользователей: {total}",
        reply_markup=broadcast_cancel_keyboard(),
    )


# ---------------------------------------------------------------------------
# Cookies upload (document) — must be registered AFTER the broadcast handler so
# an admin in broadcast mode can broadcast documents too.
# ---------------------------------------------------------------------------

@router.message(F.document)
async def upload_cookies(message: Message, bot: Bot) -> None:
    settings = get_settings()
    redis = get_redis()
    allowed, denial_msg = _check_access(message.from_user.id, settings, redis)
    if not allowed:
        await message.answer(denial_msg)
        return

    document = message.document
    platform = platform_from_filename(document.file_name)
    if not platform:
        await message.answer(
            "Чтобы загрузить cookies, пришлите <b>.txt</b> файл с именем платформы: "
            "<code>youtube.txt</code>, <code>instagram.txt</code>, "
            "<code>tiktok.txt</code> или <code>facebook.txt</code>.\n\n"
            "Подробнее: /cookies"
        )
        return

    if document.file_size and document.file_size > _MAX_COOKIE_FILE_BYTES:
        await message.answer("⚠️ Файл слишком большой для cookies.")
        return

    try:
        buffer = await bot.download(document)
        content = buffer.read().decode("utf-8", errors="replace")
    except Exception:
        logger.exception("Failed to download cookies file from user %s", message.from_user.id)
        await message.answer("❌ Не удалось прочитать файл. Попробуйте ещё раз.")
        return

    if not _looks_like_netscape(content):
        await message.answer(
            "❌ Это не похоже на cookies в формате Netscape.\n"
            "Экспортируйте файл расширением «Get cookies.txt LOCALLY» или "
            "<code>yt-dlp --cookies-from-browser ... --cookies file.txt</code>."
        )
        return

    with get_session() as session:
        Repository(session).set_user_cookies(message.from_user.id, platform, content)
    await message.answer(f"✅ Cookies для <b>{platform}</b> сохранены.")


# ---------------------------------------------------------------------------
# Admin limit input interceptor — must be registered BEFORE handle_link so
# that an admin who is editing a limit doesn't accidentally trigger a download.
# ---------------------------------------------------------------------------

class _AdminAwaitingFilter(BaseFilter):
    async def __call__(self, message: Message) -> bool:
        if not _is_admin(message.from_user.id, get_settings()):
            return False
        return bool(get_awaiting(message.from_user.id, get_redis()))


@router.message(_AdminAwaitingFilter(), F.text)
async def handle_admin_limit_input(message: Message) -> None:
    redis = get_redis()
    settings = get_settings()
    admin_id = message.from_user.id
    field = get_awaiting(admin_id, redis)
    if not field:
        return

    text = (message.text or "").strip().lower()

    if text in ("/cancel", "отмена"):
        clear_awaiting(admin_id, redis)
        await message.answer("❌ Редактирование отменено.")
        return

    if text in ("сброс", "reset", "default"):
        reset_limit(field, redis)
        clear_awaiting(admin_id, redis)
        spec = EDITABLE_LIMITS[field]
        default_val = int(getattr(settings, field))
        await message.answer(
            f"↩️ <b>{spec.emoji} {spec.label}</b> сброшен к значению из .env: "
            f"<b>{format_value(field, default_val)}</b>"
        )
        return

    if not text.lstrip("-").isdigit():
        await message.answer("⚠️ Введите целое число, <code>сброс</code> или /cancel.")
        return

    value = int(text)
    spec = EDITABLE_LIMITS[field]

    if value < spec.min_val or value > spec.max_val:
        zero_hint = f" или 0 (отключить)" if spec.zero_disables and spec.min_val == 0 else ""
        await message.answer(
            f"⚠️ Значение должно быть от {spec.min_val} до {spec.max_val}{zero_hint}."
        )
        return

    set_limit(field, value, redis)
    clear_awaiting(admin_id, redis)
    await message.answer(
        f"✅ <b>{spec.emoji} {spec.label}</b> → <b>{format_value(field, value)}</b>"
    )


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

    rl_max = get_limit("rate_limit_max_messages", settings, redis)
    if rl_max > 0:
        rl_window = get_limit("rate_limit_window_seconds", settings, redis)
        rl_ban = get_limit("ban_seconds", settings, redis)
        allowed, ban_ttl = limiter.hit_or_ban(
            user_id=user_id,
            window_seconds=rl_window,
            max_messages=rl_max,
            ban_seconds=rl_ban,
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

        daily_limit = get_limit("user_daily_limit", settings, redis)
        if daily_limit > 0 and repo.count_user_today_requests(user_id) >= daily_limit:
            await message.answer("⚠️ Дневной лимит запросов исчерпан.")
            return

        queue_limit = get_limit("user_queue_limit", settings, redis)
        if queue_limit > 0 and repo.count_user_active_requests(user_id) >= queue_limit:
            await message.answer("⚠️ У тебя слишком много активных задач в очереди.")
            return

        global_limit = get_limit("global_queue_limit", settings, redis)
        if global_limit > 0 and repo.count_global_active_requests() >= global_limit:
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
    caption = get_caption(get_settings())
    if file_type == "video":
        await message.answer_video(file_id, caption=caption)
    elif file_type == "audio":
        await message.answer_audio(file_id, caption=caption)
    else:
        await message.answer_document(file_id, caption=caption)
