from __future__ import annotations

from aiogram import F, Router
from aiogram.filters import BaseFilter
from aiogram.types import CallbackQuery, Message

from app.bot.access import _is_admin, _KEY_BOT_DISABLED
from app.bot.filters import AdminFilter
from app.bot.routers.admin import _admin_panel_text
from app.bot.utils import safe_edit_text
from app.core.config import get_settings
from app.keyboards.admin import admin_keyboard, limits_keyboard
from app.services.redis_client import get_redis
from app.services.runtime_config import (
    EDITABLE_LIMITS,
    clear_awaiting,
    format_value,
    get_awaiting,
    get_effective_limits,
    get_limit,
    reset_all_limits,
    reset_limit,
    set_awaiting,
    set_limit,
)

router = Router()


@router.callback_query(F.data == "admin:limits", AdminFilter(alert_on_deny=True))
async def show_limits(callback: CallbackQuery) -> None:
    await callback.answer()
    settings = get_settings()
    redis = get_redis()
    effective = get_effective_limits(settings, redis)
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
    effective = get_effective_limits(settings, redis)
    await safe_edit_text(
        callback.message,
        "⚙️ <b>Лимиты</b>\n\nНажмите на лимит, чтобы изменить его значение.",
        reply_markup=limits_keyboard(effective),
    )


@router.callback_query(F.data == "limits:back", AdminFilter(alert_on_deny=True))
async def limits_back(callback: CallbackQuery) -> None:
    await callback.answer()
    settings = get_settings()
    redis = get_redis()
    is_disabled = bool(redis.exists(_KEY_BOT_DISABLED))
    await safe_edit_text(
        callback.message,
        _admin_panel_text(settings, redis),
        reply_markup=admin_keyboard(is_disabled),
    )


# ---------------------------------------------------------------------------
# Admin limit input interceptor
# Registered LAST in the composed admin router (see admin.py) so it catches
# F.text only when no command matched. Must be included BEFORE url_handler
# router so admin text doesn't trigger a download.
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
