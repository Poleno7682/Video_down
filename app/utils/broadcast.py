from __future__ import annotations

from aiogram.types import InlineKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder

# Marker line that separates the message body from the button definitions.
# Everything after a line equal to this marker is parsed as URL buttons:
#   Текст сообщения...
#   ---
#   Открыть сайт | https://example.com
#   Канал | https://t.me/example
BUTTON_MARKER = "---"


def parse_buttons(text: str | None) -> tuple[str, InlineKeyboardMarkup | None]:
    """Split a broadcast message into (clean_text, inline_keyboard).

    Buttons are URL buttons defined after a line equal to ``---`` using the
    ``Label | https://url`` format (one button per row). If no valid buttons are
    found, the original text is returned unchanged with ``None``.
    """
    if not text:
        return text or "", None

    lines = text.splitlines()
    marker_idx: int | None = None
    for i, line in enumerate(lines):
        if line.strip() == BUTTON_MARKER:
            marker_idx = i
            break

    if marker_idx is None:
        return text, None

    body = "\n".join(lines[:marker_idx]).rstrip()
    button_lines = lines[marker_idx + 1:]

    builder = InlineKeyboardBuilder()
    count = 0
    for raw in button_lines:
        line = raw.strip()
        if not line or "|" not in line:
            continue
        label, _, url = line.partition("|")
        label = label.strip()
        url = url.strip()
        if not label or not (url.startswith("http://") or url.startswith("https://")):
            continue
        builder.button(text=label, url=url)
        count += 1

    if count == 0:
        # Marker present but no valid buttons — keep original text intact.
        return text, None

    builder.adjust(1)
    return body, builder.as_markup()
