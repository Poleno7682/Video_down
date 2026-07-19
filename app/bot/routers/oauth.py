from __future__ import annotations

import asyncio
import time

from aiogram import Router
from aiogram.filters import Command
from aiogram.types import Message

from app.db.repository import CookieRepository, GoogleTokenRepository
from app.db.session import get_session
from app.services.google_oauth import (
    DeviceFlowExpired,
    DeviceFlowPending,
    generate_youtube_cookies,
    poll_token,
    revoke_token,
    start_device_flow,
)
from app.services.redis_client import get_redis

router = Router()


def _google_linking_key(user_id: int) -> str:
    return f"google_linking:{user_id}"


@router.message(Command("link_google"))
async def link_google(message: Message) -> None:
    redis = get_redis()
    user_id = message.from_user.id
    link_key = _google_linking_key(user_id)
    if redis.exists(link_key):
        await message.answer(
            "⏳ Авторизация уже в процессе.\n"
            "Подтверди код в браузере или подожди, пока он не истечёт."
        )
        return

    try:
        flow_info = await asyncio.to_thread(start_device_flow)
    except Exception as e:
        await message.answer(f"❌ Не удалось начать авторизацию Google: {e}")
        return

    user_code = flow_info.get("user_code", "N/A")
    verification_url = flow_info.get("verification_url", "https://google.com/device")
    expires_in = int(flow_info.get("expires_in", 1800))
    device_code = flow_info["device_code"]
    interval = max(5, int(flow_info.get("interval", 5)))

    redis.setex(link_key, expires_in, "1")

    await message.answer(
        "🔗 <b>Привяжи свой Google-аккаунт</b>\n\n"
        f"1️⃣ Открой: <a href=\"{verification_url}\">{verification_url}</a>\n"
        f"2️⃣ Введи код: <code>{user_code}</code>\n\n"
        f"⏱ Код действует {expires_in // 60} мин.\n\n"
        "После подтверждения бот автоматически получит YouTube cookies.",
        disable_web_page_preview=True,
    )

    asyncio.create_task(
        _poll_google_link(message, device_code, interval, expires_in, link_key)
    )


async def _poll_google_link(
    message: Message,
    device_code: str,
    interval: int,
    expires_in: int,
    link_key: str,
) -> None:
    """Background polling loop for the Google OAuth device flow."""
    redis = get_redis()
    user_id = message.from_user.id
    deadline = time.monotonic() + expires_in

    try:
        while time.monotonic() < deadline:
            await asyncio.sleep(interval)
            try:
                token_info = await asyncio.to_thread(poll_token, device_code)
            except DeviceFlowPending:
                continue
            except DeviceFlowExpired:
                await message.answer(
                    "❌ Код авторизации истёк. Запусти /link_google заново."
                )
                return
            except Exception:
                continue

            access_tok = token_info.get("access_token")
            refresh_tok = token_info.get("refresh_token")
            if not access_tok:
                await message.answer("❌ Неожиданный ответ от Google. Попробуй /link_google снова.")
                return

            try:
                cookie_text = await asyncio.to_thread(generate_youtube_cookies, access_tok)
            except Exception as e:
                await message.answer(
                    f"❌ Авторизация прошла, но не удалось получить YouTube cookies: {e}\n\n"
                    "Попробуй /link_google ещё раз."
                )
                return

            with get_session() as session:
                CookieRepository(session).set_user_cookies(user_id, "youtube", cookie_text)
                if refresh_tok:
                    GoogleTokenRepository(session).set_google_token(user_id, refresh_tok)

            await message.answer(
                "✅ <b>Google-аккаунт привязан!</b>\n\n"
                "YouTube cookies сохранены — они помогут обойти bot-detection при скачивании.\n\n"
                "<b>Ограничение:</b> для видео 18+ и приватных видео по-прежнему нужны полные "
                "cookies из браузера. Загрузи их командой /cookies (файл <code>youtube.txt</code>).\n\n"
                "Для отвязки: /unlink_google"
            )
            return

        await message.answer("❌ Время ожидания истекло. Запусти /link_google заново.")
    finally:
        redis.delete(link_key)


@router.message(Command("unlink_google"))
async def unlink_google(message: Message) -> None:
    redis = get_redis()
    user_id = message.from_user.id
    redis.delete(_google_linking_key(user_id))

    refresh_token_str: str | None = None
    had_token: bool = False
    with get_session() as session:
        google_repo = GoogleTokenRepository(session)
        cookie_repo = CookieRepository(session)
        token_rec = google_repo.get_google_token(user_id)
        if token_rec:
            had_token = True
            refresh_token_str = token_rec.refresh_token
            google_repo.delete_google_token(user_id)
        removed_cookies = cookie_repo.delete_user_cookies(user_id, "youtube")

    if refresh_token_str:
        await asyncio.to_thread(revoke_token, refresh_token_str)

    if had_token or removed_cookies:
        await message.answer("✅ Google-аккаунт отвязан. YouTube cookies удалены.")
    else:
        await message.answer("ℹ️ Привязки к Google-аккаунту не найдено.")
