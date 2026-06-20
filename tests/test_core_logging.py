from __future__ import annotations

import logging
import tempfile
from pathlib import Path

import pytest

from app.core.logging import setup_logging


def _close_file_handlers():
    root = logging.getLogger()
    for h in list(root.handlers):
        try:
            h.close()
        except Exception:
            pass
    root.handlers.clear()


@pytest.fixture(autouse=True)
def cleanup_handlers():
    yield
    _close_file_handlers()


def test_setup_logging_adds_handlers():
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
        setup_logging(Path(tmp))
        root = logging.getLogger()
        assert len(root.handlers) == 2
        _close_file_handlers()


def test_setup_logging_creates_log_file():
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
        log_dir = Path(tmp)
        setup_logging(log_dir)
        exists = (log_dir / "app.log").exists()
        _close_file_handlers()
    assert exists


def test_setup_logging_replaces_existing_handlers():
    dummy = logging.NullHandler()
    logging.getLogger().addHandler(dummy)

    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
        setup_logging(Path(tmp))
        root = logging.getLogger()
        assert len(root.handlers) == 2
        assert dummy not in root.handlers
        _close_file_handlers()


def test_setup_logging_sets_info_level():
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
        setup_logging(Path(tmp))
        level = logging.getLogger().level
        _close_file_handlers()
    assert level == logging.INFO


def test_setup_logging_creates_missing_directory():
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
        nested = Path(tmp) / "sub" / "logs"
        setup_logging(nested)
        exists = (nested / "app.log").exists()
        _close_file_handlers()
    assert exists
