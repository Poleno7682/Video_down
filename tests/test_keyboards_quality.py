from aiogram.types import InlineKeyboardMarkup

from app.keyboards.quality import quality_keyboard


def test_quality_keyboard_type():
    kb = quality_keyboard()
    assert isinstance(kb, InlineKeyboardMarkup)


def test_quality_keyboard_has_two_rows():
    kb = quality_keyboard()
    assert len(kb.inline_keyboard) == 2


def test_quality_keyboard_buttons():
    kb = quality_keyboard()
    callbacks = [btn.callback_data for row in kb.inline_keyboard for btn in row]
    assert "quality:360p" in callbacks
    assert "quality:480p" in callbacks
    assert "quality:720p" in callbacks
    assert "quality:1080p" in callbacks
    assert "quality:best" in callbacks
    assert "quality:audio" in callbacks


def test_quality_keyboard_row_sizes():
    kb = quality_keyboard()
    assert len(kb.inline_keyboard[0]) == 3
    assert len(kb.inline_keyboard[1]) == 3
