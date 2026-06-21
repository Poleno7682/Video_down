from __future__ import annotations

from aiogram import F, Router
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import BaseFilter, Command
from aiogram.types import CallbackQuery, Message

from app.bot.access import _is_admin, _KEY_BOT_DISABLED, _KEY_TRUSTED_USERS
from app.bot.filters import AdminFilter
from app.core.config import get_settings
from app.keyboards.admin import admin_keyboard, limits_keyboard
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

router = Router()


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
        "<b>Рассылка:</b> /broadcast или кнопка ниже.\n\n"
        "<i>Кнопки ниже: вкл/выкл бот для всех и запуск рассылки.</i>"
    )


# ---------------------------------------------------------------------------
# Admin panel
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


@router.callback_query(F.data == "admin:limits", AdminFilter(alert_on_deny=True))
async def show_limits(callback: CallbackQuery) -> None:
    await callback.answer()
    settings = get_settings()
    redis = get_redis()
    effective = {f: get_limit(f, settings, redis) for f in EDITABLE_LIMITS}
    await callback.message.edit_text(
        "⚙️ <b>Лимиты</b>\n\nНажмите на лимит, чтобы изменить его значение.",
        reply_markup=limits_keyboard(effective),
    )


@router.callback_query(F.data.startswith("limits:edit:"), AdminFilter(alert_on_deny=True))
async def limits_start_edit(callback: CallbackQuery) -> None:
    await callback.answer()

    field = callback.data.split(":", 2)[2]
    if field not in EDITABLE_LIMITS:
        await callback.message.answer("⚠️ Неизвестный лимит.")
        return

    spec = EDITABLE_LIMITS[field]
    settings = get_settings()
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


@router.callback_query(F.data == "limits:reset_all", AdminFilter(alert_on_deny=True))
async def limits_reset_all(callback: CallbackQuery) -> None:
    settings = get_settings()
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


@router.callback_query(F.data == "limits:back", AdminFilter(alert_on_deny=True))
async def limits_back(callback: CallbackQuery) -> None:
    await callback.answer()
    settings = get_settings()
    redis = get_redis()
    is_disabled = bool(redis.exists(_KEY_BOT_DISABLED))
    try:
        await callback.message.edit_text(
            _admin_panel_text(settings, redis),
            reply_markup=admin_keyboard(is_disabled),
        )
    except Exception:
        pass


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
    try:
        await callback.message.edit_text(
            _admin_panel_text(settings, redis),
            reply_markup=admin_keyboard(is_disabled),
        )
    except TelegramBadRequest:
        pass


# ---------------------------------------------------------------------------
# User management
# ---------------------------------------------------------------------------

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
# Admin limit input interceptor
# Registered LAST in this router so it catches F.text only when no command matched.
# Must be included BEFORE url_handler router so admin text doesn't trigger download.
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
        zero_hint = " или 0 (отключить)" if spec.zero_disables and spec.min_val == 0 else ""
        await message.answer(
            f"⚠️ Значение должно быть от {spec.min_val} до {spec.max_val}{zero_hint}."
        )
        return

    set_limit(field, value, redis)
    clear_awaiting(admin_id, redis)
    await message.answer(
        f"✅ <b>{spec.emoji} {spec.label}</b> → <b>{format_value(field, value)}</b>"
    )
