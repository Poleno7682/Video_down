from __future__ import annotations

from aiogram.filters import BaseFilter
from aiogram.types import CallbackQuery, Message

from app.bot.access import _is_admin
from app.core.config import get_settings
from app.utils.magnet import is_torrent_document


class AdminFilter(BaseFilter):
    """Passes only admin users.

    For CallbackQuery: answers with show_alert when alert_on_deny=True.
    For Message: returns False silently.
    """

    def __init__(self, alert_on_deny: bool = False) -> None:
        self._alert_on_deny = alert_on_deny

    async def __call__(self, event: Message | CallbackQuery) -> bool:
        user = event.from_user
        if user and _is_admin(user.id, get_settings()):
            return True
        if self._alert_on_deny and isinstance(event, CallbackQuery):
            await event.answer("⛔ Нет доступа", show_alert=True)
        return False


class TorrentFileFilter(BaseFilter):
    """Passes messages whose document is an uploaded .torrent file.

    Must be registered ahead of cookies.py's catch-all F.document handler
    (see app.bot.router) or a .torrent upload would be swallowed by the
    cookies flow's "not a recognised platform filename" reply instead.
    """

    async def __call__(self, message: Message) -> bool:
        document = message.document
        if not document:
            return False
        return is_torrent_document(document.file_name, document.mime_type)
