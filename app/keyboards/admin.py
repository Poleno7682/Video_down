from __future__ import annotations

from aiogram.types import InlineKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder


def admin_keyboard(bot_disabled: bool) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    if bot_disabled:
        builder.button(text="🟢 Включить бот для всех", callback_data="admin:toggle_access")
    else:
        builder.button(text="🔴 Выключить бот для всех", callback_data="admin:toggle_access")
    builder.adjust(1)
    return builder.as_markup()
