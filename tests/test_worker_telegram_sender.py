from __future__ import annotations

import asyncio
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

import app.worker.telegram_sender as sender_module
from app.db.models import TelegramFileType
from app.worker.telegram_sender import (
    VIDEO_EXTENSIONS,
    AUDIO_EXTENSIONS,
    _SUFFIX_TO_FILE_TYPE,
    _UPLOAD_TIMEOUT,
    _get_bot,
    _get_loop,
    close_bot_session,
    edit_status,
    send_cached,
    send_file,
)


def _reset_singletons():
    sender_module._loop = None
    sender_module._bot = None


@pytest.fixture(autouse=True)
def reset_singletons():
    _reset_singletons()
    yield
    _reset_singletons()


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

def test_upload_timeout_constant():
    assert _UPLOAD_TIMEOUT == 600


def test_suffix_to_file_type_video():
    for ext in VIDEO_EXTENSIONS:
        assert _SUFFIX_TO_FILE_TYPE[ext] == TelegramFileType.video


def test_suffix_to_file_type_audio():
    for ext in AUDIO_EXTENSIONS:
        assert _SUFFIX_TO_FILE_TYPE[ext] == TelegramFileType.audio


def test_suffix_to_file_type_no_document_key():
    # Documents are the default (not in the dict)
    assert ".pdf" not in _SUFFIX_TO_FILE_TYPE
    assert ".txt" not in _SUFFIX_TO_FILE_TYPE


# ---------------------------------------------------------------------------
# _get_loop
# ---------------------------------------------------------------------------

def test_get_loop_creates_loop():
    loop = _get_loop()
    assert loop is not None
    assert loop.is_running()


def test_get_loop_singleton():
    loop1 = _get_loop()
    loop2 = _get_loop()
    assert loop1 is loop2


def test_get_loop_recreates_if_closed():
    loop1 = _get_loop()
    sender_module._loop = None  # reset to simulate closed
    loop2 = _get_loop()
    # Both are valid running loops; in this test we just reset manually
    assert loop2 is not None


# ---------------------------------------------------------------------------
# _get_bot
# ---------------------------------------------------------------------------

def test_get_bot_creates_bot(mocker):
    mock_bot = MagicMock()
    mocker.patch("app.worker.telegram_sender.Bot", return_value=mock_bot)
    mocker.patch("app.worker.telegram_sender.get_settings")
    bot = _get_bot()
    assert bot is mock_bot


def test_get_bot_singleton(mocker):
    mock_bot = MagicMock()
    mocker.patch("app.worker.telegram_sender.Bot", return_value=mock_bot)
    mocker.patch("app.worker.telegram_sender.get_settings")
    b1 = _get_bot()
    b2 = _get_bot()
    assert b1 is b2


# ---------------------------------------------------------------------------
# close_bot_session
# ---------------------------------------------------------------------------

def test_close_bot_session_when_bot_exists(mocker):
    mock_bot = MagicMock()
    mock_bot.session = MagicMock()
    mock_bot.session.close = AsyncMock()
    sender_module._bot = mock_bot

    mocker.patch("app.worker.telegram_sender._run")

    close_bot_session()
    assert sender_module._bot is None


def test_close_bot_session_when_no_bot():
    sender_module._bot = None
    # Should not raise
    close_bot_session()


def test_close_bot_session_swallows_exception(mocker):
    mock_bot = MagicMock()
    sender_module._bot = mock_bot
    mocker.patch("app.worker.telegram_sender._run", side_effect=RuntimeError("oops"))
    # Should not raise
    close_bot_session()
    assert sender_module._bot is None


# ---------------------------------------------------------------------------
# Async helpers (tested via _run with a real loop)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_send_file_video(mocker):
    msg = MagicMock()
    msg.video.file_id = "video_fid"
    msg.video.file_unique_id = "video_uid"

    mock_bot = MagicMock()
    mock_bot.send_video = AsyncMock(return_value=msg)
    mocker.patch("app.worker.telegram_sender._get_bot", return_value=mock_bot)
    mocker.patch("app.worker.telegram_sender.FSInputFile")

    from app.worker.telegram_sender import _send_file_async
    fid, uid, ftype = await _send_file_async(123, Path("video.mp4"), "caption")
    assert fid == "video_fid"
    assert ftype == TelegramFileType.video


@pytest.mark.asyncio
async def test_send_file_video_passes_dimension_hints(mocker):
    """Width/height/duration must reach Bot API so Telegram doesn't fall back
    to a square placeholder while it probes the file itself."""
    msg = MagicMock()
    msg.video.file_id = "video_fid"
    msg.video.file_unique_id = "video_uid"

    mock_bot = MagicMock()
    mock_bot.send_video = AsyncMock(return_value=msg)
    mocker.patch("app.worker.telegram_sender._get_bot", return_value=mock_bot)
    mocker.patch("app.worker.telegram_sender.FSInputFile")

    from app.worker.telegram_sender import _send_file_async
    await _send_file_async(123, Path("video.mp4"), "caption", width=1080, height=1920, duration=42)

    _, kwargs = mock_bot.send_video.call_args
    assert kwargs["width"] == 1080
    assert kwargs["height"] == 1920
    assert kwargs["duration"] == 42


@pytest.mark.asyncio
async def test_send_file_video_omits_hints_when_missing(mocker):
    msg = MagicMock()
    msg.video.file_id = "video_fid"
    msg.video.file_unique_id = "video_uid"

    mock_bot = MagicMock()
    mock_bot.send_video = AsyncMock(return_value=msg)
    mocker.patch("app.worker.telegram_sender._get_bot", return_value=mock_bot)
    mocker.patch("app.worker.telegram_sender.FSInputFile")

    from app.worker.telegram_sender import _send_file_async
    await _send_file_async(123, Path("video.mp4"), "caption")

    _, kwargs = mock_bot.send_video.call_args
    assert "width" not in kwargs
    assert "height" not in kwargs
    assert "duration" not in kwargs


@pytest.mark.asyncio
async def test_send_file_document_ignores_dimension_hints(mocker):
    """send_document has no width/height/duration params — must not be passed."""
    msg = MagicMock()
    msg.document.file_id = "doc_fid"
    msg.document.file_unique_id = "doc_uid"

    mock_bot = MagicMock()
    mock_bot.send_document = AsyncMock(return_value=msg)
    mocker.patch("app.worker.telegram_sender._get_bot", return_value=mock_bot)
    mocker.patch("app.worker.telegram_sender.FSInputFile")

    from app.worker.telegram_sender import _send_file_async
    await _send_file_async(123, Path("file.pdf"), "cap", width=100, height=100, duration=5)

    _, kwargs = mock_bot.send_document.call_args
    assert "width" not in kwargs
    assert "height" not in kwargs
    assert "duration" not in kwargs


@pytest.mark.asyncio
async def test_send_file_audio(mocker):
    msg = MagicMock()
    msg.audio.file_id = "audio_fid"
    msg.audio.file_unique_id = "audio_uid"

    mock_bot = MagicMock()
    mock_bot.send_audio = AsyncMock(return_value=msg)
    mocker.patch("app.worker.telegram_sender._get_bot", return_value=mock_bot)
    mocker.patch("app.worker.telegram_sender.FSInputFile")

    from app.worker.telegram_sender import _send_file_async
    fid, uid, ftype = await _send_file_async(123, Path("audio.mp3"), "cap")
    assert fid == "audio_fid"
    assert ftype == TelegramFileType.audio


@pytest.mark.asyncio
async def test_send_file_document(mocker):
    msg = MagicMock()
    msg.document.file_id = "doc_fid"
    msg.document.file_unique_id = "doc_uid"

    mock_bot = MagicMock()
    mock_bot.send_document = AsyncMock(return_value=msg)
    mocker.patch("app.worker.telegram_sender._get_bot", return_value=mock_bot)
    mocker.patch("app.worker.telegram_sender.FSInputFile")

    from app.worker.telegram_sender import _send_file_async
    fid, uid, ftype = await _send_file_async(123, Path("file.pdf"), "cap")
    assert fid == "doc_fid"
    assert ftype == TelegramFileType.document


@pytest.mark.asyncio
async def test_send_cached_video(mocker):
    mock_bot = MagicMock()
    mock_bot.send_video = AsyncMock()
    mocker.patch("app.worker.telegram_sender._get_bot", return_value=mock_bot)

    from app.worker.telegram_sender import _send_cached_async
    await _send_cached_async(123, "fid", TelegramFileType.video, "cap")
    mock_bot.send_video.assert_awaited_once()


@pytest.mark.asyncio
async def test_send_cached_audio(mocker):
    mock_bot = MagicMock()
    mock_bot.send_audio = AsyncMock()
    mocker.patch("app.worker.telegram_sender._get_bot", return_value=mock_bot)

    from app.worker.telegram_sender import _send_cached_async
    await _send_cached_async(123, "fid", TelegramFileType.audio, "cap")
    mock_bot.send_audio.assert_awaited_once()


@pytest.mark.asyncio
async def test_send_cached_document(mocker):
    mock_bot = MagicMock()
    mock_bot.send_document = AsyncMock()
    mocker.patch("app.worker.telegram_sender._get_bot", return_value=mock_bot)

    from app.worker.telegram_sender import _send_cached_async
    await _send_cached_async(123, "fid", TelegramFileType.document, "cap")
    mock_bot.send_document.assert_awaited_once()


@pytest.mark.asyncio
async def test_edit_status_no_message_id(mocker):
    mock_bot = MagicMock()
    mocker.patch("app.worker.telegram_sender._get_bot", return_value=mock_bot)

    from app.worker.telegram_sender import _edit_status_async
    await _edit_status_async(123, None, "text")
    mock_bot.edit_message_text.assert_not_called()


@pytest.mark.asyncio
async def test_edit_status_success(mocker):
    mock_bot = MagicMock()
    mock_bot.edit_message_text = AsyncMock()
    mocker.patch("app.worker.telegram_sender._get_bot", return_value=mock_bot)

    from app.worker.telegram_sender import _edit_status_async
    await _edit_status_async(123, 456, "text")
    mock_bot.edit_message_text.assert_awaited_once_with(chat_id=123, message_id=456, text="text")


@pytest.mark.asyncio
async def test_edit_status_exception_swallowed(mocker):
    mock_bot = MagicMock()
    mock_bot.edit_message_text = AsyncMock(side_effect=Exception("Message not found"))
    mocker.patch("app.worker.telegram_sender._get_bot", return_value=mock_bot)

    from app.worker.telegram_sender import _edit_status_async
    # Should not raise
    await _edit_status_async(123, 456, "text")


# ---------------------------------------------------------------------------
# Sync wrappers
# ---------------------------------------------------------------------------

def test_send_file_calls_run(mocker):
    mock_run = mocker.patch("app.worker.telegram_sender._run", return_value=("fid", "uid", TelegramFileType.video))
    result = send_file(1, Path("v.mp4"), "cap")
    assert result == ("fid", "uid", TelegramFileType.video)
    mock_run.assert_called_once()


def test_send_cached_calls_run(mocker):
    mock_run = mocker.patch("app.worker.telegram_sender._run")
    send_cached(1, "fid", TelegramFileType.video, "cap")
    mock_run.assert_called_once()


def test_edit_status_calls_run(mocker):
    mock_run = mocker.patch("app.worker.telegram_sender._run")
    edit_status(1, 2, "text")
    mock_run.assert_called_once()


# ---------------------------------------------------------------------------
# _run directly (lines 63-65)
# ---------------------------------------------------------------------------

def test_run_executes_coroutine_on_background_loop():
    """_run submits a coroutine to the background event loop and returns the result."""
    from app.worker.telegram_sender import _run

    async def sample_coro():
        return "hello"

    result = _run(sample_coro(), timeout=5)
    assert result == "hello"


def test_run_propagates_exception():
    from app.worker.telegram_sender import _run

    async def failing_coro():
        raise ValueError("from coro")

    with pytest.raises(ValueError, match="from coro"):
        _run(failing_coro(), timeout=5)
