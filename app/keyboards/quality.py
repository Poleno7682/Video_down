from aiogram.types import InlineKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder

from app.utils.quality import QUALITY_FORMATS

# Human-readable overrides for keys that don't read well as labels.
# Keys not present here fall back to the key itself (e.g. "360p", "720p").
_QUALITY_LABELS: dict[str, str] = {
    "best": "Лучшее",
    "audio": "Аудио",
}


def quality_keyboard() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    for key in QUALITY_FORMATS:
        builder.button(text=_QUALITY_LABELS.get(key, key), callback_data=f"quality:{key}")
    builder.adjust(3)
    return builder.as_markup()
