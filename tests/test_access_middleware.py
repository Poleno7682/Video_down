from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.bot.filters import AdminFilter
from app.bot.middleware import AccessMiddleware


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_message(user_id: int = 1):
    msg = MagicMock()
    msg.from_user = MagicMock()
    msg.from_user.id = user_id
    msg.answer = AsyncMock()
    return msg


def _make_callback(user_id: int = 1):
    cb = MagicMock()
    cb.from_user = MagicMock()
    cb.from_user.id = user_id
    cb.answer = AsyncMock()
    return cb


def _make_settings(**kwargs):
    s = MagicMock()
    s.admin_user_ids = set()
    s.allowed_user_ids = set()
    for k, v in kwargs.items():
        setattr(s, k, v)
    return s


def _make_redis(disabled=False, trusted_count=0, user_trusted=False):
    r = MagicMock()
    r.exists.return_value = 1 if disabled else 0
    r.scard.return_value = trusted_count
    r.sismember.return_value = user_trusted
    return r


# ---------------------------------------------------------------------------
# AccessMiddleware
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_middleware_passes_through_non_message():
    mw = AccessMiddleware()
    handler = AsyncMock(return_value="ok")
    non_message = MagicMock()  # not a Message instance
    result = await mw(handler, non_message, {})
    handler.assert_awaited_once_with(non_message, {})
    assert result == "ok"


@pytest.mark.asyncio
async def test_middleware_passes_through_message_no_user():
    from aiogram.types import Message
    mw = AccessMiddleware()
    handler = AsyncMock(return_value="ok")
    msg = MagicMock(spec=Message)
    msg.from_user = None
    result = await mw(handler, msg, {})
    handler.assert_awaited_once()


@pytest.mark.asyncio
async def test_middleware_blocks_bot_disabled():
    from aiogram.types import Message
    mw = AccessMiddleware()
    handler = AsyncMock()
    msg = MagicMock(spec=Message)
    msg.from_user = MagicMock()
    msg.from_user.id = 999
    msg.answer = AsyncMock()

    settings = _make_settings()
    redis = _make_redis(disabled=True)

    with patch("app.bot.middleware.get_settings", return_value=settings), \
         patch("app.bot.middleware.get_redis", return_value=redis):
        await mw(handler, msg, {})

    handler.assert_not_awaited()
    msg.answer.assert_awaited_once()
    text = msg.answer.call_args[0][0]
    assert "🔴" in text or "недоступен" in text.lower()


@pytest.mark.asyncio
async def test_middleware_blocks_static_whitelist_non_member():
    from aiogram.types import Message
    mw = AccessMiddleware()
    handler = AsyncMock()
    msg = MagicMock(spec=Message)
    msg.from_user = MagicMock()
    msg.from_user.id = 999
    msg.answer = AsyncMock()

    settings = _make_settings(allowed_user_ids={1, 2, 3})
    redis = _make_redis()

    with patch("app.bot.middleware.get_settings", return_value=settings), \
         patch("app.bot.middleware.get_redis", return_value=redis):
        await mw(handler, msg, {})

    handler.assert_not_awaited()
    assert "⛔" in msg.answer.call_args[0][0]


@pytest.mark.asyncio
async def test_middleware_allows_admin_even_when_disabled():
    from aiogram.types import Message
    mw = AccessMiddleware()
    handler = AsyncMock(return_value=None)
    msg = MagicMock(spec=Message)
    msg.from_user = MagicMock()
    msg.from_user.id = 42
    msg.answer = AsyncMock()

    settings = _make_settings(admin_user_ids={42})
    redis = _make_redis(disabled=True)

    with patch("app.bot.middleware.get_settings", return_value=settings), \
         patch("app.bot.middleware.get_redis", return_value=redis):
        await mw(handler, msg, {})

    handler.assert_awaited_once()
    msg.answer.assert_not_awaited()


@pytest.mark.asyncio
async def test_middleware_allows_public_mode():
    from aiogram.types import Message
    mw = AccessMiddleware()
    handler = AsyncMock(return_value=None)
    msg = MagicMock(spec=Message)
    msg.from_user = MagicMock()
    msg.from_user.id = 99
    msg.answer = AsyncMock()

    settings = _make_settings()  # public mode: no whitelist, no trusted users
    redis = _make_redis(trusted_count=0)

    with patch("app.bot.middleware.get_settings", return_value=settings), \
         patch("app.bot.middleware.get_redis", return_value=redis):
        await mw(handler, msg, {})

    handler.assert_awaited_once()
    msg.answer.assert_not_awaited()


@pytest.mark.asyncio
async def test_middleware_allows_trusted_user():
    from aiogram.types import Message
    mw = AccessMiddleware()
    handler = AsyncMock(return_value=None)
    msg = MagicMock(spec=Message)
    msg.from_user = MagicMock()
    msg.from_user.id = 55
    msg.answer = AsyncMock()

    settings = _make_settings()
    redis = _make_redis(trusted_count=2, user_trusted=True)

    with patch("app.bot.middleware.get_settings", return_value=settings), \
         patch("app.bot.middleware.get_redis", return_value=redis):
        await mw(handler, msg, {})

    handler.assert_awaited_once()
    msg.answer.assert_not_awaited()


@pytest.mark.asyncio
async def test_middleware_blocks_untrusted_user():
    from aiogram.types import Message
    mw = AccessMiddleware()
    handler = AsyncMock()
    msg = MagicMock(spec=Message)
    msg.from_user = MagicMock()
    msg.from_user.id = 55
    msg.answer = AsyncMock()

    settings = _make_settings()
    redis = _make_redis(trusted_count=2, user_trusted=False)

    with patch("app.bot.middleware.get_settings", return_value=settings), \
         patch("app.bot.middleware.get_redis", return_value=redis):
        await mw(handler, msg, {})

    handler.assert_not_awaited()
    assert "⛔" in msg.answer.call_args[0][0]


# ---------------------------------------------------------------------------
# AdminFilter
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_admin_filter_allows_admin_message():
    from aiogram.types import Message
    msg = MagicMock(spec=Message)
    msg.from_user = MagicMock()
    msg.from_user.id = 7

    settings = _make_settings(admin_user_ids={7})
    with patch("app.bot.filters.get_settings", return_value=settings):
        result = await AdminFilter()(msg)

    assert result is True


@pytest.mark.asyncio
async def test_admin_filter_denies_non_admin_message_silently():
    from aiogram.types import Message
    msg = MagicMock(spec=Message)
    msg.from_user = MagicMock()
    msg.from_user.id = 99

    settings = _make_settings(admin_user_ids={7})
    with patch("app.bot.filters.get_settings", return_value=settings):
        result = await AdminFilter(alert_on_deny=True)(msg)

    assert result is False
    msg.answer.assert_not_called()


@pytest.mark.asyncio
async def test_admin_filter_alerts_non_admin_callback():
    from aiogram.types import CallbackQuery
    cb = MagicMock(spec=CallbackQuery)
    cb.from_user = MagicMock()
    cb.from_user.id = 99
    cb.answer = AsyncMock()

    settings = _make_settings(admin_user_ids={7})
    with patch("app.bot.filters.get_settings", return_value=settings):
        result = await AdminFilter(alert_on_deny=True)(cb)

    assert result is False
    cb.answer.assert_awaited_once()
    assert "⛔" in cb.answer.call_args[0][0]
    assert cb.answer.call_args[1].get("show_alert") is True


@pytest.mark.asyncio
async def test_admin_filter_no_alert_when_alert_on_deny_false():
    from aiogram.types import CallbackQuery
    cb = MagicMock(spec=CallbackQuery)
    cb.from_user = MagicMock()
    cb.from_user.id = 99
    cb.answer = AsyncMock()

    settings = _make_settings(admin_user_ids={7})
    with patch("app.bot.filters.get_settings", return_value=settings):
        result = await AdminFilter(alert_on_deny=False)(cb)

    assert result is False
    cb.answer.assert_not_awaited()


@pytest.mark.asyncio
async def test_admin_filter_allows_admin_callback():
    from aiogram.types import CallbackQuery
    cb = MagicMock(spec=CallbackQuery)
    cb.from_user = MagicMock()
    cb.from_user.id = 7
    cb.answer = AsyncMock()

    settings = _make_settings(admin_user_ids={7})
    with patch("app.bot.filters.get_settings", return_value=settings):
        result = await AdminFilter(alert_on_deny=True)(cb)

    assert result is True
    cb.answer.assert_not_awaited()


@pytest.mark.asyncio
async def test_admin_filter_no_user():
    from aiogram.types import Message
    msg = MagicMock(spec=Message)
    msg.from_user = None

    settings = _make_settings(admin_user_ids={7})
    with patch("app.bot.filters.get_settings", return_value=settings):
        result = await AdminFilter()(msg)

    assert result is False
