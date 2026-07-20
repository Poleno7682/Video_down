from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.bot.routers.rezka_flow import (
    rezka_back_to_seasons,
    rezka_back_to_translators,
    rezka_episode_chosen,
    rezka_season_all_chosen,
    rezka_season_chosen,
    rezka_translator_chosen,
    start_rezka_flow,
)
from app.utils.rezka import RezkaContentInfo, RezkaResolveError


def _make_message(user_id=1, chat_id=1, message_id=10):
    msg = MagicMock()
    msg.from_user.id = user_id
    msg.chat.id = chat_id
    msg.message_id = message_id
    status_msg = MagicMock()
    status_msg.edit_text = AsyncMock()
    msg.answer = AsyncMock(return_value=status_msg)
    return msg, status_msg


def _make_callback(user_id=1, data="rezka:tr:56"):
    callback = MagicMock()
    callback.from_user.id = user_id
    callback.data = data
    callback.answer = AsyncMock()
    callback.message = MagicMock()
    callback.message.edit_text = AsyncMock()
    return callback


def _make_settings():
    s = MagicMock()
    s.rezka_antibot_bypass = False
    return s


# ---------------------------------------------------------------------------
# start_rezka_flow
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_start_rezka_flow_movie_single_translator_auto_selects():
    message, status_msg = _make_message()
    info = RezkaContentInfo(title="A Movie", is_series=False, translators={56: "Дубляж"})
    redis = MagicMock()
    session = {
        "raw_url": "https://rezka.ag/films/x/1-y.html",
        "url": "https://rezka.ag/films/x/1-y.html",
        "quality": "720p",
        "is_series": False,
        "chat_id": 1,
        "message_id": 10,
    }

    with patch("app.bot.routers.rezka_flow.get_settings", return_value=_make_settings()), \
         patch("app.bot.routers.rezka_flow.get_redis", return_value=redis), \
         patch("app.bot.routers.rezka_flow.get_rezka_content_info", return_value=info), \
         patch("app.bot.routers.rezka_flow.get_rezka_session", return_value=session), \
         patch("app.bot.routers.rezka_flow.enqueue_download", new=AsyncMock()) as mock_enqueue:
        await start_rezka_flow(message, "https://rezka.ag/films/x/1-y.html", "https://rezka.ag/films/x/1-y.html", "720p")

    mock_enqueue.assert_awaited_once()
    call_args = mock_enqueue.call_args[0]
    assert call_args[5] == "https://rezka.ag/films/x/1-y.html?rezka_tr=56"
    status_msg.edit_text.assert_any_call("⏳ Добавляю в очередь...")


@pytest.mark.asyncio
async def test_start_rezka_flow_movie_multiple_translators_shows_keyboard():
    message, status_msg = _make_message()
    info = RezkaContentInfo(title="A Movie", is_series=False, translators={56: "Дубляж", 99: "Оригинал"})
    redis = MagicMock()

    with patch("app.bot.routers.rezka_flow.get_settings", return_value=_make_settings()), \
         patch("app.bot.routers.rezka_flow.get_redis", return_value=redis), \
         patch("app.bot.routers.rezka_flow.get_rezka_content_info", return_value=info), \
         patch("app.bot.routers.rezka_flow.set_rezka_session") as mock_set:
        await start_rezka_flow(message, "https://rezka.ag/films/x/1-y.html", "https://rezka.ag/films/x/1-y.html", "720p")

    mock_set.assert_called_once()
    status_msg.edit_text.assert_awaited_once()
    args, kwargs = status_msg.edit_text.call_args
    assert "Выберите озвучку" in args[0]
    assert "reply_markup" in kwargs


@pytest.mark.asyncio
async def test_start_rezka_flow_reports_resolve_error():
    message, status_msg = _make_message()
    redis = MagicMock()

    with patch("app.bot.routers.rezka_flow.get_settings", return_value=_make_settings()), \
         patch("app.bot.routers.rezka_flow.get_redis", return_value=redis), \
         patch("app.bot.routers.rezka_flow.get_rezka_content_info", side_effect=RezkaResolveError("blocked")):
        await start_rezka_flow(message, "https://rezka.ag/films/x/1-y.html", "https://rezka.ag/films/x/1-y.html", "720p")

    status_msg.edit_text.assert_awaited_once_with("❌ blocked")


@pytest.mark.asyncio
async def test_start_rezka_flow_no_translators_clears_session():
    message, status_msg = _make_message()
    info = RezkaContentInfo(title="A Movie", is_series=False, translators={})
    redis = MagicMock()

    with patch("app.bot.routers.rezka_flow.get_settings", return_value=_make_settings()), \
         patch("app.bot.routers.rezka_flow.get_redis", return_value=redis), \
         patch("app.bot.routers.rezka_flow.get_rezka_content_info", return_value=info), \
         patch("app.bot.routers.rezka_flow.clear_rezka_session") as mock_clear:
        await start_rezka_flow(message, "https://rezka.ag/films/x/1-y.html", "https://rezka.ag/films/x/1-y.html", "720p")

    mock_clear.assert_called_once_with(1, redis)
    status_msg.edit_text.assert_awaited_once()
    assert "не нашлось" in status_msg.edit_text.call_args[0][0]


# ---------------------------------------------------------------------------
# rezka_translator_chosen / _after_translator_chosen
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_rezka_translator_chosen_expired_session():
    callback = _make_callback(data="rezka:tr:56")
    with patch("app.bot.routers.rezka_flow.get_redis", return_value=MagicMock()), \
         patch("app.bot.routers.rezka_flow.get_rezka_session", return_value=None):
        await rezka_translator_chosen(callback)
    callback.message.edit_text.assert_awaited_once()
    assert "истекло" in callback.message.edit_text.call_args[0][0]


@pytest.mark.asyncio
async def test_rezka_translator_chosen_movie_enqueues_and_clears_session():
    callback = _make_callback(data="rezka:tr:56")
    session = {
        "raw_url": "https://rezka.ag/films/x/1-y.html",
        "url": "https://rezka.ag/films/x/1-y.html",
        "quality": "720p",
        "is_series": False,
        "chat_id": 1,
        "message_id": 10,
    }
    redis = MagicMock()

    with patch("app.bot.routers.rezka_flow.get_redis", return_value=redis), \
         patch("app.bot.routers.rezka_flow.get_rezka_session", return_value=session), \
         patch("app.bot.routers.rezka_flow.clear_rezka_session") as mock_clear, \
         patch("app.bot.routers.rezka_flow.enqueue_download", new=AsyncMock()) as mock_enqueue:
        await rezka_translator_chosen(callback)

    mock_clear.assert_called_once_with(1, redis)
    mock_enqueue.assert_awaited_once()
    assert mock_enqueue.call_args[0][5] == "https://rezka.ag/films/x/1-y.html?rezka_tr=56"


@pytest.mark.asyncio
async def test_rezka_translator_chosen_series_shows_season_keyboard():
    callback = _make_callback(data="rezka:tr:56")
    episodes_info = [
        {"season": 1, "episodes": [{"episode": 1, "translations": [{"translator_id": 56}]}]},
        {"season": 2, "episodes": [{"episode": 1, "translations": [{"translator_id": 56}]}]},
    ]
    session = {
        "raw_url": "https://rezka.ag/series/x/1-y.html",
        "url": "https://rezka.ag/series/x/1-y.html",
        "quality": "720p",
        "is_series": True,
        "episodes_info": episodes_info,
        "chat_id": 1,
        "message_id": 10,
    }
    redis = MagicMock()

    with patch("app.bot.routers.rezka_flow.get_redis", return_value=redis), \
         patch("app.bot.routers.rezka_flow.get_rezka_session", return_value=session):
        await rezka_translator_chosen(callback)

    callback.message.edit_text.assert_awaited_once()
    args, kwargs = callback.message.edit_text.call_args
    assert "сезон" in args[0].lower()
    assert "reply_markup" in kwargs


@pytest.mark.asyncio
async def test_rezka_translator_chosen_series_single_season_skips_to_episodes():
    callback = _make_callback(data="rezka:tr:56")
    episodes_info = [
        {"season": 1, "episodes": [
            {"episode": 1, "translations": [{"translator_id": 56}]},
            {"episode": 2, "translations": [{"translator_id": 56}]},
        ]},
    ]
    session = {
        "raw_url": "https://rezka.ag/series/x/1-y.html",
        "url": "https://rezka.ag/series/x/1-y.html",
        "quality": "720p",
        "is_series": True,
        "episodes_info": episodes_info,
        "chat_id": 1,
        "message_id": 10,
    }
    redis = MagicMock()

    with patch("app.bot.routers.rezka_flow.get_redis", return_value=redis), \
         patch("app.bot.routers.rezka_flow.get_rezka_session", return_value=session):
        await rezka_translator_chosen(callback)

    callback.message.edit_text.assert_awaited_once()
    args, kwargs = callback.message.edit_text.call_args
    assert "серию" in args[0].lower() or "серия" in args[0].lower()
    assert "reply_markup" in kwargs


# ---------------------------------------------------------------------------
# rezka_season_chosen
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_rezka_season_chosen_shows_episode_keyboard():
    callback = _make_callback(data="rezka:season:1")
    episodes_info = [
        {"season": 1, "episodes": [
            {"episode": 1, "translations": [{"translator_id": 56}]},
            {"episode": 2, "translations": [{"translator_id": 56}]},
        ]},
    ]
    session = {"translator_id": 56, "episodes_info": episodes_info}
    redis = MagicMock()

    with patch("app.bot.routers.rezka_flow.get_redis", return_value=redis), \
         patch("app.bot.routers.rezka_flow.get_rezka_session", return_value=session), \
         patch("app.bot.routers.rezka_flow.set_rezka_session") as mock_set:
        await rezka_season_chosen(callback)

    mock_set.assert_called_once()
    callback.message.edit_text.assert_awaited_once()
    args, kwargs = callback.message.edit_text.call_args
    assert "Сезон 1" in args[0]
    assert "reply_markup" in kwargs


# ---------------------------------------------------------------------------
# rezka_episode_chosen
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_rezka_episode_chosen_enqueues_with_full_selection():
    callback = _make_callback(data="rezka:ep:5")
    session = {
        "raw_url": "https://rezka.ag/series/x/1-y.html",
        "url": "https://rezka.ag/series/x/1-y.html",
        "quality": "720p",
        "translator_id": 56,
        "season": 2,
        "chat_id": 1,
        "message_id": 10,
    }
    redis = MagicMock()

    with patch("app.bot.routers.rezka_flow.get_redis", return_value=redis), \
         patch("app.bot.routers.rezka_flow.get_rezka_session", return_value=session), \
         patch("app.bot.routers.rezka_flow.clear_rezka_session") as mock_clear, \
         patch("app.bot.routers.rezka_flow.enqueue_download", new=AsyncMock()) as mock_enqueue:
        await rezka_episode_chosen(callback)

    mock_clear.assert_called_once_with(1, redis)
    mock_enqueue.assert_awaited_once()
    final_url = mock_enqueue.call_args[0][5]
    assert "rezka_tr=56" in final_url
    assert "rezka_s=2" in final_url


# ---------------------------------------------------------------------------
# rezka_back_to_translators / rezka_back_to_seasons
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_rezka_back_to_translators_uses_cached_session_data():
    callback = _make_callback(data="rezka:back:translators")
    session = {"title": "A Movie", "translators": {"56": "Дубляж", "99": "Оригинал"}}
    redis = MagicMock()

    with patch("app.bot.routers.rezka_flow.get_redis", return_value=redis), \
         patch("app.bot.routers.rezka_flow.get_rezka_session", return_value=session):
        await rezka_back_to_translators(callback)

    callback.message.edit_text.assert_awaited_once()
    args, kwargs = callback.message.edit_text.call_args
    assert "A Movie" in args[0]
    assert "reply_markup" in kwargs


@pytest.mark.asyncio
async def test_rezka_back_to_translators_expired_session():
    callback = _make_callback(data="rezka:back:translators")
    with patch("app.bot.routers.rezka_flow.get_redis", return_value=MagicMock()), \
         patch("app.bot.routers.rezka_flow.get_rezka_session", return_value=None):
        await rezka_back_to_translators(callback)
    callback.message.edit_text.assert_awaited_once_with(
        "⚠️ Время выбора истекло, пришли ссылку ещё раз."
    )


@pytest.mark.asyncio
async def test_rezka_back_to_seasons_recomputes_from_episodes_info():
    callback = _make_callback(data="rezka:back:season")
    episodes_info = [
        {"season": 1, "episodes": [{"episode": 1, "translations": [{"translator_id": 56}]}]},
        {"season": 2, "episodes": [{"episode": 1, "translations": [{"translator_id": 56}]}]},
    ]
    session = {"translator_id": 56, "episodes_info": episodes_info}
    redis = MagicMock()

    with patch("app.bot.routers.rezka_flow.get_redis", return_value=redis), \
         patch("app.bot.routers.rezka_flow.get_rezka_session", return_value=session):
        await rezka_back_to_seasons(callback)

    callback.message.edit_text.assert_awaited_once()
    args, kwargs = callback.message.edit_text.call_args
    assert "сезон" in args[0].lower()
    assert "reply_markup" in kwargs


@pytest.mark.asyncio
async def test_rezka_back_to_seasons_expired_session():
    callback = _make_callback(data="rezka:back:season")
    with patch("app.bot.routers.rezka_flow.get_redis", return_value=MagicMock()), \
         patch("app.bot.routers.rezka_flow.get_rezka_session", return_value={}):
        await rezka_back_to_seasons(callback)
    callback.message.edit_text.assert_awaited_once_with(
        "⚠️ Время выбора истекло, пришли ссылку ещё раз."
    )


# ---------------------------------------------------------------------------
# rezka_season_all_chosen
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_rezka_season_all_chosen_enqueues_sorted_episodes():
    callback = _make_callback(data="rezka:season_all")
    episodes_info = [
        {"season": 2, "episodes": [
            {"episode": 3, "translations": [{"translator_id": 56}]},
            {"episode": 1, "translations": [{"translator_id": 56}]},
            {"episode": 2, "translations": [{"translator_id": 56}]},
        ]},
    ]
    session = {
        "raw_url": "https://rezka.ag/series/x/1-y.html",
        "url": "https://rezka.ag/series/x/1-y.html",
        "quality": "720p",
        "translator_id": 56,
        "season": 2,
        "episodes_info": episodes_info,
        "chat_id": 1,
        "message_id": 10,
    }
    redis = MagicMock()

    with patch("app.bot.routers.rezka_flow.get_redis", return_value=redis), \
         patch("app.bot.routers.rezka_flow.get_rezka_session", return_value=session), \
         patch("app.bot.routers.rezka_flow.clear_rezka_session") as mock_clear, \
         patch("app.bot.routers.rezka_flow.enqueue_season_download", new=AsyncMock()) as mock_enqueue:
        await rezka_season_all_chosen(callback)

    mock_clear.assert_called_once_with(1, redis)
    mock_enqueue.assert_awaited_once()
    args = mock_enqueue.call_args[0]
    assert args[6] == 56  # translator_id
    assert args[7] == 2  # season
    assert args[8] == [1, 2, 3]  # sorted episodes


@pytest.mark.asyncio
async def test_rezka_season_all_chosen_expired_session():
    callback = _make_callback(data="rezka:season_all")
    with patch("app.bot.routers.rezka_flow.get_redis", return_value=MagicMock()), \
         patch("app.bot.routers.rezka_flow.get_rezka_session", return_value=None):
        await rezka_season_all_chosen(callback)
    callback.message.edit_text.assert_awaited_once_with(
        "⚠️ Время выбора истекло, пришли ссылку ещё раз."
    )
