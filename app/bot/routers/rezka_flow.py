from __future__ import annotations

import asyncio
import logging

from aiogram import F, Router
from aiogram.types import CallbackQuery, Message

from app.bot.routers.url_handler import enqueue_download
from app.core.config import get_settings
from app.keyboards.rezka import episode_keyboard, season_keyboard, translator_keyboard
from app.services.redis_client import get_redis
from app.services.rezka_session import clear_rezka_session, get_rezka_session, set_rezka_session
from app.utils.rezka import (
    RezkaResolveError,
    build_selection_url,
    episodes_for_translator_season,
    get_rezka_content_info,
    seasons_for_translator,
)

router = Router()
logger = logging.getLogger(__name__)

_SESSION_EXPIRED = "⚠️ Время выбора истекло, пришли ссылку ещё раз."


async def start_rezka_flow(message: Message, raw_url: str, normalized_url: str, quality: str) -> None:
    """Entry point from url_handler: fetch the page's translators (and, for
    a series, its episode listing) and start the inline-keyboard selection.

    Runs the (potentially antibot-bypass-heavy, 10-30+ second cold) fetch
    in a thread so it doesn't block the bot's event loop for every other
    user while one person's rezka link is being resolved.
    """
    settings = get_settings()
    redis = get_redis()
    status_msg = await message.answer("🔍 Получаю информацию о видео с rezka...")

    try:
        info = await asyncio.to_thread(
            get_rezka_content_info, normalized_url, settings.rezka_antibot_bypass, redis,
        )
    except RezkaResolveError as exc:
        logger.info("rezka content info failed for %s: %s", normalized_url, exc)
        await status_msg.edit_text(f"❌ {exc}")
        return
    except Exception:
        logger.exception("Unexpected error fetching rezka content info for %s", normalized_url)
        await status_msg.edit_text("❌ Не удалось получить информацию о видео с rezka.")
        return

    set_rezka_session(
        message.from_user.id,
        {
            "raw_url": raw_url,
            "url": normalized_url,
            "quality": quality,
            "title": info.title,
            "is_series": info.is_series,
            "translators": {str(k): v for k, v in info.translators.items()},
            "episodes_info": info.episodes_info,
            "chat_id": message.chat.id,
            "message_id": message.message_id,
        },
        redis,
    )

    if not info.translators:
        await status_msg.edit_text("❌ На странице rezka не нашлось ни одной озвучки.")
        clear_rezka_session(message.from_user.id, redis)
        return

    if len(info.translators) == 1:
        translator_id = next(iter(info.translators))
        await _after_translator_chosen(status_msg, message.from_user.id, translator_id)
        return

    await status_msg.edit_text(
        f"🎬 <b>{info.title}</b>\n\nВыберите озвучку:",
        reply_markup=translator_keyboard(info.translators),
    )


@router.callback_query(F.data.startswith("rezka:tr:"))
async def rezka_translator_chosen(callback: CallbackQuery) -> None:
    await callback.answer()
    translator_id = int(callback.data.split(":", 2)[2])
    session = get_rezka_session(callback.from_user.id, get_redis())
    if not session:
        await callback.message.edit_text(_SESSION_EXPIRED)
        return
    await _after_translator_chosen(callback.message, callback.from_user.id, translator_id)


async def _after_translator_chosen(status_msg: Message, user_id: int, translator_id: int) -> None:
    redis = get_redis()
    session = get_rezka_session(user_id, redis)
    if not session:
        await status_msg.edit_text(_SESSION_EXPIRED)
        return

    session["translator_id"] = translator_id
    set_rezka_session(user_id, session, redis)

    if not session["is_series"]:
        clear_rezka_session(user_id, redis)
        final_url = build_selection_url(session["url"], translator_id)
        await status_msg.edit_text("⏳ Добавляю в очередь...")
        await enqueue_download(
            status_msg, user_id, session["chat_id"], session["message_id"],
            session["raw_url"], final_url, session["quality"],
        )
        return

    seasons = seasons_for_translator(session["episodes_info"], translator_id)
    if not seasons:
        await status_msg.edit_text("⚠️ Для этой озвучки не нашлось ни одного сезона.")
        clear_rezka_session(user_id, redis)
        return
    if len(seasons) == 1:
        await _after_season_chosen(status_msg, user_id, seasons[0])
        return

    await status_msg.edit_text("📺 Выберите сезон:", reply_markup=season_keyboard(seasons))


@router.callback_query(F.data.startswith("rezka:season:"))
async def rezka_season_chosen(callback: CallbackQuery) -> None:
    await callback.answer()
    season = int(callback.data.split(":", 2)[2])
    await _after_season_chosen(callback.message, callback.from_user.id, season)


async def _after_season_chosen(status_msg: Message, user_id: int, season: int) -> None:
    redis = get_redis()
    session = get_rezka_session(user_id, redis)
    if not session:
        await status_msg.edit_text(_SESSION_EXPIRED)
        return

    session["season"] = season
    set_rezka_session(user_id, session, redis)

    episodes = episodes_for_translator_season(session["episodes_info"], session["translator_id"], season)
    if not episodes:
        await status_msg.edit_text(f"⚠️ В сезоне {season} не нашлось ни одной серии для этой озвучки.")
        clear_rezka_session(user_id, redis)
        return

    await status_msg.edit_text(f"📺 Сезон {season}. Выберите серию:", reply_markup=episode_keyboard(episodes))


@router.callback_query(F.data.startswith("rezka:ep:"))
async def rezka_episode_chosen(callback: CallbackQuery) -> None:
    await callback.answer()
    episode = int(callback.data.split(":", 2)[2])
    user_id = callback.from_user.id
    redis = get_redis()
    session = get_rezka_session(user_id, redis)
    if not session:
        await callback.message.edit_text(_SESSION_EXPIRED)
        return

    clear_rezka_session(user_id, redis)
    final_url = build_selection_url(session["url"], session["translator_id"], session["season"], episode)
    await callback.message.edit_text("⏳ Добавляю в очередь...")
    await enqueue_download(
        callback.message, user_id, session["chat_id"], session["message_id"],
        session["raw_url"], final_url, session["quality"],
    )
