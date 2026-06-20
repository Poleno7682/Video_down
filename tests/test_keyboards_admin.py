from aiogram.types import InlineKeyboardMarkup

from app.keyboards.admin import admin_keyboard


def test_admin_keyboard_returns_markup():
    assert isinstance(admin_keyboard(bot_disabled=False), InlineKeyboardMarkup)
    assert isinstance(admin_keyboard(bot_disabled=True), InlineKeyboardMarkup)


def test_admin_keyboard_enabled_shows_disable_button():
    kb = admin_keyboard(bot_disabled=False)
    buttons = [btn for row in kb.inline_keyboard for btn in row]
    assert len(buttons) == 1
    assert "🔴" in buttons[0].text
    assert buttons[0].callback_data == "admin:toggle_access"


def test_admin_keyboard_disabled_shows_enable_button():
    kb = admin_keyboard(bot_disabled=True)
    buttons = [btn for row in kb.inline_keyboard for btn in row]
    assert len(buttons) == 1
    assert "🟢" in buttons[0].text
    assert buttons[0].callback_data == "admin:toggle_access"
