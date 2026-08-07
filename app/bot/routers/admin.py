from __future__ import annotations

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.types import CallbackQuery, Message

from app.bot.access import _KEY_BOT_DISABLED, _KEY_TRUSTED_USERS
from app.bot.filters import AdminFilter
from app.bot.utils import safe_edit_text
from app.core.config import get_settings
from app.keyboards.admin import admin_keyboard
from app.services.redis_client import get_redis

router = Router()


def _parse_telegram_id(raw: str) -> int | None:
    """Parse a single Telegram user ID argument (e.g. from /adduser 123456789)."""
    raw = raw.strip()
    if not raw.lstrip("-").isdigit():
        return None
    return int(raw)


async def _reply_usage(message: Message, command: str, example_id: int) -> None:
    await message.answer(
        f"Использование: /{command} <code>&lt;telegram_id&gt;</code>\n"
        f"Пример: <code>/{command} {example_id}</code>"
    )


def _admin_panel_text(settings, redis) -> str:
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
        "<b>Прокси для yt-dlp (обход антибот-блокировок):</b>\n"
        "  /addproxy — добавить (спросит тип и данные, проверит перед сохранением)\n"
        "  /delproxy <code>&lt;id&gt;</code> — /listproxies — список\n\n"
        "<b>Рассылка:</b> /broadcast или кнопка ниже.\n\n"
        "<i>Кнопки ниже: вкл/выкл бот для всех и запуск рассылки.</i>"
    )


# ---------------------------------------------------------------------------
# Admin panel
# ---------------------------------------------------------------------------

@router.message(Command("admin"), AdminFilter())
async def admin_panel(message: Message) -> None:
    settings = get_settings()
    redis = get_redis()
    is_disabled = bool(redis.exists(_KEY_BOT_DISABLED))
    await message.answer(
        _admin_panel_text(settings, redis),
        reply_markup=admin_keyboard(is_disabled),
    )


@router.callback_query(F.data == "admin:toggle_access", AdminFilter(alert_on_deny=True))
async def toggle_bot_access(callback: CallbackQuery) -> None:
    settings = get_settings()
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
    await safe_edit_text(
        callback.message,
        _admin_panel_text(settings, redis),
        reply_markup=admin_keyboard(is_disabled),
    )


# ---------------------------------------------------------------------------
# User management
# ---------------------------------------------------------------------------

@router.message(Command("adduser"), AdminFilter())
async def add_trusted_user(message: Message) -> None:
    parts = (message.text or "").split(maxsplit=1)
    uid = _parse_telegram_id(parts[1]) if len(parts) >= 2 else None
    if uid is None:
        await _reply_usage(message, "adduser", 123456789)
        return

    get_redis().sadd(_KEY_TRUSTED_USERS, str(uid))
    await message.answer(f"✅ Пользователь <code>{uid}</code> добавлен в доверенные.")


@router.message(Command("removeuser"), AdminFilter())
async def remove_trusted_user(message: Message) -> None:
    parts = (message.text or "").split(maxsplit=1)
    uid = _parse_telegram_id(parts[1]) if len(parts) >= 2 else None
    if uid is None:
        await _reply_usage(message, "removeuser", 123456789)
        return

    removed = get_redis().srem(_KEY_TRUSTED_USERS, str(uid))
    if removed:
        await message.answer(f"✅ Пользователь <code>{uid}</code> удалён из доверенных.")
    else:
        await message.answer(f"⚠️ Пользователь <code>{uid}</code> не найден в списке доверенных.")


@router.message(Command("listusers"), AdminFilter())
async def list_trusted_users(message: Message) -> None:
    members = get_redis().smembers(_KEY_TRUSTED_USERS)
    if not members:
        await message.answer("Список доверенных пользователей пуст.\nДобавьте: /adduser &lt;id&gt;")
        return

    lines = "\n".join(f"• <code>{uid}</code>" for uid in sorted(members, key=int))
    await message.answer(f"👥 <b>Доверенные пользователи</b> ({len(members)}):\n\n{lines}")


# ---------------------------------------------------------------------------
# Sub-routers registered in the same relative order they used to appear in
# this file as inline handlers — the proxy-input and limit-input F.text
# interceptors both must stay ahead of url_handler's generic F.text handler
# (see each sub-router's own module docstring/comments), and proxy's
# interceptor specifically ahead of the limit-input one, as before.
# ---------------------------------------------------------------------------
from app.bot.routers.admin_proxy import router as _admin_proxy_router  # noqa: E402
from app.bot.routers.admin_limits import router as _admin_limits_router  # noqa: E402

router.include_router(_admin_proxy_router)
router.include_router(_admin_limits_router)
