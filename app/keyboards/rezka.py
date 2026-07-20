from __future__ import annotations

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder

# Sanity cap on how many buttons a single keyboard shows — a handful of
# anime/long-running shows have 100+ episodes in one season, which would
# blow well past what's usable as inline buttons (and past Telegram's own
# per-message limits). Simple truncation rather than pagination: rare
# enough in practice not to be worth the extra UI complexity yet.
_MAX_BUTTONS = 60


def translator_keyboard(translators: dict[int, str]) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    for translator_id, name in list(translators.items())[:_MAX_BUTTONS]:
        builder.button(text=name, callback_data=f"rezka:tr:{translator_id}")
    builder.adjust(1)
    return builder.as_markup()


def season_keyboard(seasons: list[int]) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    for season in seasons[:_MAX_BUTTONS]:
        builder.button(text=f"Сезон {season}", callback_data=f"rezka:season:{season}")
    builder.adjust(4)
    # Added via .row() (not .button()+.adjust()) so it lands on its own
    # trailing row: .adjust() rebuilds the whole markup from just the
    # queued .button() calls, so anything meant to sit outside that grid
    # has to be appended afterwards.
    builder.row(InlineKeyboardButton(text="⬅️ Назад", callback_data="rezka:back:translators"))
    return builder.as_markup()


def episode_keyboard(episodes: list[int]) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text="📥 Скачать весь сезон", callback_data="rezka:season_all")
    for episode in episodes[:_MAX_BUTTONS]:
        builder.button(text=f"Серия {episode}", callback_data=f"rezka:ep:{episode}")
    # First row size 1 puts "Скачать весь сезон" alone on its own row, then
    # 5-per-row for every episode button after it (see .adjust()'s repeat-
    # last-size behavior).
    builder.adjust(1, 5)
    builder.row(InlineKeyboardButton(text="⬅️ Назад", callback_data="rezka:back:season"))
    return builder.as_markup()
