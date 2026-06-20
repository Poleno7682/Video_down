from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from aiogram.exceptions import TelegramBadRequest

from app.bot.router import (
    HELP_TEXT,
    _is_allowed,
    _check_access,
    send_cached_file,
    _process_url_message,
)
from app.db.models import DownloadStatus, TelegramFileType


# ---------------------------------------------------------------------------
# _is_allowed (backward-compat helper)
# ---------------------------------------------------------------------------

def test_is_allowed_empty_set_allows_all():
    assert _is_allowed(123, set()) is True


def test_is_allowed_user_in_set():
    assert _is_allowed(123, {123, 456}) is True


def test_is_allowed_user_not_in_set():
    assert _is_allowed(999, {123, 456}) is False


# ---------------------------------------------------------------------------
# _check_access
# ---------------------------------------------------------------------------

def _make_access_settings(**kwargs):
    s = MagicMock()
    s.admin_user_ids = set()
    s.allowed_user_ids = set()
    for k, v in kwargs.items():
        setattr(s, k, v)
    return s


def _make_redis(disabled=False, trusted_count=0, user_trusted=False):
    r = MagicMock()
    r.exists.return_value = 1 if disabled else 0
    r.scard.return_value = trusted_count
    r.sismember.return_value = user_trusted
    return r


def test_check_access_admin_always_allowed():
    s = _make_access_settings(admin_user_ids={99})
    redis = _make_redis(disabled=True)
    allowed, _ = _check_access(99, s, redis)
    assert allowed is True


def test_check_access_bot_disabled_denies_non_admin():
    s = _make_access_settings()
    redis = _make_redis(disabled=True)
    allowed, msg = _check_access(1, s, redis)
    assert allowed is False
    assert "недоступен" in msg.lower() or "🔴" in msg


def test_check_access_static_whitelist_allows():
    s = _make_access_settings(allowed_user_ids={1, 2})
    redis = _make_redis()
    allowed, _ = _check_access(1, s, redis)
    assert allowed is True


def test_check_access_static_whitelist_denies():
    s = _make_access_settings(allowed_user_ids={1, 2})
    redis = _make_redis()
    allowed, _ = _check_access(99, s, redis)
    assert allowed is False


def test_check_access_trusted_list_allows():
    s = _make_access_settings()
    redis = _make_redis(trusted_count=3, user_trusted=True)
    allowed, _ = _check_access(1, s, redis)
    assert allowed is True


def test_check_access_trusted_list_denies():
    s = _make_access_settings()
    redis = _make_redis(trusted_count=3, user_trusted=False)
    allowed, _ = _check_access(99, s, redis)
    assert allowed is False


def test_check_access_public_bot():
    s = _make_access_settings()
    redis = _make_redis(trusted_count=0)
    allowed, _ = _check_access(999, s, redis)
    assert allowed is True


# ---------------------------------------------------------------------------
# send_cached_file
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_send_cached_file_video():
    message = MagicMock()
    message.answer_video = AsyncMock()
    await send_cached_file(message, "fid", "video")
    message.answer_video.assert_awaited_once_with("fid", caption="⚡ Готово из Telegram-кэша")


@pytest.mark.asyncio
async def test_send_cached_file_audio():
    message = MagicMock()
    message.answer_audio = AsyncMock()
    await send_cached_file(message, "fid", "audio")
    message.answer_audio.assert_awaited_once_with("fid", caption="⚡ Готово из Telegram-кэша")


@pytest.mark.asyncio
async def test_send_cached_file_document():
    message = MagicMock()
    message.answer_document = AsyncMock()
    await send_cached_file(message, "fid", "document")
    message.answer_document.assert_awaited_once_with("fid", caption="⚡ Готово из Telegram-кэша")


# ---------------------------------------------------------------------------
# Helpers for handler tests
# ---------------------------------------------------------------------------

def _make_message(user_id=1, username="user", first_name="User",
                  text="https://youtube.com/watch?v=abc", chat_id=1, message_id=10):
    msg = MagicMock()
    msg.from_user.id = user_id
    msg.from_user.username = username
    msg.from_user.first_name = first_name
    msg.text = text
    msg.caption = None
    msg.chat.id = chat_id
    msg.message_id = message_id
    msg.answer = AsyncMock()
    msg.answer_video = AsyncMock()
    msg.answer_audio = AsyncMock()
    msg.answer_document = AsyncMock()
    return msg


def _make_settings(**kwargs):
    s = MagicMock()
    s.admin_user_ids = set()
    s.allowed_user_ids = set()
    s.rate_limit_window_seconds = 60
    s.rate_limit_max_messages = 20
    s.ban_seconds = 300
    s.user_daily_limit = 50
    s.user_queue_limit = 3
    s.global_queue_limit = 20
    s.default_quality = "720p"
    s.cache_ttl_hours = 24
    s.webhook_url = "https://example.com/bot"
    s.webhook_secret = "secret"
    for k, v in kwargs.items():
        setattr(s, k, v)
    return s


def _make_redis_mock(disabled=False, trusted_count=0, user_trusted=False):
    r = MagicMock()
    r.exists.return_value = 1 if disabled else 0
    r.scard.return_value = trusted_count
    r.sismember.return_value = user_trusted
    r.get.return_value = None
    r.setex = MagicMock()
    r.sadd = MagicMock()
    r.srem = MagicMock()
    r.smembers = MagicMock(return_value=set())
    r.set = MagicMock()
    r.delete = MagicMock()
    return r


def _make_session(repo):
    session = MagicMock()
    session.__enter__ = MagicMock(return_value=session)
    session.__exit__ = MagicMock(return_value=False)
    return session


# ---------------------------------------------------------------------------
# start handler
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_start_handler():
    from app.bot.router import start
    message = _make_message()
    await start(message)
    message.answer.assert_awaited_once_with(HELP_TEXT)


# ---------------------------------------------------------------------------
# quality handler
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_quality_handler():
    from app.bot.router import quality
    message = _make_message()
    await quality(message)
    message.answer.assert_awaited_once()
    assert "reply_markup" in message.answer.call_args[1]


# ---------------------------------------------------------------------------
# set_quality callback handler
# ---------------------------------------------------------------------------

def _make_callback(data="quality:720p", user_id=1):
    cb = MagicMock()
    cb.from_user.id = user_id
    cb.data = data
    cb.answer = AsyncMock()
    cb.message = MagicMock()
    cb.message.edit_text = AsyncMock()
    return cb


@pytest.mark.asyncio
async def test_set_quality_allowed():
    from app.bot.router import set_quality
    cb = _make_callback(data="quality:720p")
    settings = _make_settings()
    redis = _make_redis_mock()

    limiter = MagicMock()
    limiter.hit_or_ban.return_value = (True, 0)

    with patch("app.bot.router.get_settings", return_value=settings), \
         patch("app.bot.router.get_redis", return_value=redis), \
         patch("app.bot.router.RateLimiter", return_value=limiter):
        await set_quality(cb)

    cb.answer.assert_awaited_once_with("Сохранено")
    cb.message.edit_text.assert_awaited_once()
    redis.setex.assert_called_once()


@pytest.mark.asyncio
async def test_set_quality_rate_limited():
    from app.bot.router import set_quality
    cb = _make_callback()
    settings = _make_settings()
    redis = _make_redis_mock()

    limiter = MagicMock()
    limiter.hit_or_ban.return_value = (False, 120)

    with patch("app.bot.router.get_settings", return_value=settings), \
         patch("app.bot.router.get_redis", return_value=redis), \
         patch("app.bot.router.RateLimiter", return_value=limiter):
        await set_quality(cb)

    assert "120" in cb.answer.call_args[0][0]
    cb.message.edit_text.assert_not_awaited()


@pytest.mark.asyncio
async def test_set_quality_access_denied():
    from app.bot.router import set_quality
    cb = _make_callback()
    settings = _make_settings()
    redis = _make_redis_mock(disabled=True)

    with patch("app.bot.router.get_settings", return_value=settings), \
         patch("app.bot.router.get_redis", return_value=redis):
        await set_quality(cb)

    cb.answer.assert_awaited_once()
    assert cb.answer.call_args[1].get("show_alert") is True
    cb.message.edit_text.assert_not_awaited()


# ---------------------------------------------------------------------------
# status handler
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_status_handler():
    from app.bot.router import status
    message = _make_message()
    settings = _make_settings()
    redis = _make_redis_mock()

    repo = MagicMock()
    repo.count_user_active_requests.return_value = 1
    repo.count_global_active_requests.return_value = 5
    repo.count_user_today_requests.return_value = 10
    session = _make_session(repo)

    with patch("app.bot.router.get_settings", return_value=settings), \
         patch("app.bot.router.get_redis", return_value=redis), \
         patch("app.bot.router.get_session", return_value=session), \
         patch("app.bot.router.Repository", return_value=repo):
        await status(message)

    message.answer.assert_awaited_once()


@pytest.mark.asyncio
async def test_status_handler_access_denied():
    from app.bot.router import status
    message = _make_message()
    settings = _make_settings()
    redis = _make_redis_mock(disabled=True)

    with patch("app.bot.router.get_settings", return_value=settings), \
         patch("app.bot.router.get_redis", return_value=redis):
        await status(message)

    message.answer.assert_awaited_once()
    assert "недоступен" in message.answer.call_args[0][0].lower() or \
           "🔴" in message.answer.call_args[0][0]


# ---------------------------------------------------------------------------
# handle_link / handle_caption_link handlers
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_handle_link_calls_process_url_message():
    from app.bot.router import handle_link
    message = _make_message(text="hello")
    with patch("app.bot.router._process_url_message", new=AsyncMock()) as mock_proc:
        await handle_link(message)
    mock_proc.assert_awaited_once_with(message, "hello", reply_on_no_url=True)


@pytest.mark.asyncio
async def test_handle_link_empty_text():
    from app.bot.router import handle_link
    message = _make_message()
    message.text = None
    with patch("app.bot.router._process_url_message", new=AsyncMock()) as mock_proc:
        await handle_link(message)
    mock_proc.assert_awaited_once_with(message, "", reply_on_no_url=True)


@pytest.mark.asyncio
async def test_handle_caption_link_calls_process_url_message():
    from app.bot.router import handle_caption_link
    message = _make_message()
    message.caption = "Check this https://youtube.com/watch?v=x"
    with patch("app.bot.router._process_url_message", new=AsyncMock()) as mock_proc:
        await handle_caption_link(message)
    mock_proc.assert_awaited_once_with(
        message, "Check this https://youtube.com/watch?v=x", reply_on_no_url=False
    )


# ---------------------------------------------------------------------------
# Admin handlers
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_admin_panel_non_admin_ignored():
    from app.bot.router import admin_panel
    message = _make_message(user_id=999)
    settings = _make_settings(admin_user_ids=set())
    with patch("app.bot.router.get_settings", return_value=settings):
        await admin_panel(message)
    message.answer.assert_not_awaited()


@pytest.mark.asyncio
async def test_admin_panel_shown_to_admin():
    from app.bot.router import admin_panel
    message = _make_message(user_id=42)
    settings = _make_settings(admin_user_ids={42})
    redis = _make_redis_mock()
    with patch("app.bot.router.get_settings", return_value=settings), \
         patch("app.bot.router.get_redis", return_value=redis):
        await admin_panel(message)
    message.answer.assert_awaited_once()
    assert "reply_markup" in message.answer.call_args[1]


@pytest.mark.asyncio
async def test_toggle_bot_access_non_admin():
    from app.bot.router import toggle_bot_access
    cb = MagicMock()
    cb.from_user.id = 999
    cb.answer = AsyncMock()
    settings = _make_settings(admin_user_ids=set())
    with patch("app.bot.router.get_settings", return_value=settings):
        await toggle_bot_access(cb)
    cb.answer.assert_awaited_once()
    assert "⛔" in cb.answer.call_args[0][0]


@pytest.mark.asyncio
async def test_toggle_bot_access_enables():
    from app.bot.router import toggle_bot_access
    cb = MagicMock()
    cb.from_user.id = 1
    cb.answer = AsyncMock()
    cb.message = MagicMock()
    cb.message.edit_text = AsyncMock()
    settings = _make_settings(admin_user_ids={1})
    redis = _make_redis_mock(disabled=True)  # currently disabled

    with patch("app.bot.router.get_settings", return_value=settings), \
         patch("app.bot.router.get_redis", return_value=redis):
        await toggle_bot_access(cb)

    redis.delete.assert_called_once()   # enabled → delete key
    cb.answer.assert_awaited_once()
    assert "🟢" in cb.answer.call_args[0][0]


@pytest.mark.asyncio
async def test_toggle_bot_access_disables():
    from app.bot.router import toggle_bot_access
    cb = MagicMock()
    cb.from_user.id = 1
    cb.answer = AsyncMock()
    cb.message = MagicMock()
    cb.message.edit_text = AsyncMock()
    settings = _make_settings(admin_user_ids={1})
    redis = _make_redis_mock(disabled=False)  # currently enabled

    with patch("app.bot.router.get_settings", return_value=settings), \
         patch("app.bot.router.get_redis", return_value=redis):
        await toggle_bot_access(cb)

    redis.set.assert_called()   # disabled → set key
    assert "🔴" in cb.answer.call_args[0][0]


@pytest.mark.asyncio
async def test_add_trusted_user_success():
    from app.bot.router import add_trusted_user
    message = _make_message(user_id=1, text="/adduser 123456")
    settings = _make_settings(admin_user_ids={1})
    redis = _make_redis_mock()
    with patch("app.bot.router.get_settings", return_value=settings), \
         patch("app.bot.router.get_redis", return_value=redis):
        await add_trusted_user(message)
    redis.sadd.assert_called_once_with("trusted_users", "123456")
    assert "✅" in message.answer.call_args[0][0]


@pytest.mark.asyncio
async def test_add_trusted_user_bad_id():
    from app.bot.router import add_trusted_user
    message = _make_message(user_id=1, text="/adduser notanid")
    settings = _make_settings(admin_user_ids={1})
    redis = _make_redis_mock()
    with patch("app.bot.router.get_settings", return_value=settings), \
         patch("app.bot.router.get_redis", return_value=redis):
        await add_trusted_user(message)
    redis.sadd.assert_not_called()


@pytest.mark.asyncio
async def test_add_trusted_user_non_admin_ignored():
    from app.bot.router import add_trusted_user
    message = _make_message(user_id=999, text="/adduser 111")
    settings = _make_settings(admin_user_ids=set())
    with patch("app.bot.router.get_settings", return_value=settings):
        await add_trusted_user(message)
    message.answer.assert_not_awaited()


@pytest.mark.asyncio
async def test_remove_trusted_user_found():
    from app.bot.router import remove_trusted_user
    message = _make_message(user_id=1, text="/removeuser 777")
    settings = _make_settings(admin_user_ids={1})
    redis = _make_redis_mock()
    redis.srem.return_value = 1
    with patch("app.bot.router.get_settings", return_value=settings), \
         patch("app.bot.router.get_redis", return_value=redis):
        await remove_trusted_user(message)
    assert "✅" in message.answer.call_args[0][0]


@pytest.mark.asyncio
async def test_remove_trusted_user_not_found():
    from app.bot.router import remove_trusted_user
    message = _make_message(user_id=1, text="/removeuser 777")
    settings = _make_settings(admin_user_ids={1})
    redis = _make_redis_mock()
    redis.srem.return_value = 0
    with patch("app.bot.router.get_settings", return_value=settings), \
         patch("app.bot.router.get_redis", return_value=redis):
        await remove_trusted_user(message)
    assert "⚠️" in message.answer.call_args[0][0]


@pytest.mark.asyncio
async def test_list_trusted_users_empty():
    from app.bot.router import list_trusted_users
    message = _make_message(user_id=1)
    settings = _make_settings(admin_user_ids={1})
    redis = _make_redis_mock()
    redis.smembers.return_value = set()
    with patch("app.bot.router.get_settings", return_value=settings), \
         patch("app.bot.router.get_redis", return_value=redis):
        await list_trusted_users(message)
    assert "пуст" in message.answer.call_args[0][0].lower()


@pytest.mark.asyncio
async def test_list_trusted_users_with_entries():
    from app.bot.router import list_trusted_users
    message = _make_message(user_id=1)
    settings = _make_settings(admin_user_ids={1})
    redis = _make_redis_mock()
    redis.smembers.return_value = {"111", "222", "333"}
    with patch("app.bot.router.get_settings", return_value=settings), \
         patch("app.bot.router.get_redis", return_value=redis):
        await list_trusted_users(message)
    text = message.answer.call_args[0][0]
    assert "111" in text


# ---------------------------------------------------------------------------
# _process_url_message — all branches
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_process_url_message_bot_disabled():
    message = _make_message(user_id=999)
    settings = _make_settings()
    redis = _make_redis_mock(disabled=True)

    with patch("app.bot.router.get_settings", return_value=settings), \
         patch("app.bot.router.get_redis", return_value=redis), \
         patch("app.bot.router.RateLimiter"):
        await _process_url_message(message, "https://youtube.com/watch?v=x", True)

    message.answer.assert_awaited_once()
    assert "🔴" in message.answer.call_args[0][0] or \
           "недоступен" in message.answer.call_args[0][0].lower()


@pytest.mark.asyncio
async def test_process_url_message_not_allowed():
    message = _make_message(user_id=999)
    settings = _make_settings(allowed_user_ids={1, 2, 3})
    redis = _make_redis_mock()

    with patch("app.bot.router.get_settings", return_value=settings), \
         patch("app.bot.router.get_redis", return_value=redis), \
         patch("app.bot.router.RateLimiter"):
        await _process_url_message(message, "https://youtube.com/watch?v=x", True)

    message.answer.assert_awaited_once()
    assert "⛔" in message.answer.call_args[0][0]


@pytest.mark.asyncio
async def test_process_url_message_rate_limited():
    message = _make_message()
    settings = _make_settings()
    redis = _make_redis_mock()

    limiter = MagicMock()
    limiter.hit_or_ban.return_value = (False, 300)

    with patch("app.bot.router.get_settings", return_value=settings), \
         patch("app.bot.router.get_redis", return_value=redis), \
         patch("app.bot.router.RateLimiter", return_value=limiter):
        await _process_url_message(message, "https://youtube.com/watch?v=x", True)

    assert "300" in message.answer.call_args[0][0]


@pytest.mark.asyncio
async def test_process_url_message_no_url_reply():
    message = _make_message()
    settings = _make_settings()
    redis = _make_redis_mock()

    limiter = MagicMock()
    limiter.hit_or_ban.return_value = (True, 0)

    with patch("app.bot.router.get_settings", return_value=settings), \
         patch("app.bot.router.get_redis", return_value=redis), \
         patch("app.bot.router.RateLimiter", return_value=limiter):
        await _process_url_message(message, "just some text", reply_on_no_url=True)

    message.answer.assert_awaited_once()


@pytest.mark.asyncio
async def test_process_url_message_no_url_no_reply():
    message = _make_message()
    settings = _make_settings()
    redis = _make_redis_mock()

    limiter = MagicMock()
    limiter.hit_or_ban.return_value = (True, 0)

    with patch("app.bot.router.get_settings", return_value=settings), \
         patch("app.bot.router.get_redis", return_value=redis), \
         patch("app.bot.router.RateLimiter", return_value=limiter):
        await _process_url_message(message, "just some text", reply_on_no_url=False)

    message.answer.assert_not_awaited()


@pytest.mark.asyncio
async def test_process_url_message_invalid_url():
    message = _make_message()
    settings = _make_settings()
    redis = _make_redis_mock()

    limiter = MagicMock()
    limiter.hit_or_ban.return_value = (True, 0)

    with patch("app.bot.router.get_settings", return_value=settings), \
         patch("app.bot.router.get_redis", return_value=redis), \
         patch("app.bot.router.RateLimiter", return_value=limiter), \
         patch("app.bot.router.extract_url", return_value="not-a-url"), \
         patch("app.bot.router.is_valid_url", return_value=False):
        await _process_url_message(message, "not-a-url", True)

    assert "некорректно" in message.answer.call_args[0][0].lower()


@pytest.mark.asyncio
async def test_process_url_message_daily_limit():
    message = _make_message()
    settings = _make_settings(user_daily_limit=10)
    redis = _make_redis_mock()

    limiter = MagicMock()
    limiter.hit_or_ban.return_value = (True, 0)

    repo = MagicMock()
    repo.upsert_user.return_value = MagicMock()
    repo.count_user_today_requests.return_value = 10
    repo.count_user_active_requests.return_value = 0
    repo.count_global_active_requests.return_value = 0
    session = _make_session(repo)

    with patch("app.bot.router.get_settings", return_value=settings), \
         patch("app.bot.router.get_redis", return_value=redis), \
         patch("app.bot.router.RateLimiter", return_value=limiter), \
         patch("app.bot.router.get_session", return_value=session), \
         patch("app.bot.router.Repository", return_value=repo):
        await _process_url_message(message, "https://youtube.com/watch?v=x", True)

    assert "лимит" in message.answer.call_args[0][0].lower() or "⚠️" in message.answer.call_args[0][0]


@pytest.mark.asyncio
async def test_process_url_message_user_queue_limit():
    message = _make_message()
    settings = _make_settings(user_queue_limit=3)
    redis = _make_redis_mock()

    limiter = MagicMock()
    limiter.hit_or_ban.return_value = (True, 0)

    repo = MagicMock()
    repo.upsert_user.return_value = MagicMock()
    repo.count_user_today_requests.return_value = 0
    repo.count_user_active_requests.return_value = 3
    repo.count_global_active_requests.return_value = 0
    session = _make_session(repo)

    with patch("app.bot.router.get_settings", return_value=settings), \
         patch("app.bot.router.get_redis", return_value=redis), \
         patch("app.bot.router.RateLimiter", return_value=limiter), \
         patch("app.bot.router.get_session", return_value=session), \
         patch("app.bot.router.Repository", return_value=repo):
        await _process_url_message(message, "https://youtube.com/watch?v=x", True)

    assert "⚠️" in message.answer.call_args[0][0]


@pytest.mark.asyncio
async def test_process_url_message_global_queue_limit():
    message = _make_message()
    settings = _make_settings(global_queue_limit=20)
    redis = _make_redis_mock()

    limiter = MagicMock()
    limiter.hit_or_ban.return_value = (True, 0)

    repo = MagicMock()
    repo.upsert_user.return_value = MagicMock()
    repo.count_user_today_requests.return_value = 0
    repo.count_user_active_requests.return_value = 0
    repo.count_global_active_requests.return_value = 20
    session = _make_session(repo)

    with patch("app.bot.router.get_settings", return_value=settings), \
         patch("app.bot.router.get_redis", return_value=redis), \
         patch("app.bot.router.RateLimiter", return_value=limiter), \
         patch("app.bot.router.get_session", return_value=session), \
         patch("app.bot.router.Repository", return_value=repo):
        await _process_url_message(message, "https://youtube.com/watch?v=x", True)

    assert "⚠️" in message.answer.call_args[0][0]


@pytest.mark.asyncio
async def test_process_url_message_cache_hit_success():
    message = _make_message()
    status_msg = MagicMock()
    status_msg.message_id = 99
    message.answer = AsyncMock(return_value=status_msg)
    settings = _make_settings()
    redis = _make_redis_mock()

    file_type_mock = MagicMock()
    file_type_mock.value = "video"

    ready_video = MagicMock()
    ready_video.telegram_file_id = "cached_fid"
    ready_video.telegram_file_type = file_type_mock

    repo = MagicMock()
    repo.upsert_user.return_value = MagicMock()
    repo.count_user_today_requests.return_value = 0
    repo.count_user_active_requests.return_value = 0
    repo.count_global_active_requests.return_value = 0
    repo.get_ready_video.return_value = ready_video

    session = _make_session(repo)
    limiter = MagicMock()
    limiter.hit_or_ban.return_value = (True, 0)

    with patch("app.bot.router.get_settings", return_value=settings), \
         patch("app.bot.router.get_redis", return_value=redis), \
         patch("app.bot.router.RateLimiter", return_value=limiter), \
         patch("app.bot.router.get_session", return_value=session), \
         patch("app.bot.router.Repository", return_value=repo), \
         patch("app.bot.router.send_cached_file", new=AsyncMock()) as mock_send:
        await _process_url_message(message, "https://youtube.com/watch?v=x", True)

    mock_send.assert_awaited_once()
    repo.get_or_create_video.assert_not_called()


@pytest.mark.asyncio
async def test_process_url_message_cache_hit_telegram_bad_request():
    message = _make_message()
    status_msg = MagicMock()
    status_msg.message_id = 99
    message.answer = AsyncMock(return_value=status_msg)
    settings = _make_settings()
    redis = _make_redis_mock()

    file_type_mock = MagicMock()
    file_type_mock.value = "video"

    ready_video = MagicMock()
    ready_video.id = 7
    ready_video.telegram_file_id = "stale_fid"
    ready_video.telegram_file_type = file_type_mock

    video = MagicMock()
    video.id = 7
    req = MagicMock()
    req.id = 1

    repo = MagicMock()
    repo.upsert_user.return_value = MagicMock()
    repo.count_user_today_requests.return_value = 0
    repo.count_user_active_requests.return_value = 0
    repo.count_global_active_requests.return_value = 0
    repo.get_ready_video.return_value = ready_video
    repo.get_or_create_video.return_value = video
    repo.create_request.return_value = req

    session = _make_session(repo)
    limiter = MagicMock()
    limiter.hit_or_ban.return_value = (True, 0)
    task = MagicMock()
    task.id = "task-uuid"

    with patch("app.bot.router.get_settings", return_value=settings), \
         patch("app.bot.router.get_redis", return_value=redis), \
         patch("app.bot.router.RateLimiter", return_value=limiter), \
         patch("app.bot.router.get_session", return_value=session), \
         patch("app.bot.router.Repository", return_value=repo), \
         patch("app.bot.router.send_cached_file", new=AsyncMock(
             side_effect=TelegramBadRequest(method=MagicMock(), message="Bad file id")
         )), \
         patch("app.bot.router.process_download_request") as mock_task:
        mock_task.delay.return_value = task
        await _process_url_message(message, "https://youtube.com/watch?v=x", True)

    repo.invalidate_video_cache.assert_called_once_with(ready_video.id)
    mock_task.delay.assert_called_once()


@pytest.mark.asyncio
async def test_process_url_message_normal_queue_path():
    message = _make_message()
    status_msg = MagicMock()
    status_msg.message_id = 99
    message.answer = AsyncMock(return_value=status_msg)
    settings = _make_settings()
    redis = _make_redis_mock()

    video = MagicMock()
    video.id = 5
    req = MagicMock()
    req.id = 1

    repo = MagicMock()
    repo.upsert_user.return_value = MagicMock()
    repo.count_user_today_requests.return_value = 0
    repo.count_user_active_requests.return_value = 0
    repo.count_global_active_requests.return_value = 0
    repo.get_ready_video.return_value = None
    repo.get_or_create_video.return_value = video
    repo.create_request.return_value = req

    session = _make_session(repo)
    limiter = MagicMock()
    limiter.hit_or_ban.return_value = (True, 0)
    task = MagicMock()
    task.id = "task-uuid"

    with patch("app.bot.router.get_settings", return_value=settings), \
         patch("app.bot.router.get_redis", return_value=redis), \
         patch("app.bot.router.RateLimiter", return_value=limiter), \
         patch("app.bot.router.get_session", return_value=session), \
         patch("app.bot.router.Repository", return_value=repo), \
         patch("app.bot.router.process_download_request") as mock_task:
        mock_task.delay.return_value = task
        await _process_url_message(message, "https://youtube.com/watch?v=x", True)

    repo.create_request.assert_called_once()
    mock_task.delay.assert_called_once_with(req.id)
    repo.set_request_task_id.assert_called_once_with(req.id, task.id)


@pytest.mark.asyncio
async def test_process_url_message_uses_user_quality_preference():
    message = _make_message()
    status_msg = MagicMock()
    status_msg.message_id = 99
    message.answer = AsyncMock(return_value=status_msg)
    settings = _make_settings(default_quality="720p")
    redis = _make_redis_mock()
    redis.get.return_value = "1080p"

    video = MagicMock()
    video.id = 1
    req = MagicMock()
    req.id = 1

    repo = MagicMock()
    repo.upsert_user.return_value = MagicMock()
    repo.count_user_today_requests.return_value = 0
    repo.count_user_active_requests.return_value = 0
    repo.count_global_active_requests.return_value = 0
    repo.get_ready_video.return_value = None
    repo.get_or_create_video.return_value = video
    repo.create_request.return_value = req

    session = _make_session(repo)
    limiter = MagicMock()
    limiter.hit_or_ban.return_value = (True, 0)
    task = MagicMock()
    task.id = "t"

    with patch("app.bot.router.get_settings", return_value=settings), \
         patch("app.bot.router.get_redis", return_value=redis), \
         patch("app.bot.router.RateLimiter", return_value=limiter), \
         patch("app.bot.router.get_session", return_value=session), \
         patch("app.bot.router.Repository", return_value=repo), \
         patch("app.bot.router.process_download_request") as mock_task:
        mock_task.delay.return_value = task
        await _process_url_message(message, "https://youtube.com/watch?v=x", True)

    assert repo.create_request.call_args[1]["quality"] == "1080p"


# ---------------------------------------------------------------------------
# Coverage gap tests — router.py lines 93, 95, 220-221, 247, 251-255, 269
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_admin_panel_text_static_whitelist_mode():
    """_admin_panel_text with non-empty allowed_user_ids → line 93."""
    from app.bot.router import admin_panel
    message = _make_message(user_id=42)
    settings = _make_settings(admin_user_ids={42}, allowed_user_ids={10, 20})
    redis = _make_redis_mock()
    with patch("app.bot.router.get_settings", return_value=settings), \
         patch("app.bot.router.get_redis", return_value=redis):
        await admin_panel(message)
    text = message.answer.call_args[0][0]
    assert "Статичный список" in text


@pytest.mark.asyncio
async def test_admin_panel_text_trusted_users_mode():
    """_admin_panel_text with trusted_count > 0 and no static list → line 95."""
    from app.bot.router import admin_panel
    message = _make_message(user_id=42)
    settings = _make_settings(admin_user_ids={42}, allowed_user_ids=set())
    redis = _make_redis_mock(trusted_count=5)
    with patch("app.bot.router.get_settings", return_value=settings), \
         patch("app.bot.router.get_redis", return_value=redis):
        await admin_panel(message)
    text = message.answer.call_args[0][0]
    assert "Доверенные пользователи" in text


@pytest.mark.asyncio
async def test_toggle_bot_access_swallows_telegram_bad_request():
    """edit_text raising TelegramBadRequest is silently swallowed → lines 220-221."""
    from app.bot.router import toggle_bot_access
    cb = MagicMock()
    cb.from_user.id = 1
    cb.answer = AsyncMock()
    cb.message = MagicMock()
    cb.message.edit_text = AsyncMock(
        side_effect=TelegramBadRequest(method=MagicMock(), message="message is not modified")
    )
    settings = _make_settings(admin_user_ids={1})
    redis = _make_redis_mock(disabled=False)

    with patch("app.bot.router.get_settings", return_value=settings), \
         patch("app.bot.router.get_redis", return_value=redis):
        await toggle_bot_access(cb)  # must not raise

    cb.answer.assert_awaited_once()


@pytest.mark.asyncio
async def test_remove_trusted_user_non_admin_ignored():
    """Non-admin calling /removeuser returns silently → line 247."""
    from app.bot.router import remove_trusted_user
    message = _make_message(user_id=999, text="/removeuser 111")
    settings = _make_settings(admin_user_ids=set())
    with patch("app.bot.router.get_settings", return_value=settings):
        await remove_trusted_user(message)
    message.answer.assert_not_awaited()


@pytest.mark.asyncio
async def test_remove_trusted_user_bad_id_shows_usage():
    """Admin sends /removeuser with non-numeric arg → usage message → lines 251-255."""
    from app.bot.router import remove_trusted_user
    message = _make_message(user_id=1, text="/removeuser notanid")
    settings = _make_settings(admin_user_ids={1})
    redis = _make_redis_mock()
    with patch("app.bot.router.get_settings", return_value=settings), \
         patch("app.bot.router.get_redis", return_value=redis):
        await remove_trusted_user(message)
    message.answer.assert_awaited_once()
    assert "Использование" in message.answer.call_args[0][0]
    redis.srem.assert_not_called()


@pytest.mark.asyncio
async def test_list_trusted_users_non_admin_ignored():
    """Non-admin calling /listusers returns silently → line 269."""
    from app.bot.router import list_trusted_users
    message = _make_message(user_id=999)
    settings = _make_settings(admin_user_ids=set())
    with patch("app.bot.router.get_settings", return_value=settings):
        await list_trusted_users(message)
    message.answer.assert_not_awaited()
