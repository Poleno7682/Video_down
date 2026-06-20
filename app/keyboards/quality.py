from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup


def quality_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="360p", callback_data="quality:360p"),
                InlineKeyboardButton(text="480p", callback_data="quality:480p"),
                InlineKeyboardButton(text="720p", callback_data="quality:720p"),
            ],
            [
                InlineKeyboardButton(text="1080p", callback_data="quality:1080p"),
                InlineKeyboardButton(text="Лучшее", callback_data="quality:best"),
                InlineKeyboardButton(text="Аудио", callback_data="quality:audio"),
            ],
        ]
    )
