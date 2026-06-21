from __future__ import annotations

from typing import Any, Awaitable, Callable

from aiogram import BaseMiddleware
from aiogram.types import Message, TelegramObject

from app.bot.access import _check_access
from app.core.config import get_settings
from app.services.redis_client import get_redis


class AccessMiddleware(BaseMiddleware):
    """Checks user access before any Message handler on the protected router.

    Sends the denial message and stops the handler chain when access is denied.
    Admin users and public-mode bots always pass through.
    """

    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        if not isinstance(event, Message) or event.from_user is None:
            return await handler(event, data)

        settings = get_settings()
        redis = get_redis()
        allowed, denial_msg = _check_access(event.from_user.id, settings, redis)
        if not allowed:
            await event.answer(denial_msg)
            return

        return await handler(event, data)
