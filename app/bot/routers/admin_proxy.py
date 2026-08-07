from __future__ import annotations

import asyncio
import html
import logging
import time

from aiogram import Bot, F, Router
from aiogram.filters import BaseFilter, Command
from aiogram.types import CallbackQuery, Message

from app.bot.access import _is_admin
from app.bot.filters import AdminFilter
from app.core.config import get_settings
from app.db.repository import ProxyRepository
from app.db.session import run_in_session
from app.keyboards.admin import proxy_scheme_keyboard
from app.services.proxy_awaiting import clear_proxy_awaiting, get_proxy_awaiting, set_proxy_awaiting
from app.services.redis_client import get_redis
from app.utils.proxy_check import ProxyCheckError, check_proxy
from app.utils.proxy_format import parse_proxy_input

router = Router()
logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Proxy pool (SOCKS5/SOCKS5h) — routes yt-dlp around anti-bot IP blocks.
# The worker tries proxies in order (least-failed first), falling over to the
# next one on failure — see app.worker.tasks._resolve_proxies.
# ---------------------------------------------------------------------------

_PROXY_SCHEMES = ("socks5://", "socks5h://", "socks4://", "http://", "https://")

_PROXY_FORMATS_HELP = (
    "Отправьте <b>один прокси текстом</b> в одном из форматов:\n"
    "• <code>IP:PORT</code>\n"
    "• <code>IP:PORT@LOGIN:PASSWORD</code>\n"
    "• <code>IP:PORT:LOGIN:PASSWORD</code>\n"
    "• <code>IP:PORT;LOGIN:PASSWORD</code>\n"
    "• или полный URL: <code>socks5h://login:pass@host:port</code>\n\n"
    "Либо пришлите <b>.txt файл</b> со списком — по одному прокси на строку, "
    "в любом из этих же форматов (можно вперемешку).\n\n"
    "Или /cancel для отмены."
)

_MAX_PROXY_FILE_BYTES = 512 * 1024
_MAX_PROXY_LINES_PER_FILE = 500
# Proxy checks are I/O-bound (network round-trip to YouTube), so this caps
# how many run at once rather than serializing 500 checks one after another.
_PROXY_CHECK_CONCURRENCY = 10


@router.message(Command("addproxy"), AdminFilter())
async def add_proxy(message: Message) -> None:
    await message.answer(
        "Выберите тип прокси:",
        reply_markup=proxy_scheme_keyboard(),
    )


@router.callback_query(F.data.startswith("proxy:scheme:"), AdminFilter(alert_on_deny=True))
async def proxy_scheme_chosen(callback: CallbackQuery) -> None:
    await callback.answer()
    scheme = callback.data.split(":", 2)[2]
    set_proxy_awaiting(callback.from_user.id, scheme, get_redis())
    await callback.message.answer(
        f"Тип: <b>{scheme}</b>\n\n{_PROXY_FORMATS_HELP}"
    )


@router.message(Command("delproxy"), AdminFilter())
async def delete_proxy(message: Message) -> None:
    parts = (message.text or "").split(maxsplit=1)
    raw = parts[1].strip() if len(parts) >= 2 else ""
    if not raw.isdigit():
        await message.answer("Использование: <code>/delproxy &lt;id&gt;</code>\nСписок id: /listproxies")
        return

    removed = await run_in_session(lambda session: ProxyRepository(session).delete_proxy(int(raw)))
    if removed:
        await message.answer(f"✅ Прокси <code>{raw}</code> удалён.")
    else:
        await message.answer(f"⚠️ Прокси с id <code>{raw}</code> не найден.")


@router.message(Command("listproxies"), AdminFilter())
async def list_proxies(message: Message) -> None:
    proxies = await run_in_session(lambda session: ProxyRepository(session).list_proxies())

    if not proxies:
        await message.answer(
            "Список прокси пуст.\nДобавьте: /addproxy"
        )
        return

    lines = [
        f"• <code>{p.id}</code> — <code>{html.escape(p.url)}</code>"
        + (f" ⚠️ сбоев подряд: {p.failure_count}" if p.failure_count else " ✅")
        for p in proxies
    ]
    await message.answer(
        f"🌐 <b>Прокси для yt-dlp</b> ({len(proxies)}), в порядке перебора:\n\n"
        + "\n".join(lines)
        + "\n\nУдалить: <code>/delproxy &lt;id&gt;</code>"
    )


# ---------------------------------------------------------------------------
# Proxy input interceptor — catches the proxy string sent after the admin
# picked a scheme via proxy_scheme_chosen above. Registered before the
# generic limit-input interceptor / url_handler for the same reason as that
# one: raw text must not fall through to "try to download this as a URL".
# ---------------------------------------------------------------------------

class _ProxyAwaitingFilter(BaseFilter):
    async def __call__(self, message: Message) -> bool:
        if not _is_admin(message.from_user.id, get_settings()):
            return False
        return bool(get_proxy_awaiting(message.from_user.id, get_redis()))


async def _existing_proxy_urls() -> set[str]:
    return set(await run_in_session(lambda session: ProxyRepository(session).get_enabled_proxy_urls()))


async def _check_and_add_proxy(
    raw: str,
    scheme: str,
    admin_id: int,
    existing_urls: set[str],
    dedup_lock: asyncio.Lock,
) -> tuple[str | None, str, bool]:
    """Parse, dedup, probe, and save one proxy line.

    Returns (added_url_or_None, message, was_duplicate). existing_urls is
    shared across a whole batch (and reserved under dedup_lock before the
    slow probe) so an already-added proxy — or the same proxy appearing
    twice in one file — is skipped instead of being re-checked/re-added.
    """
    proxy_url = parse_proxy_input(raw, scheme)
    if not proxy_url:
        return None, "не распознан формат", False

    async with dedup_lock:
        if proxy_url in existing_urls:
            return None, "уже есть в базе — пропущен", True
        existing_urls.add(proxy_url)

    try:
        await asyncio.to_thread(check_proxy, proxy_url)
    except ProxyCheckError as exc:
        return None, str(exc), False
    except Exception as exc:
        logger.warning("Unexpected error checking proxy %s: %s", proxy_url, exc)
        return None, f"ошибка проверки: {exc}", False

    proxy = await run_in_session(lambda session: ProxyRepository(session).add_proxy(proxy_url, added_by=admin_id))
    return proxy.url, "добавлен", False


_MAX_PROXY_REPORT_CHARS = 3500  # safety margin under Telegram's 4096 message limit


async def _send_proxy_batch_report(
    status_msg: Message,
    original_message: Message,
    added: list[str],
    duplicates: list[str],
    failed: list[tuple[str, str]],
    total: int,
    truncated: bool,
) -> None:
    """Build and send the final per-batch report.

    Builds by whole line (never truncates mid-string) so a long failure list
    can't cut an HTML tag in half and make Telegram silently reject the
    edit — every raw proxy string is also escaped since it's admin-controlled
    free text, not something we generated ourselves.
    """
    lines = [f"✅ Добавлено: {len(added)}/{total}", f"⏭ Уже были в базе: {len(duplicates)}"]
    if failed:
        lines.append("\n❌ Не добавлены:")
        shown = 0
        budget = _MAX_PROXY_REPORT_CHARS - sum(len(l) for l in lines)
        for raw, reason in failed:
            entry = f"  • <code>{html.escape(raw)}</code> — {html.escape(reason)}"
            if len(entry) + 1 > budget:
                break
            lines.append(entry)
            budget -= len(entry) + 1
            shown += 1
        if shown < len(failed):
            lines.append(f"  ...и ещё {len(failed) - shown}")
    if truncated:
        lines.append(
            f"\n⚠️ В файле больше {_MAX_PROXY_LINES_PER_FILE} строк — "
            f"обработаны только первые {_MAX_PROXY_LINES_PER_FILE}."
        )
    report_text = "\n".join(lines)

    try:
        await status_msg.edit_text(report_text)
    except Exception:
        logger.exception("Failed to edit final proxy report message")
        try:
            await original_message.answer(report_text)
        except Exception:
            logger.exception("Fallback proxy report message also failed to send")


@router.message(_ProxyAwaitingFilter(), F.document)
async def handle_proxy_file(message: Message, bot: Bot) -> None:
    redis = get_redis()
    admin_id = message.from_user.id
    scheme = get_proxy_awaiting(admin_id, redis)
    if not scheme:
        return

    document = message.document
    if document.file_size and document.file_size > _MAX_PROXY_FILE_BYTES:
        await message.answer("⚠️ Файл слишком большой (лимит 512 КБ).")
        return

    try:
        buffer = await bot.download(document)
        content = buffer.read().decode("utf-8", errors="replace")
    except Exception:
        logger.exception("Failed to download proxy list file from admin %s", admin_id)
        await message.answer("❌ Не удалось прочитать файл. Попробуйте ещё раз.")
        return

    lines = [line.strip() for line in content.splitlines() if line.strip() and not line.strip().startswith("#")]
    if not lines:
        await message.answer(f"⚠️ Файл пустой.\n\n{_PROXY_FORMATS_HELP}")
        return

    truncated = len(lines) > _MAX_PROXY_LINES_PER_FILE
    lines = lines[:_MAX_PROXY_LINES_PER_FILE]

    total = len(lines)
    status_msg = await message.answer(
        f"🔍 Проверяю {total} прокси (до {_PROXY_CHECK_CONCURRENCY} одновременно)...\n"
        f"✅ Прошли: 0 | ⏭ Дубли: 0 | ❌ Провалили: 0 | Осталось: {total}"
    )
    semaphore = asyncio.Semaphore(_PROXY_CHECK_CONCURRENCY)
    existing_urls = await _existing_proxy_urls()
    dedup_lock = asyncio.Lock()
    progress = {"passed": 0, "duplicate": 0, "failed": 0}
    progress_lock = asyncio.Lock()
    last_edit_at = 0.0

    async def _bounded_check(raw: str) -> tuple[str, str | None, str, bool]:
        nonlocal last_edit_at
        async with semaphore:
            added_url, note, was_duplicate = await _check_and_add_proxy(
                raw, scheme, admin_id, existing_urls, dedup_lock
            )
        async with progress_lock:
            if added_url:
                progress["passed"] += 1
            elif was_duplicate:
                progress["duplicate"] += 1
            else:
                progress["failed"] += 1
            done = progress["passed"] + progress["duplicate"] + progress["failed"]
            now = time.monotonic()
            # Throttle edits to avoid Telegram rate limits on a large batch;
            # always send the final one so the count ends up accurate.
            if now - last_edit_at >= 3 or done == total:
                last_edit_at = now
                try:
                    await status_msg.edit_text(
                        f"🔍 Проверяю {total} прокси (до {_PROXY_CHECK_CONCURRENCY} одновременно)...\n"
                        f"✅ Прошли: {progress['passed']} | ⏭ Дубли: {progress['duplicate']} | "
                        f"❌ Провалили: {progress['failed']} | Осталось: {total - done}"
                    )
                except Exception:
                    pass  # message unchanged since last edit, or edited/deleted by the user — not fatal
        return raw, added_url, note, was_duplicate

    results = await asyncio.gather(*(_bounded_check(raw) for raw in lines))

    added: list[str] = []
    duplicates: list[str] = []
    failed: list[tuple[str, str]] = []
    for raw, added_url, note, was_duplicate in results:
        if added_url:
            added.append(added_url)
        elif was_duplicate:
            duplicates.append(raw)
        else:
            failed.append((raw, note))

    clear_proxy_awaiting(admin_id, redis)
    await _send_proxy_batch_report(status_msg, message, added, duplicates, failed, len(lines), truncated)


@router.message(_ProxyAwaitingFilter(), F.text)
async def handle_proxy_input(message: Message) -> None:
    redis = get_redis()
    admin_id = message.from_user.id
    scheme = get_proxy_awaiting(admin_id, redis)
    if not scheme:
        return

    text = (message.text or "").strip()
    if text.lower() in ("/cancel", "отмена"):
        clear_proxy_awaiting(admin_id, redis)
        await message.answer("❌ Добавление прокси отменено.")
        return

    status_msg = await message.answer("🔍 Проверяю прокси на YouTube (Sign in to confirm you're not a bot)...")
    existing_urls = await _existing_proxy_urls()
    added_url, note, was_duplicate = await _check_and_add_proxy(
        text, scheme, admin_id, existing_urls, asyncio.Lock()
    )
    if was_duplicate:
        clear_proxy_awaiting(admin_id, redis)
        await status_msg.edit_text("⏭ Этот прокси уже есть в базе — пропущен.")
        return
    if not added_url:
        await status_msg.edit_text(
            f"❌ Прокси не добавлен: {html.escape(note)}\n\nПопробуйте другой или /cancel."
        )
        return

    clear_proxy_awaiting(admin_id, redis)
    await status_msg.edit_text(f"✅ Прокси проверен и добавлен: <code>{html.escape(added_url)}</code>")
