from __future__ import annotations

import logging

from aiogram import Bot, F, Router
from aiogram.filters import Command
from aiogram.types import Message

from app.bot.access import _check_access
from app.core.config import get_settings
from app.db.repository import Repository
from app.db.session import get_session
from app.services.redis_client import get_redis
from app.utils.platforms import PLATFORMS, platform_from_filename

router = Router()
logger = logging.getLogger(__name__)

_MAX_COOKIE_FILE_BYTES = 2 * 1024 * 1024

_COOKIES_HELP = (
    "🍪 <b>Личные cookies</b>\n\n"
    "Некоторые сайты (особенно YouTube) требуют cookies авторизованного аккаунта.\n\n"
    "Экспортируйте cookies в формате <b>Netscape</b> (cookies.txt) и пришлите файл "
    "боту. <b>Имя файла задаёт платформу:</b>\n"
    "• <code>youtube.txt</code>\n"
    "• <code>instagram.txt</code>\n"
    "• <code>tiktok.txt</code>\n"
    "• <code>facebook.txt</code>\n\n"
    "Удалить: <code>/delcookies youtube</code>"
)


def _looks_like_netscape(text: str) -> bool:
    head = text.lstrip()
    if head.startswith("# Netscape HTTP Cookie File") or head.startswith("# HTTP Cookie File"):
        return True
    for line in text.splitlines():
        if line and not line.startswith("#") and line.count("\t") >= 5:
            return True
    return False


@router.message(Command("cookies"))
async def cookies_info(message: Message) -> None:
    settings = get_settings()
    redis = get_redis()
    allowed, denial_msg = _check_access(message.from_user.id, settings, redis)
    if not allowed:
        await message.answer(denial_msg)
        return

    with get_session() as session:
        platforms = Repository(session).list_user_platforms(message.from_user.id)

    if platforms:
        status = "Загружены: " + ", ".join(sorted(platforms))
    else:
        status = "Пока не загружены."
    await message.answer(f"{_COOKIES_HELP}\n\n<b>Статус:</b> {status}")


@router.message(Command("delcookies"))
async def delete_cookies(message: Message) -> None:
    settings = get_settings()
    redis = get_redis()
    allowed, denial_msg = _check_access(message.from_user.id, settings, redis)
    if not allowed:
        await message.answer(denial_msg)
        return

    parts = (message.text or "").split(maxsplit=1)
    platform = parts[1].strip().lower() if len(parts) > 1 else ""
    if platform not in PLATFORMS:
        await message.answer(
            "Использование: <code>/delcookies &lt;platform&gt;</code>\n"
            f"Платформы: {', '.join(PLATFORMS)}"
        )
        return

    with get_session() as session:
        removed = Repository(session).delete_user_cookies(message.from_user.id, platform)
    if removed:
        await message.answer(f"✅ Cookies для <b>{platform}</b> удалены.")
    else:
        await message.answer(f"⚠️ Cookies для <b>{platform}</b> не найдены.")


# Must be registered AFTER the broadcast router so an admin in broadcast mode
# can broadcast documents (broadcast handler catches them first).
@router.message(F.document)
async def upload_cookies(message: Message, bot: Bot) -> None:
    settings = get_settings()
    redis = get_redis()
    allowed, denial_msg = _check_access(message.from_user.id, settings, redis)
    if not allowed:
        await message.answer(denial_msg)
        return

    document = message.document
    platform = platform_from_filename(document.file_name)
    if not platform:
        await message.answer(
            "Чтобы загрузить cookies, пришлите <b>.txt</b> файл с именем платформы: "
            "<code>youtube.txt</code>, <code>instagram.txt</code>, "
            "<code>tiktok.txt</code> или <code>facebook.txt</code>.\n\n"
            "Подробнее: /cookies"
        )
        return

    if document.file_size and document.file_size > _MAX_COOKIE_FILE_BYTES:
        await message.answer("⚠️ Файл слишком большой для cookies.")
        return

    try:
        buffer = await bot.download(document)
        content = buffer.read().decode("utf-8", errors="replace")
    except Exception:
        logger.exception("Failed to download cookies file from user %s", message.from_user.id)
        await message.answer("❌ Не удалось прочитать файл. Попробуйте ещё раз.")
        return

    if not _looks_like_netscape(content):
        await message.answer(
            "❌ Это не похоже на cookies в формате Netscape.\n"
            "Экспортируйте файл расширением «Get cookies.txt LOCALLY» или "
            "<code>yt-dlp --cookies-from-browser ... --cookies file.txt</code>."
        )
        return

    with get_session() as session:
        Repository(session).set_user_cookies(message.from_user.id, platform, content)
    await message.answer(f"✅ Cookies для <b>{platform}</b> сохранены.")
