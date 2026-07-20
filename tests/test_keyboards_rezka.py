from __future__ import annotations

from aiogram.types import InlineKeyboardMarkup

from app.keyboards.rezka import episode_keyboard, season_keyboard, translator_keyboard


def test_translator_keyboard_returns_markup_with_buttons():
    kb = translator_keyboard({56: "Дубляж", 99: "Оригинал"})
    assert isinstance(kb, InlineKeyboardMarkup)
    buttons = [btn for row in kb.inline_keyboard for btn in row]
    callback_data = {b.callback_data for b in buttons}
    assert callback_data == {"rezka:tr:56", "rezka:tr:99"}


def test_translator_keyboard_caps_button_count():
    translators = {i: f"Voice {i}" for i in range(100)}
    kb = translator_keyboard(translators)
    buttons = [btn for row in kb.inline_keyboard for btn in row]
    assert len(buttons) == 60


def test_season_keyboard_builds_labeled_buttons():
    kb = season_keyboard([1, 2, 3])
    buttons = [btn for row in kb.inline_keyboard for btn in row]
    assert {b.text for b in buttons} == {"Сезон 1", "Сезон 2", "Сезон 3"}
    assert {b.callback_data for b in buttons} == {"rezka:season:1", "rezka:season:2", "rezka:season:3"}


def test_episode_keyboard_builds_labeled_buttons():
    kb = episode_keyboard([1, 2])
    buttons = [btn for row in kb.inline_keyboard for btn in row]
    assert {b.text for b in buttons} == {"Серия 1", "Серия 2"}
    assert {b.callback_data for b in buttons} == {"rezka:ep:1", "rezka:ep:2"}
