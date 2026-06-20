from __future__ import annotations

from aiogram.types import InlineKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder


def admin_keyboard(bot_disabled: bool) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    if bot_disabled:
        builder.button(text="🟢 Включить бот для всех", callback_data="admin:toggle_access")
    else:
        builder.button(text="🔴 Выключить бот для всех", callback_data="admin:toggle_access")
    builder.button(text="📢 Рассылка", callback_data="admin:broadcast")
    builder.adjust(1)
    return builder.as_markup()


def broadcast_cancel_keyboard() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text="❌ Отменить рассылку", callback_data="broadcast:cancel")
    builder.adjust(1)
    return builder.as_markup()
