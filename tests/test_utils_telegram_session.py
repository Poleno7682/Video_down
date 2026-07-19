from __future__ import annotations

from unittest.mock import MagicMock

from aiogram.client.session.aiohttp import AiohttpSession

from app.utils.telegram_session import build_bot_session


def _make_settings(use_local_bot_api=False, local_bot_api_url="http://telegram-bot-api:8081"):
    settings = MagicMock()
    settings.use_local_bot_api = use_local_bot_api
    settings.local_bot_api_url = local_bot_api_url
    return settings


def test_returns_none_when_disabled():
    assert build_bot_session(_make_settings(use_local_bot_api=False)) is None


def test_returns_session_pointed_at_local_server_when_enabled():
    session = build_bot_session(_make_settings(use_local_bot_api=True))
    assert isinstance(session, AiohttpSession)
    assert session.api.base == "http://telegram-bot-api:8081/bot{token}/{method}"
    assert session.api.file == "http://telegram-bot-api:8081/file/bot{token}/{path}"
    assert session.api.is_local is True


def test_uses_configured_local_bot_api_url():
    session = build_bot_session(
        _make_settings(use_local_bot_api=True, local_bot_api_url="http://custom-host:9999")
    )
    assert session.api.base == "http://custom-host:9999/bot{token}/{method}"
