from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from app.services.runtime_config import (
    EDITABLE_LIMITS,
    clear_awaiting,
    format_value,
    get_awaiting,
    get_limit,
    reset_all_limits,
    reset_limit,
    set_awaiting,
    set_limit,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_settings(**overrides):
    s = MagicMock()
    defaults = {
        "user_daily_limit": 50,
        "user_queue_limit": 3,
        "global_queue_limit": 50,
        "max_active_downloads_per_user": 1,
        "max_file_mb": 50,
        "rate_limit_max_messages": 8,
        "rate_limit_window_seconds": 60,
        "ban_seconds": 600,
        "max_download_duration_seconds": 900,
        "cache_ttl_hours": 168,
    }
    defaults.update(overrides)
    for k, v in defaults.items():
        setattr(s, k, v)
    return s


def _make_redis(stored: dict | None = None):
    """Fake Redis matching decode_responses=True — values are str, not bytes."""
    store: dict[str, str] = {}
    if stored:
        store.update({k: str(v) for k, v in stored.items()})

    r = MagicMock()
    r.get.side_effect = lambda k: store.get(k)
    r.set.side_effect = lambda k, v: store.__setitem__(k, str(v))
    r.setex.side_effect = lambda k, ttl, v: store.__setitem__(k, str(v))
    r.delete.side_effect = lambda *keys: [store.pop(k, None) for k in keys]
    r.keys.side_effect = lambda pattern: [
        k for k in store if k.startswith(pattern.rstrip("*"))
    ]
    return r


# ---------------------------------------------------------------------------
# get_limit
# ---------------------------------------------------------------------------

class TestGetLimit:
    def test_returns_settings_default_when_no_override(self):
        redis = _make_redis()
        settings = _make_settings(user_daily_limit=50)
        assert get_limit("user_daily_limit", settings, redis) == 50

    def test_redis_override_takes_priority(self):
        redis = _make_redis({"runtime_limit:user_daily_limit": 99})
        settings = _make_settings(user_daily_limit=50)
        assert get_limit("user_daily_limit", settings, redis) == 99

    def test_zero_override_is_respected(self):
        redis = _make_redis({"runtime_limit:user_daily_limit": 0})
        settings = _make_settings(user_daily_limit=50)
        assert get_limit("user_daily_limit", settings, redis) == 0

    def test_all_fields_readable(self):
        redis = _make_redis()
        settings = _make_settings()
        for field in EDITABLE_LIMITS:
            val = get_limit(field, settings, redis)
            assert isinstance(val, int)


# ---------------------------------------------------------------------------
# set_limit / reset_limit / reset_all_limits
# ---------------------------------------------------------------------------

class TestSetResetLimits:
    def test_set_limit_overrides_default(self):
        redis = _make_redis()
        settings = _make_settings(max_file_mb=50)
        set_limit("max_file_mb", 200, redis)
        assert get_limit("max_file_mb", settings, redis) == 200

    def test_reset_limit_removes_override(self):
        redis = _make_redis({"runtime_limit:max_file_mb": 200})
        settings = _make_settings(max_file_mb=50)
        reset_limit("max_file_mb", redis)
        assert get_limit("max_file_mb", settings, redis) == 50

    def test_reset_all_limits(self):
        redis = _make_redis({
            "runtime_limit:user_daily_limit": 100,
            "runtime_limit:max_file_mb": 200,
        })
        settings = _make_settings(user_daily_limit=50, max_file_mb=50)
        reset_all_limits(redis)
        assert get_limit("user_daily_limit", settings, redis) == 50
        assert get_limit("max_file_mb", settings, redis) == 50

    def test_reset_all_is_noop_when_nothing_set(self):
        redis = _make_redis()
        # Should not raise even when there's nothing to delete
        reset_all_limits(redis)


# ---------------------------------------------------------------------------
# format_value
# ---------------------------------------------------------------------------

class TestFormatValue:
    def test_zero_on_disableable_field_shows_infinity(self):
        assert "∞" in format_value("user_daily_limit", 0)

    def test_nonzero_on_disableable_field_shows_number(self):
        result = format_value("user_daily_limit", 42)
        assert "42" in result
        assert "∞" not in result

    def test_zero_on_non_disableable_field_shows_number(self):
        # rate_limit_window_seconds has zero_disables=False — 0 has no special meaning
        result = format_value("rate_limit_window_seconds", 60)
        assert "60" in result
        assert "∞" not in result


# ---------------------------------------------------------------------------
# Awaiting state
# ---------------------------------------------------------------------------

class TestAwaitingState:
    def test_set_and_get_awaiting(self):
        redis = _make_redis()
        set_awaiting(42, "user_daily_limit", redis)
        assert get_awaiting(42, redis) == "user_daily_limit"

    def test_get_awaiting_returns_none_when_not_set(self):
        redis = _make_redis()
        assert get_awaiting(99, redis) is None

    def test_clear_awaiting_removes_state(self):
        redis = _make_redis()
        set_awaiting(42, "max_file_mb", redis)
        clear_awaiting(42, redis)
        assert get_awaiting(42, redis) is None


# ---------------------------------------------------------------------------
# EDITABLE_LIMITS structure
# ---------------------------------------------------------------------------

class TestEditableLimits:
    def test_all_fields_have_specs(self):
        for field, spec in EDITABLE_LIMITS.items():
            assert spec.label
            assert spec.unit
            assert spec.min_val <= spec.max_val

    def test_zero_disables_fields_have_zero_min(self):
        for field, spec in EDITABLE_LIMITS.items():
            if spec.zero_disables:
                assert spec.min_val == 0, f"{field}: zero_disables=True but min_val != 0"
