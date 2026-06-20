"""
Global test configuration.
Environment variables are set here BEFORE any project module is imported,
so module-level code in session.py and celery_app.py sees the right values.
"""
from __future__ import annotations

import os

# Must be set before importing any app module that calls get_settings() at module level.
os.environ.setdefault("BOT_TOKEN", "123456789:AATestTokenForTestingPurposesOnly")
os.environ.setdefault("WEBHOOK_BASE_URL", "https://test.example.com")
os.environ.setdefault("WEBHOOK_SECRET", "test-webhook-secret")
os.environ.setdefault("DATABASE_URL", "postgresql://test:test@localhost/test_db")
os.environ.setdefault("REDIS_URL", "redis://localhost:6379/15")
os.environ.setdefault("CELERY_BROKER_URL", "redis://localhost:6379/14")
os.environ.setdefault("CELERY_RESULT_BACKEND", "redis://localhost:6379/13")

import pytest

from app.core.config import get_settings


@pytest.fixture(autouse=True)
def clear_settings_cache():
    """Each test starts with a fresh Settings instance."""
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


@pytest.fixture(autouse=True, scope="session")
def configure_celery_eager():
    """Run Celery tasks synchronously during tests."""
    from app.worker.celery_app import celery_app
    celery_app.conf.update(task_always_eager=True, task_eager_propagates=True)


@pytest.fixture()
def mock_session(mocker):
    """A mock SQLAlchemy Session with a context-manager interface."""
    session = mocker.MagicMock()
    session.__enter__ = mocker.MagicMock(return_value=session)
    session.__exit__ = mocker.MagicMock(return_value=False)
    return session


@pytest.fixture()
def mock_redis(mocker):
    return mocker.MagicMock()
