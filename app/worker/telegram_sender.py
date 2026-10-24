from __future__ import annotations

import asyncio
import logging
import re
import threading
import time
from collections.abc import Awaitable, Callable
from concurrent.futures import TimeoutError as FuturesTimeoutError
from dataclasses import dataclass
from pathlib import Path
from typing import Any, TypeVar

from aiogram import Bot
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.exceptions import TelegramBadRequest, TelegramRetryAfter
from aiogram.types import FSInputFile, ReplyParameters

from app.core.config import get_settings
from app.db.models import TelegramFileType
from app.utils.telegram_session import build_bot_session

logger = logging.getLogger(__name__)

VIDEO_EXTENSIONS = {".mp4", ".mov", ".m4v", ".webm", ".mkv"}
AUDIO_EXTENSIONS = {".m4a", ".mp3", ".ogg", ".opus", ".wav"}

_UPLOAD_TIMEOUT = 600

_T = TypeVar("_T")
_MAX_FLOOD_RETRIES = 3
_RETRY_AFTER_RE = re.compile(r"retry after (\d+)", re.IGNORECASE)

# Держим отправку заметно ниже официальных лимитов Telegram Bot API
# (~30 сообщений/сек глобально, ~1 сообщение/сек в один чат), чтобы не
# упираться в flood control реактивно, а просто не создавать его.
_GLOBAL_MIN_INTERVAL = 1 / 25
_CHAT_MIN_INTERVAL = 1.2


class _SendRateLimiter:
    """Проактивно выдерживает интервалы между отправками, чтобы не словить 429.

    Резервирует слот времени под блокировкой и уже потом спит вне неё, так
    что параллельные отправки из разных задач не накладываются друг на друга.
    """

    def __init__(self) -> None:
        self._lock = asyncio.Lock()
        self._next_global_slot = 0.0
        self._next_chat_slot: dict[int, float] = {}

    async def wait(self, chat_id: int) -> None:
        async with self._lock:
            now = time.monotonic()
            slot = max(
                now,
                self._next_global_slot,
                self._next_chat_slot.get(chat_id, 0.0),
            )
            self._next_global_slot = slot + _GLOBAL_MIN_INTERVAL
            self._next_chat_slot[chat_id] = slot + _CHAT_MIN_INTERVAL
            delay = slot - now
        if delay > 0:
            await asyncio.sleep(delay)


async def _call_with_flood_retry(call: Callable[[], Awaitable[_T]]) -> _T:
    """Await an aiogram bot call, retrying on Telegram flood control.

    aiogram only raises TelegramRetryAfter when the response's own
    `parameters.retry_after` field is set. The local Bot API server this
    project talks to (see docker-compose's telegram_api service) has been
    observed reporting flood control as a plain 400 Bad Request ("too Many
    Requests: retry after N") without that field, so aiogram raises
    TelegramBadRequest instead — which nothing upstream retries, silently
    dropping an already-downloaded file. Handle both shapes here.
    """
    for attempt in range(_MAX_FLOOD_RETRIES + 1):
        try:
            return await call()
        except (TelegramRetryAfter, TelegramBadRequest) as exc:
            retry_after = getattr(exc, "retry_after", None)
            if retry_after is None:
                match = _RETRY_AFTER_RE.search(str(exc))
                if not match:
                    raise
                retry_after = int(match.group(1))
            if attempt == _MAX_FLOOD_RETRIES:
                raise
            logger.warning(
                "Telegram flood control hit, retrying in %ss (attempt %d/%d)",
                retry_after, attempt + 1, _MAX_FLOOD_RETRIES,
            )
            await asyncio.sleep(retry_after + 1)
    raise AssertionError("unreachable")  # pragma: no cover


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


def _reply_kwargs(reply_to_message_id: int | None) -> dict[str, Any]:
    """Bot API kwargs to send as a reply (used for chat auto-download).

    allow_sending_without_reply keeps the video going out even if the
    original link message was deleted while the download was running.
    """
    if not reply_to_message_id:
        return {}
    return {"reply_parameters": ReplyParameters(message_id=reply_to_message_id, allow_sending_without_reply=True)}


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
        self._rate_limiter = _SendRateLimiter()

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
                settings = get_settings()
                token = self._token or settings.bot_token
                self._bot = Bot(
                    token=token,
                    session=build_bot_session(settings),
                    default=DefaultBotProperties(parse_mode=ParseMode.HTML),
                )
        return self._bot

    def _run(self, coro: Any, timeout: int | None = None) -> Any:
        loop = self._get_loop()
        future = asyncio.run_coroutine_threadsafe(coro, loop)
        try:
            return future.result(timeout=timeout if timeout is not None else self._upload_timeout)
        except FuturesTimeoutError:
            # future.result() timing out does NOT stop the coroutine running on
            # the background loop - without cancelling it explicitly it keeps
            # holding its socket/file handle until it finishes on its own.
            future.cancel()
            raise

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
        reply_to_message_id: int | None = None,
    ) -> tuple[str, str | None, TelegramFileType]:
        bot = self._get_bot()
        input_file = FSInputFile(file_path)
        file_type = _SUFFIX_TO_FILE_TYPE.get(file_path.suffix.lower(), TelegramFileType.document)
        spec = _TYPE_SPECS[file_type]

        await self._rate_limiter.wait(chat_id)
        msg = await _call_with_flood_retry(
            lambda: getattr(bot, spec.method)(
                chat_id=chat_id,
                **{spec.media_param: input_file},
                caption=caption,
                request_timeout=self._upload_timeout,
                **spec.extra_kwargs,
                **_media_hints(file_type, width, height, duration),
                **_reply_kwargs(reply_to_message_id),
            )
        )
        media = getattr(msg, spec.media_param)
        return media.file_id, media.file_unique_id, file_type

    async def _send_cached_async(
        self,
        chat_id: int,
        file_id: str,
        file_type: TelegramFileType,
        caption: str,
        reply_to_message_id: int | None = None,
    ) -> None:
        bot = self._get_bot()
        spec = _TYPE_SPECS[file_type]
        await self._rate_limiter.wait(chat_id)
        await _call_with_flood_retry(
            lambda: getattr(bot, spec.method)(
                chat_id=chat_id,
                **{spec.media_param: file_id},
                caption=caption,
                **_reply_kwargs(reply_to_message_id),
            )
        )

    async def _edit_status_async(self, chat_id: int, message_id: int | None, text: str) -> None:
        if not message_id:
            return
        bot = self._get_bot()
        try:
            await bot.edit_message_text(
                chat_id=chat_id, message_id=message_id, text=text, request_timeout=10
            )
        except Exception as exc:
            logger.debug("Could not edit status message %s in chat %s: %s", message_id, chat_id, exc)

    async def _delete_status_async(self, chat_id: int, message_id: int | None) -> None:
        if not message_id:
            return
        bot = self._get_bot()
        try:
            await bot.delete_message(chat_id=chat_id, message_id=message_id, request_timeout=10)
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
        reply_to_message_id: int | None = None,
    ) -> tuple[str, str | None, TelegramFileType]:
        return self._run(
            self._send_file_async(chat_id, file_path, caption, width, height, duration, reply_to_message_id)
        )

    def send_cached(
        self,
        chat_id: int,
        file_id: str,
        file_type: TelegramFileType,
        caption: str,
        reply_to_message_id: int | None = None,
    ) -> None:
        self._run(self._send_cached_async(chat_id, file_id, file_type, caption, reply_to_message_id))

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
