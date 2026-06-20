from __future__ import annotations

from app.utils.broadcast import parse_buttons


def test_no_marker_returns_original():
    text, markup = parse_buttons("Просто текст без кнопок")
    assert text == "Просто текст без кнопок"
    assert markup is None


def test_empty_text():
    text, markup = parse_buttons("")
    assert text == ""
    assert markup is None


def test_none_text():
    text, markup = parse_buttons(None)
    assert text == ""
    assert markup is None


def test_parses_single_button():
    text, markup = parse_buttons("Привет\n---\nОткрыть | https://example.com")
    assert text == "Привет"
    assert markup is not None
    buttons = [b for row in markup.inline_keyboard for b in row]
    assert len(buttons) == 1
    assert buttons[0].text == "Открыть"
    assert buttons[0].url == "https://example.com"


def test_parses_multiple_buttons_one_per_row():
    body = "Тело\n---\nA | https://a.com\nB | https://b.com"
    text, markup = parse_buttons(body)
    assert text == "Тело"
    rows = markup.inline_keyboard
    assert len(rows) == 2
    assert rows[0][0].url == "https://a.com"
    assert rows[1][0].url == "https://b.com"


def test_invalid_url_skipped_returns_original_when_no_valid():
    raw = "Тело\n---\nПлохая | ftp://nope"
    text, markup = parse_buttons(raw)
    assert text == raw
    assert markup is None


def test_mixed_valid_and_invalid_buttons():
    raw = "Тело\n---\nOK | https://ok.com\nBad | notaurl"
    text, markup = parse_buttons(raw)
    assert text == "Тело"
    buttons = [b for row in markup.inline_keyboard for b in row]
    assert len(buttons) == 1
    assert buttons[0].url == "https://ok.com"
