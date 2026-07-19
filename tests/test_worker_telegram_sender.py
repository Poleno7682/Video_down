from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

import app.worker.telegram_sender as sender_module
from app.db.models import TelegramFileType
from app.worker.telegram_sender import (
    VIDEO_EXTENSIONS,
    AUDIO_EXTENSIONS,
    _SUFFIX_TO_FILE_TYPE,
    _UPLOAD_TIMEOUT,
    TelegramSender,
    close_bot_session,
    get_default_sender,
)


@pytest.fixture(autouse=True)
def reset_default_sender():
    sender_module._default_sender = None
    yield
    sender_module._default_sender = None


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
# get_default_sender
# ---------------------------------------------------------------------------

def test_get_default_sender_returns_instance():
    sender = get_default_sender()
    assert isinstance(sender, TelegramSender)


def test_get_default_sender_singleton():
    s1 = get_default_sender()
    s2 = get_default_sender()
    assert s1 is s2


def test_new_instances_are_independent():
    a = TelegramSender()
    b = TelegramSender()
    assert a is not b
    assert a._bot is None
    assert b._bot is None


# ---------------------------------------------------------------------------
# TelegramSender._get_loop
# ---------------------------------------------------------------------------

def test_get_loop_creates_loop():
    sender = TelegramSender()
    loop = sender._get_loop()
    assert loop is not None
    assert loop.is_running()


def test_get_loop_singleton_per_instance():
    sender = TelegramSender()
    loop1 = sender._get_loop()
    loop2 = sender._get_loop()
    assert loop1 is loop2


def test_get_loop_recreates_if_closed():
    sender = TelegramSender()
    sender._get_loop()
    sender._loop = None  # reset to simulate closed
    loop2 = sender._get_loop()
    assert loop2 is not None


# ---------------------------------------------------------------------------
# TelegramSender._get_bot
# ---------------------------------------------------------------------------

def test_get_bot_creates_bot(mocker):
    mock_bot = MagicMock()
    mocker.patch("app.worker.telegram_sender.Bot", return_value=mock_bot)
    mocker.patch("app.worker.telegram_sender.get_settings")
    sender = TelegramSender()
    bot = sender._get_bot()
    assert bot is mock_bot


def test_get_bot_singleton(mocker):
    mock_bot = MagicMock()
    mocker.patch("app.worker.telegram_sender.Bot", return_value=mock_bot)
    mocker.patch("app.worker.telegram_sender.get_settings")
    sender = TelegramSender()
    b1 = sender._get_bot()
    b2 = sender._get_bot()
    assert b1 is b2


def test_get_bot_uses_explicit_token(mocker):
    mock_bot_cls = mocker.patch("app.worker.telegram_sender.Bot")
    mock_get_settings = mocker.patch("app.worker.telegram_sender.get_settings")
    sender = TelegramSender(token="explicit-token")
    sender._get_bot()
    assert mock_bot_cls.call_args.kwargs["token"] == "explicit-token"
    mock_get_settings.assert_not_called()


# ---------------------------------------------------------------------------
# close_session / close_bot_session
# ---------------------------------------------------------------------------

def test_close_session_when_bot_exists(mocker):
    mock_bot = MagicMock()
    mock_bot.session = MagicMock()
    mock_bot.session.close = AsyncMock()
    sender = TelegramSender()
    sender._bot = mock_bot

    mocker.patch.object(sender, "_run")

    sender.close_session()
    assert sender._bot is None


def test_close_session_when_no_bot():
    sender = TelegramSender()
    # Should not raise
    sender.close_session()


def test_close_session_swallows_exception(mocker):
    mock_bot = MagicMock()
    sender = TelegramSender()
    sender._bot = mock_bot
    mocker.patch.object(sender, "_run", side_effect=RuntimeError("oops"))
    # Should not raise
    sender.close_session()
    assert sender._bot is None


def test_close_bot_session_when_no_default_instance():
    # Should not raise, and should not create a sender just to close it
    close_bot_session()
    assert sender_module._default_sender is None


def test_close_bot_session_closes_default_instance(mocker):
    sender = get_default_sender()
    mock_close = mocker.patch.object(sender, "close_session")
    close_bot_session()
    mock_close.assert_called_once()
    assert sender_module._default_sender is None


# ---------------------------------------------------------------------------
# Async helpers (tested via _run with a real loop)
# ---------------------------------------------------------------------------

def _sender_with_bot(mocker, **bot_methods) -> TelegramSender:
    sender = TelegramSender()
    mock_bot = MagicMock()
    for name, value in bot_methods.items():
        setattr(mock_bot, name, value)
    mocker.patch.object(sender, "_get_bot", return_value=mock_bot)
    return sender, mock_bot


@pytest.mark.asyncio
async def test_send_file_video(mocker):
    msg = MagicMock()
    msg.video.file_id = "video_fid"
    msg.video.file_unique_id = "video_uid"

    sender, mock_bot = _sender_with_bot(mocker, send_video=AsyncMock(return_value=msg))
    mocker.patch("app.worker.telegram_sender.FSInputFile")

    fid, uid, ftype = await sender._send_file_async(123, Path("video.mp4"), "caption")
    assert fid == "video_fid"
    assert ftype == TelegramFileType.video


@pytest.mark.asyncio
async def test_send_file_video_passes_dimension_hints(mocker):
    """Width/height/duration must reach Bot API so Telegram doesn't fall back
    to a square placeholder while it probes the file itself."""
    msg = MagicMock()
    msg.video.file_id = "video_fid"
    msg.video.file_unique_id = "video_uid"

    sender, mock_bot = _sender_with_bot(mocker, send_video=AsyncMock(return_value=msg))
    mocker.patch("app.worker.telegram_sender.FSInputFile")

    await sender._send_file_async(123, Path("video.mp4"), "caption", width=1080, height=1920, duration=42)

    _, kwargs = mock_bot.send_video.call_args
    assert kwargs["width"] == 1080
    assert kwargs["height"] == 1920
    assert kwargs["duration"] == 42


@pytest.mark.asyncio
async def test_send_file_video_omits_hints_when_missing(mocker):
    msg = MagicMock()
    msg.video.file_id = "video_fid"
    msg.video.file_unique_id = "video_uid"

    sender, mock_bot = _sender_with_bot(mocker, send_video=AsyncMock(return_value=msg))
    mocker.patch("app.worker.telegram_sender.FSInputFile")

    await sender._send_file_async(123, Path("video.mp4"), "caption")

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

    sender, mock_bot = _sender_with_bot(mocker, send_document=AsyncMock(return_value=msg))
    mocker.patch("app.worker.telegram_sender.FSInputFile")

    await sender._send_file_async(123, Path("file.pdf"), "cap", width=100, height=100, duration=5)

    _, kwargs = mock_bot.send_document.call_args
    assert "width" not in kwargs
    assert "height" not in kwargs
    assert "duration" not in kwargs


@pytest.mark.asyncio
async def test_send_file_audio(mocker):
    msg = MagicMock()
    msg.audio.file_id = "audio_fid"
    msg.audio.file_unique_id = "audio_uid"

    sender, mock_bot = _sender_with_bot(mocker, send_audio=AsyncMock(return_value=msg))
    mocker.patch("app.worker.telegram_sender.FSInputFile")

    fid, uid, ftype = await sender._send_file_async(123, Path("audio.mp3"), "cap")
    assert fid == "audio_fid"
    assert ftype == TelegramFileType.audio


@pytest.mark.asyncio
async def test_send_file_document(mocker):
    msg = MagicMock()
    msg.document.file_id = "doc_fid"
    msg.document.file_unique_id = "doc_uid"

    sender, mock_bot = _sender_with_bot(mocker, send_document=AsyncMock(return_value=msg))
    mocker.patch("app.worker.telegram_sender.FSInputFile")

    fid, uid, ftype = await sender._send_file_async(123, Path("file.pdf"), "cap")
    assert fid == "doc_fid"
    assert ftype == TelegramFileType.document


@pytest.mark.asyncio
async def test_send_cached_video(mocker):
    sender, mock_bot = _sender_with_bot(mocker, send_video=AsyncMock())
    await sender._send_cached_async(123, "fid", TelegramFileType.video, "cap")
    mock_bot.send_video.assert_awaited_once()


@pytest.mark.asyncio
async def test_send_cached_audio(mocker):
    sender, mock_bot = _sender_with_bot(mocker, send_audio=AsyncMock())
    await sender._send_cached_async(123, "fid", TelegramFileType.audio, "cap")
    mock_bot.send_audio.assert_awaited_once()


@pytest.mark.asyncio
async def test_send_cached_document(mocker):
    sender, mock_bot = _sender_with_bot(mocker, send_document=AsyncMock())
    await sender._send_cached_async(123, "fid", TelegramFileType.document, "cap")
    mock_bot.send_document.assert_awaited_once()


@pytest.mark.asyncio
async def test_edit_status_no_message_id(mocker):
    sender, mock_bot = _sender_with_bot(mocker)
    await sender._edit_status_async(123, None, "text")
    mock_bot.edit_message_text.assert_not_called()


@pytest.mark.asyncio
async def test_edit_status_success(mocker):
    sender, mock_bot = _sender_with_bot(mocker, edit_message_text=AsyncMock())
    await sender._edit_status_async(123, 456, "text")
    mock_bot.edit_message_text.assert_awaited_once_with(chat_id=123, message_id=456, text="text")


@pytest.mark.asyncio
async def test_edit_status_exception_swallowed(mocker):
    sender, mock_bot = _sender_with_bot(
        mocker, edit_message_text=AsyncMock(side_effect=Exception("Message not found"))
    )
    # Should not raise
    await sender._edit_status_async(123, 456, "text")


# ---------------------------------------------------------------------------
# Sync wrappers
# ---------------------------------------------------------------------------

def test_send_file_calls_run(mocker):
    sender = TelegramSender()
    mock_run = mocker.patch.object(sender, "_run", return_value=("fid", "uid", TelegramFileType.video))
    result = sender.send_file(1, Path("v.mp4"), "cap")
    assert result == ("fid", "uid", TelegramFileType.video)
    mock_run.assert_called_once()


def test_send_cached_calls_run(mocker):
    sender = TelegramSender()
    mock_run = mocker.patch.object(sender, "_run")
    sender.send_cached(1, "fid", TelegramFileType.video, "cap")
    mock_run.assert_called_once()


def test_edit_status_calls_run(mocker):
    sender = TelegramSender()
    mock_run = mocker.patch.object(sender, "_run")
    sender.edit_status(1, 2, "text")
    mock_run.assert_called_once()


# ---------------------------------------------------------------------------
# _run directly
# ---------------------------------------------------------------------------

def test_run_executes_coroutine_on_background_loop():
    """_run submits a coroutine to the background event loop and returns the result."""
    sender = TelegramSender()

    async def sample_coro():
        return "hello"

    result = sender._run(sample_coro(), timeout=5)
    assert result == "hello"


def test_run_propagates_exception():
    sender = TelegramSender()

    async def failing_coro():
        raise ValueError("from coro")

    with pytest.raises(ValueError, match="from coro"):
        sender._run(failing_coro(), timeout=5)


def test_run_uses_upload_timeout_by_default():
    """_run() with no explicit timeout falls back to the instance's upload_timeout."""
    sender = TelegramSender(upload_timeout=5)

    async def sample_coro():
        return "ok"

    assert sender._run(sample_coro()) == "ok"
