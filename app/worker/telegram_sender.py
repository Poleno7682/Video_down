from __future__ import annotations

import asyncio
import logging
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from aiogram import Bot
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.types import FSInputFile

from app.core.config import get_settings
from app.db.models import TelegramFileType

logger = logging.getLogger(__name__)

VIDEO_EXTENSIONS = {".mp4", ".mov", ".m4v", ".webm", ".mkv"}
AUDIO_EXTENSIONS = {".m4a", ".mp3", ".ogg", ".opus", ".wav"}

_UPLOAD_TIMEOUT = 600


@dataclass(frozen=True)
class _TypeSpec:
    method: str        # "send_video", "send_audio", "send_document"
    media_param: str   # параметр Bot API: "video", "audio", "document"
    extra_kwargs: dict[str, Any]


_TYPE_SPECS: dict[TelegramFileType, _TypeSpec] = {
    TelegramFileType.video: _TypeSpec("send_video", "video", {"supports_streaming": True}),
    TelegramFileType.audio: _TypeSpec("send_audio", "audio", {}),
    TelegramFileType.document: _TypeSpec("send_document", "document", {}),
}

# Maps file suffix → TelegramFileType; OCP: add new types by extending this dict.
_SUFFIX_TO_FILE_TYPE: dict[str, TelegramFileType] = {
    **{ext: TelegramFileType.video for ext in VIDEO_EXTENSIONS},
    **{ext: TelegramFileType.audio for ext in AUDIO_EXTENSIONS},
}


def _media_hints(
    file_type: TelegramFileType, width: int | None, height: int | None, duration: int | None
) -> dict[str, Any]:
    """Bot API kwargs so Telegram clients render the correct aspect ratio right
    away instead of falling back to a square placeholder while probing the file.
    """
    hints: dict[str, Any] = {}
    if file_type == TelegramFileType.video and width and height:
        hints["width"] = width
        hints["height"] = height
    if file_type in (TelegramFileType.video, TelegramFileType.audio) and duration:
        hints["duration"] = duration
    return hints


class TelegramSender:
    """Facade over aiogram's async Bot for use from synchronous Celery tasks.

    Each instance owns its own Bot and background event loop, so callers can
    inject a fake/mock implementation in tests instead of monkeypatching
    module-level globals. Production code obtains the process-wide instance
    via get_default_sender() rather than constructing one per call, since the
    underlying aiohttp session is expensive to set up and safe to reuse.
    """

    def __init__(self, token: str | None = None, upload_timeout: int = _UPLOAD_TIMEOUT) -> None:
        self._token = token
        self._upload_timeout = upload_timeout
        self._loop: asyncio.AbstractEventLoop | None = None
        self._loop_lock = threading.Lock()
        self._bot: Bot | None = None
        self._bot_lock = threading.Lock()

    def _get_loop(self) -> asyncio.AbstractEventLoop:
        with self._loop_lock:
            if self._loop is None or self._loop.is_closed():
                self._loop = asyncio.new_event_loop()
                t = threading.Thread(target=self._loop.run_forever, daemon=True)
                t.start()
        return self._loop

    def _get_bot(self) -> Bot:
        with self._bot_lock:
            if self._bot is None:
                token = self._token or get_settings().bot_token
                self._bot = Bot(
                    token=token,
                    default=DefaultBotProperties(parse_mode=ParseMode.HTML),
                )
        return self._bot

    def _run(self, coro: Any, timeout: int | None = None) -> Any:
        loop = self._get_loop()
        future = asyncio.run_coroutine_threadsafe(coro, loop)
        return future.result(timeout=timeout if timeout is not None else self._upload_timeout)

    def close_session(self) -> None:
        """Call from worker shutdown signal to cleanly close the aiohttp session."""
        with self._bot_lock:
            if self._bot is not None:
                try:
                    self._run(self._bot.session.close(), timeout=10)
                except Exception:
                    pass
                self._bot = None

    async def _send_file_async(
        self,
        chat_id: int,
        file_path: Path,
        caption: str,
        width: int | None = None,
        height: int | None = None,
        duration: int | None = None,
    ) -> tuple[str, str | None, TelegramFileType]:
        bot = self._get_bot()
        input_file = FSInputFile(file_path)
        file_type = _SUFFIX_TO_FILE_TYPE.get(file_path.suffix.lower(), TelegramFileType.document)
        spec = _TYPE_SPECS[file_type]

        msg = await getattr(bot, spec.method)(
            chat_id=chat_id,
            **{spec.media_param: input_file},
            caption=caption,
            request_timeout=self._upload_timeout,
            **spec.extra_kwargs,
            **_media_hints(file_type, width, height, duration),
        )
        media = getattr(msg, spec.media_param)
        return media.file_id, media.file_unique_id, file_type

    async def _send_cached_async(
        self, chat_id: int, file_id: str, file_type: TelegramFileType, caption: str
    ) -> None:
        bot = self._get_bot()
        spec = _TYPE_SPECS[file_type]
        await getattr(bot, spec.method)(
            chat_id=chat_id,
            **{spec.media_param: file_id},
            caption=caption,
        )

    async def _edit_status_async(self, chat_id: int, message_id: int | None, text: str) -> None:
        if not message_id:
            return
        bot = self._get_bot()
        try:
            await bot.edit_message_text(chat_id=chat_id, message_id=message_id, text=text)
        except Exception as exc:
            logger.debug("Could not edit status message %s in chat %s: %s", message_id, chat_id, exc)

    async def _delete_status_async(self, chat_id: int, message_id: int | None) -> None:
        if not message_id:
            return
        bot = self._get_bot()
        try:
            await bot.delete_message(chat_id=chat_id, message_id=message_id)
        except Exception as exc:
            logger.debug("Could not delete status message %s in chat %s: %s", message_id, chat_id, exc)

    def send_file(
        self,
        chat_id: int,
        file_path: Path,
        caption: str,
        width: int | None = None,
        height: int | None = None,
        duration: int | None = None,
    ) -> tuple[str, str | None, TelegramFileType]:
        return self._run(self._send_file_async(chat_id, file_path, caption, width, height, duration))

    def send_cached(self, chat_id: int, file_id: str, file_type: TelegramFileType, caption: str) -> None:
        self._run(self._send_cached_async(chat_id, file_id, file_type, caption))

    def edit_status(self, chat_id: int, message_id: int | None, text: str) -> None:
        self._run(self._edit_status_async(chat_id, message_id, text), timeout=15)

    def delete_status(self, chat_id: int, message_id: int | None) -> None:
        self._run(self._delete_status_async(chat_id, message_id), timeout=15)


_default_sender: TelegramSender | None = None
_default_sender_lock = threading.Lock()


def get_default_sender() -> TelegramSender:
    """Process-wide TelegramSender singleton, reusing one Bot/event loop across calls."""
    global _default_sender
    with _default_sender_lock:
        if _default_sender is None:
            _default_sender = TelegramSender()
    return _default_sender


def close_bot_session() -> None:
    """Call from worker shutdown signal to cleanly close the default sender's session."""
    global _default_sender
    with _default_sender_lock:
        sender = _default_sender
        _default_sender = None
    if sender is not None:
        sender.close_session()
