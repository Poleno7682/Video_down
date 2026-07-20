from __future__ import annotations

from unittest.mock import MagicMock

from app.services.rezka_session import clear_rezka_session, get_rezka_session, set_rezka_session


def test_set_rezka_session_stores_json_with_ttl():
    redis = MagicMock()
    set_rezka_session(1, {"url": "https://rezka.ag/x.html", "quality": "720p"}, redis)
    redis.setex.assert_called_once()
    key, ttl, payload = redis.setex.call_args[0]
    assert key == "rezka_session:1"
    assert ttl == 600
    assert "https://rezka.ag/x.html" in payload


def test_get_rezka_session_roundtrips():
    redis = MagicMock()
    stored = {}

    def _setex(key, ttl, value):
        stored[key] = value

    def _get(key):
        return stored.get(key)

    redis.setex.side_effect = _setex
    redis.get.side_effect = _get

    set_rezka_session(1, {"translator_id": 56, "season": 2}, redis)
    session = get_rezka_session(1, redis)
    assert session == {"translator_id": 56, "season": 2}


def test_get_rezka_session_returns_none_when_missing():
    redis = MagicMock()
    redis.get.return_value = None
    assert get_rezka_session(1, redis) is None


def test_get_rezka_session_returns_none_on_corrupt_json():
    redis = MagicMock()
    redis.get.return_value = "not json"
    assert get_rezka_session(1, redis) is None


def test_clear_rezka_session_deletes_key():
    redis = MagicMock()
    clear_rezka_session(1, redis)
    redis.delete.assert_called_once_with("rezka_session:1")


def test_sessions_are_isolated_per_user():
    redis = MagicMock()
    stored = {}
    redis.setex.side_effect = lambda k, t, v: stored.__setitem__(k, v)
    redis.get.side_effect = lambda k: stored.get(k)

    set_rezka_session(1, {"user": "one"}, redis)
    set_rezka_session(2, {"user": "two"}, redis)

    assert get_rezka_session(1, redis) == {"user": "one"}
    assert get_rezka_session(2, redis) == {"user": "two"}
