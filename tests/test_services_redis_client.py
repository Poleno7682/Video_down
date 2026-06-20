from __future__ import annotations

import app.services.redis_client as _module


def _reset_singleton():
    _module._redis = None


def test_get_redis_returns_client(mocker):
    _reset_singleton()
    mock_redis = mocker.MagicMock()
    mocker.patch("app.services.redis_client.Redis.from_url", return_value=mock_redis)
    result = _module.get_redis()
    assert result is mock_redis


def test_get_redis_singleton(mocker):
    _reset_singleton()
    mock_redis = mocker.MagicMock()
    mocker.patch("app.services.redis_client.Redis.from_url", return_value=mock_redis)
    r1 = _module.get_redis()
    r2 = _module.get_redis()
    assert r1 is r2


def test_get_redis_creates_only_once(mocker):
    _reset_singleton()
    mock_redis = mocker.MagicMock()
    from_url = mocker.patch("app.services.redis_client.Redis.from_url", return_value=mock_redis)
    _module.get_redis()
    _module.get_redis()
    _module.get_redis()
    assert from_url.call_count == 1


def test_get_redis_uses_settings_url(mocker):
    _reset_singleton()
    mock_settings = mocker.MagicMock()
    mock_settings.redis_url = "redis://testhost:6379/7"
    mocker.patch("app.services.redis_client.get_settings", return_value=mock_settings)
    mock_redis = mocker.MagicMock()
    from_url = mocker.patch("app.services.redis_client.Redis.from_url", return_value=mock_redis)
    _module.get_redis()
    from_url.assert_called_once_with("redis://testhost:6379/7", decode_responses=True)
    _reset_singleton()
