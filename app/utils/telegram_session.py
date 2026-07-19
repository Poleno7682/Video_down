from __future__ import annotations

from aiogram.client.session.aiohttp import AiohttpSession
from aiogram.client.session.base import BaseSession
from aiogram.client.telegram import TelegramAPIServer

from app.core.config import Settings


def build_bot_session(settings: Settings) -> BaseSession | None:
    """Return a session pointed at a self-hosted Local Bot API server when
    configured, or None to use aiogram's default (api.telegram.org).

    The standard cloud Bot API caps file uploads/downloads at 50 MB
    regardless of app-level config; only a Local Bot API server
    (https://core.telegram.org/bots/api#using-a-local-bot-api-server) raises
    that to 2000 MB.
    """
    if not settings.use_local_bot_api:
        return None
    api = TelegramAPIServer.from_base(settings.local_bot_api_url, is_local=True)
    return AiohttpSession(api=api)
