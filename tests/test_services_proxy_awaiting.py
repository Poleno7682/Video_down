from __future__ import annotations

from unittest.mock import MagicMock

from app.services.proxy_awaiting import clear_proxy_awaiting, get_proxy_awaiting, set_proxy_awaiting


def test_set_and_get_awaiting():
    redis = MagicMock()
    redis.get.return_value = b"socks5h"
    set_proxy_awaiting(1, "socks5h", redis)
    redis.setex.assert_called_once()
    assert get_proxy_awaiting(1, redis) == "socks5h"


def test_get_awaiting_none_when_missing():
    redis = MagicMock()
    redis.get.return_value = None
    assert get_proxy_awaiting(1, redis) is None


def test_get_awaiting_decodes_bytes():
    redis = MagicMock()
    redis.get.return_value = b"https"
    assert get_proxy_awaiting(1, redis) == "https"


def test_clear_awaiting():
    redis = MagicMock()
    clear_proxy_awaiting(1, redis)
    redis.delete.assert_called_once()
