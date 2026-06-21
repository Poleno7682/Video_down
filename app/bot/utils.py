from __future__ import annotations

from aiogram.exceptions import TelegramBadRequest
from aiogram.types import Message


async def safe_edit_text(message: Message, text: str, **kwargs) -> None:
    """Edit a message, silently ignoring TelegramBadRequest (e.g. message not modified)."""
    try:
        await message.edit_text(text, **kwargs)
    except TelegramBadRequest:
        pass
