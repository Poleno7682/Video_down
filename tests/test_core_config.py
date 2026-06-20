from __future__ import annotations

import os
import logging

import pytest

from app.core.config import Settings, _parse_ids, get_settings


class TestParseIds:
    def test_valid_ids(self):
        assert _parse_ids("123,456,789") == {123, 456, 789}

    def test_empty_string(self):
        assert _parse_ids("") == set()

    def test_whitespace_only(self):
        assert _parse_ids("   ") == set()

    def test_invalid_id_skipped(self, caplog):
        with caplog.at_level(logging.WARNING, logger="app.core.config"):
            result = _parse_ids("123,abc,456")
        assert result == {123, 456}
        assert "abc" in caplog.text

    def test_negative_not_digit(self):
        # Negative numbers are not digits (isdigit is False for "-1")
        result = _parse_ids("-1,100")
        assert result == {100}

    def test_spaces_around_ids(self):
        assert _parse_ids(" 1 , 2 , 3 ") == {1, 2, 3}


class TestSettings:
    def test_webhook_url_property(self):
        s = Settings(
            BOT_TOKEN="1:t",
            WEBHOOK_BASE_URL="https://example.com/",
            WEBHOOK_SECRET="s",
            DATABASE_URL="postgresql://x",
            REDIS_URL="redis://x",
            CELERY_BROKER_URL="redis://x",
            CELERY_RESULT_BACKEND="redis://x",
        )
        assert s.webhook_url == "https://example.com/telegram/webhook"

    def test_webhook_url_no_trailing_slash(self):
        s = Settings(
            BOT_TOKEN="1:t",
            WEBHOOK_BASE_URL="https://example.com",
            WEBHOOK_SECRET="s",
            DATABASE_URL="postgresql://x",
            REDIS_URL="redis://x",
            CELERY_BROKER_URL="redis://x",
            CELERY_RESULT_BACKEND="redis://x",
        )
        assert s.webhook_url == "https://example.com/telegram/webhook"

    def test_allowed_user_ids_cached(self):
        s = Settings(
            BOT_TOKEN="1:t",
            WEBHOOK_BASE_URL="https://x.com",
            WEBHOOK_SECRET="s",
            DATABASE_URL="postgresql://x",
            REDIS_URL="redis://x",
            CELERY_BROKER_URL="redis://x",
            CELERY_RESULT_BACKEND="redis://x",
            ALLOWED_USERS="1,2,3",
        )
        ids1 = s.allowed_user_ids
        ids2 = s.allowed_user_ids
        assert ids1 == {1, 2, 3}
        assert ids1 is ids2  # same object (PrivateAttr, computed once)

    def test_admin_user_ids(self):
        s = Settings(
            BOT_TOKEN="1:t",
            WEBHOOK_BASE_URL="https://x.com",
            WEBHOOK_SECRET="s",
            DATABASE_URL="postgresql://x",
            REDIS_URL="redis://x",
            CELERY_BROKER_URL="redis://x",
            CELERY_RESULT_BACKEND="redis://x",
            ADMIN_USERS="10,20",
        )
        assert s.admin_user_ids == {10, 20}

    def test_empty_allowed_users(self):
        s = Settings(
            BOT_TOKEN="1:t",
            WEBHOOK_BASE_URL="https://x.com",
            WEBHOOK_SECRET="s",
            DATABASE_URL="postgresql://x",
            REDIS_URL="redis://x",
            CELERY_BROKER_URL="redis://x",
            CELERY_RESULT_BACKEND="redis://x",
        )
        assert s.allowed_user_ids == set()

    def test_default_max_file_mb(self):
        s = Settings(
            BOT_TOKEN="1:t",
            WEBHOOK_BASE_URL="https://x.com",
            WEBHOOK_SECRET="s",
            DATABASE_URL="postgresql://x",
            REDIS_URL="redis://x",
            CELERY_BROKER_URL="redis://x",
            CELERY_RESULT_BACKEND="redis://x",
        )
        assert s.max_file_mb == 50


class TestGetSettings:
    def test_returns_settings_instance(self):
        s = get_settings()
        assert isinstance(s, Settings)

    def test_is_cached(self):
        s1 = get_settings()
        s2 = get_settings()
        assert s1 is s2

    def test_cache_cleared_between_tests(self):
        # conftest clears cache; this just verifies the fixture works
        s = get_settings()
        assert s is not None
