from __future__ import annotations

import asyncio
import logging

from aiogram import Bot, F, Router
from aiogram.filters import BaseFilter, Command
from aiogram.types import CallbackQuery, Message

from app.bot.access import _is_admin
from app.bot.filters import AdminFilter
from app.bot.utils import safe_edit_text
from app.core.config import get_settings
from app.db.repository import UserRepository
from app.db.session import get_session
from app.keyboards.admin import broadcast_cancel_keyboard
from app.services.redis_client import get_redis
from app.utils.broadcast import parse_buttons

router = Router()
logger = logging.getLogger(__name__)


def _broadcast_key(admin_id: int) -> str:
    return f"broadcast_mode:{admin_id}"


class BroadcastModeFilter(BaseFilter):
    """Matches only when an admin currently has an active broadcast session."""

    async def __call__(self, message: Message) -> bool:
        settings = get_settings()
        if not _is_admin(message.from_user.id, settings):
            return False
        return bool(get_redis().exists(_broadcast_key(message.from_user.id)))


async def _enter_broadcast_mode(target: Message, admin_id: int) -> None:
    settings = get_settings()
    get_redis().setex(_broadcast_key(admin_id), settings.broadcast_timeout_seconds, "1")
    minutes = settings.broadcast_timeout_seconds // 60
    await target.answer(
        "📢 <b>Режим рассылки включён.</b>\n\n"
        "Отправьте сообщение (текст, фото, GIF, видео, музыку) — оно будет разослано "
        "всем пользователям бота.\n\n"
        "<b>Inline-кнопки:</b> добавьте в конце сообщения строку <code>---</code>, "
        "а далее по одной кнопке в строке в формате <code>Текст | https://ссылка</code>.\n\n"
        f"⏱ Режим автоматически выключится через {minutes} мин без активности "
        "(таймер сбрасывается после каждой рассылки).",
        reply_markup=broadcast_cancel_keyboard(),
    )


@router.message(Command("broadcast"), AdminFilter())
async def broadcast_command(message: Message) -> None:
    await _enter_broadcast_mode(message, message.from_user.id)


@router.callback_query(F.data == "admin:broadcast", AdminFilter(alert_on_deny=True))
async def broadcast_start_callback(callback: CallbackQuery) -> None:
    await callback.answer()
    await _enter_broadcast_mode(callback.message, callback.from_user.id)


@router.callback_query(F.data == "broadcast:cancel", AdminFilter(alert_on_deny=True))
async def broadcast_cancel_callback(callback: CallbackQuery) -> None:
    get_redis().delete(_broadcast_key(callback.from_user.id))
    await callback.answer("Рассылка отменена")
    await safe_edit_text(callback.message, "❌ Режим рассылки выключен.")


async def _broadcast_to_all(bot: Bot, source: Message) -> tuple[int, int, int]:
    """Send a copy of `source` to every user. Returns (ok, failed, total)."""
    with get_session() as session:
        user_ids = UserRepository(session).get_all_user_ids()

    from_chat_id = source.chat.id
    message_id = source.message_id

    if source.text is not None:
        clean_text, markup = parse_buttons(source.html_text)
        is_text = True
    else:
        clean_text, markup = parse_buttons(source.caption)
        is_text = False

    ok = 0
    failed = 0
    for uid in user_ids:
        try:
            if markup is None:
                await bot.copy_message(chat_id=uid, from_chat_id=from_chat_id, message_id=message_id)
            elif is_text:
                await bot.send_message(uid, clean_text, reply_markup=markup)
            else:
                await bot.copy_message(
                    chat_id=uid,
                    from_chat_id=from_chat_id,
                    message_id=message_id,
                    caption=clean_text or None,
                    reply_markup=markup,
                )
            ok += 1
        except Exception:
            failed += 1
        await asyncio.sleep(0.05)

    return ok, failed, len(user_ids)


# Must be registered BEFORE the cookies router's F.document handler so an admin
# in broadcast mode can broadcast documents (this handler catches them first).
@router.message(BroadcastModeFilter())
async def broadcast_message(message: Message, bot: Bot) -> None:
    settings = get_settings()
    get_redis().setex(_broadcast_key(message.from_user.id), settings.broadcast_timeout_seconds, "1")

    ok, failed, total = await _broadcast_to_all(bot, message)
    await message.answer(
        f"📢 Рассылка завершена.\n"
        f"✅ Доставлено: {ok}\n"
        f"⚠️ Не доставлено: {failed}\n"
        f"👥 Всего пользователей: {total}",
        reply_markup=broadcast_cancel_keyboard(),
    )
