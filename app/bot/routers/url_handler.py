from __future__ import annotations

import logging

from aiogram import F, Router
from aiogram.exceptions import TelegramBadRequest
from aiogram.types import Message
from celery import chain

from app.core.config import get_settings
from app.db.models import DownloadStatus
from app.db.repository import RequestRepository, TorrentRepository, UserRepository, VideoRepository
from app.db.session import get_session, run_in_session
from app.services.rate_limiter import RateLimiter, check_rate_limit
from app.services.redis_client import get_redis
from app.services.runtime_config import get_limit
from app.utils.caption import get_caption
from app.utils.magnet import extract_magnet_uri
from app.utils.quality import normalize_quality
from app.utils.rezka import build_selection_url, canonicalize_rezka_url, is_rezka_url
from app.utils.site_behavior import site_behavior
from app.utils.url_tools import extract_url, is_valid_url, normalize_url, url_hash
from app.worker.tasks import process_download_request, process_torrent_request

router = Router()
logger = logging.getLogger(__name__)

# Table-driven dispatch (mirrors telegram_sender._TYPE_SPECS): add a new
# TelegramFileType by extending this dict rather than another if/elif branch.
_ANSWER_METHOD_BY_FILE_TYPE = {
    "video": "answer_video",
    "audio": "answer_audio",
}


async def send_cached_file(message: Message, file_id: str, file_type: str, title: str | None = None) -> None:
    caption = get_caption(get_settings(), title)
    method_name = _ANSWER_METHOD_BY_FILE_TYPE.get(file_type, "answer_document")
    await getattr(message, method_name)(file_id, caption=caption)


async def enqueue_download(
    message: Message,
    user_id: int,
    chat_id: int,
    message_id: int | None,
    raw_url: str,
    normalized: str,
    quality_value: str,
) -> None:
    """Queue normalized for download, or serve it straight from cache.

    Shared by the plain-link flow below and app.bot.routers.rezka_flow's
    inline-keyboard translator/season/episode selection — the latter just
    builds a different normalized URL (via app.utils.rezka.build_selection_url)
    before calling this, so a specific rezka episode+voiceover combo gets
    its own url_hash and its own cache entry like any other video.
    """
    settings = get_settings()
    redis = get_redis()
    h = url_hash(normalized)

    with get_session() as session:
        req_repo = RequestRepository(session)
        video_repo = VideoRepository(session)

        daily_limit = get_limit("user_daily_limit", settings, redis)
        if daily_limit > 0 and req_repo.count_user_today_requests(user_id) >= daily_limit:
            await message.answer("⚠️ Дневной лимит запросов исчерпан.")
            return

        queue_limit = get_limit("user_queue_limit", settings, redis)
        if queue_limit > 0 and req_repo.count_user_active_requests(user_id) >= queue_limit:
            await message.answer("⚠️ У тебя слишком много активных задач в очереди.")
            return

        global_limit = get_limit("global_queue_limit", settings, redis)
        if global_limit > 0 and req_repo.count_global_active_requests() >= global_limit:
            await message.answer("⚠️ Сервер сейчас перегружен. Попробуй позже.")
            return

        ready_video = video_repo.get_ready_video(h, quality_value)
        if ready_video and ready_video.telegram_file_id and ready_video.telegram_file_type:
            caption_title = ready_video.title if site_behavior(normalized).caption_uses_resolved_title else None
            try:
                await send_cached_file(
                    message, ready_video.telegram_file_id, ready_video.telegram_file_type.value, caption_title
                )
                return
            except TelegramBadRequest:
                logger.warning(
                    "Cached file_id for video %s is invalid, clearing and re-queuing",
                    ready_video.id,
                )
                video_repo.invalidate_video_cache(ready_video.id)
                await message.answer("⚠️ Кэш устарел, скачиваю заново...")

        video = video_repo.get_or_create_video(
            original_url=raw_url,
            normalized_url=normalized,
            url_hash=h,
            quality=quality_value,
        )

        status_msg = await message.answer(
            "🧾 Задача добавлена в очередь.\n"
            f"Качество: {quality_value}"
        )

        req = req_repo.create_request(
            user_id=user_id,
            chat_id=chat_id,
            message_id=message_id,
            status_message_id=status_msg.message_id,
            video_id=video.id,
            original_url=raw_url,
            normalized_url=normalized,
            url_hash=h,
            quality=quality_value,
            status=DownloadStatus.queued,
        )

        task = process_download_request.delay(req.id)
        req_repo.set_request_task_id(req.id, task.id)


async def enqueue_torrent_download(
    message: Message,
    user_id: int,
    chat_id: int,
    message_id: int | None,
    magnet_uri: str,
) -> None:
    """Queue a magnet link for download.

    Unlike enqueue_download, there's no cache to check and no quality to
    pick - a torrent is an arbitrary set of files, sent once and then wiped
    (see app.worker.tasks.process_torrent_request), so every magnet link is
    a fresh job. Uses its own queue/limit counters (TorrentRepository)
    rather than RequestRepository's, since torrent jobs live in their own
    table.
    """
    settings = get_settings()
    redis = get_redis()

    with get_session() as session:
        repo = TorrentRepository(session)

        daily_limit = get_limit("user_daily_limit", settings, redis)
        if daily_limit > 0 and repo.count_user_today_requests(user_id) >= daily_limit:
            await message.answer("⚠️ Дневной лимит запросов исчерпан.")
            return

        queue_limit = get_limit("user_queue_limit", settings, redis)
        if queue_limit > 0 and repo.count_user_active_requests(user_id) >= queue_limit:
            await message.answer("⚠️ У тебя слишком много активных задач в очереди.")
            return

        global_limit = get_limit("global_queue_limit", settings, redis)
        if global_limit > 0 and repo.count_global_active_requests() >= global_limit:
            await message.answer("⚠️ Сервер сейчас перегружен. Попробуй позже.")
            return

        status_msg = await message.answer("🧲 Магнет-ссылка добавлена в очередь.")

        req = repo.create_request(
            user_id=user_id,
            chat_id=chat_id,
            message_id=message_id,
            status_message_id=status_msg.message_id,
            magnet_uri=magnet_uri,
            status=DownloadStatus.queued,
        )

        task = process_torrent_request.delay(req.id)
        repo.set_request_task_id(req.id, task.id)


async def enqueue_season_download(
    message: Message,
    user_id: int,
    chat_id: int,
    message_id: int | None,
    raw_url: str,
    base_url: str,
    translator_id: int,
    season: int,
    episodes: list[int],
    quality_value: str,
) -> None:
    """Queue every episode in episodes (already sorted ascending) for one
    translator/season, one DownloadRequest/Video row per episode — exactly
    like clicking each episode individually, so a re-run serves already-
    downloaded episodes straight from their cached Telegram file_id via
    process_download_request's own cache check.

    The requests are chained with Celery's chain() rather than fired off
    with N separate .delay() calls, so episode 2 only starts once episode
    1's request has fully finished (downloaded+sent, or served from cache)
    — "step by step" as asked, and it sidesteps the per-user active-request
    queue limit, which would otherwise reject most of a season's requests
    outright since they'd all be sitting "queued" at once.
    """
    if not episodes:
        await message.answer("⚠️ В этом сезоне нет серий для этой озвучки.")
        return

    settings = get_settings()
    redis = get_redis()

    with get_session() as session:
        req_repo = RequestRepository(session)
        video_repo = VideoRepository(session)

        daily_limit = get_limit("user_daily_limit", settings, redis)
        if daily_limit > 0:
            remaining = daily_limit - req_repo.count_user_today_requests(user_id)
            if remaining <= 0:
                await message.answer("⚠️ Дневной лимит запросов исчерпан.")
                return
            episodes = episodes[:remaining]

        global_limit = get_limit("global_queue_limit", settings, redis)
        if global_limit > 0 and req_repo.count_global_active_requests() >= global_limit:
            await message.answer("⚠️ Сервер сейчас перегружен. Попробуй позже.")
            return

        # One INSERT/UPDATE per episode here would mean 3xN DB round-trips
        # for a whole season (up to 20-24 episodes); batch each step into a
        # single round-trip instead. The Telegram "queued" messages still go
        # out one per episode (unavoidable - Telegram has no bulk-send), so
        # that part stays a loop; only the DB calls are batched afterwards.
        episode_urls = [build_selection_url(base_url, translator_id, season, ep) for ep in episodes]
        episode_hashes = [url_hash(u) for u in episode_urls]
        status_message_ids = [
            (await message.answer(f"🧾 Серия {ep}: добавлена в очередь сезона.")).message_id
            for ep in episodes
        ]

        videos_by_key = video_repo.get_or_create_videos_bulk([
            {
                "original_url": raw_url,
                "normalized_url": final_url,
                "url_hash": h,
                "quality": quality_value,
            }
            for final_url, h in zip(episode_urls, episode_hashes)
        ])

        created = req_repo.create_requests_bulk([
            {
                "user_id": user_id,
                "chat_id": chat_id,
                "message_id": message_id,
                "status_message_id": status_message_id,
                "video_id": videos_by_key[(h, quality_value)].id,
                "original_url": raw_url,
                "normalized_url": final_url,
                "url_hash": h,
                "quality": quality_value,
                "status": DownloadStatus.queued,
            }
            for final_url, h, status_message_id in zip(episode_urls, episode_hashes, status_message_ids)
        ])

        if not created:
            return

        request_ids = [req.id for req in created]
        signatures = [process_download_request.si(rid) for rid in request_ids]
        req_repo.set_request_task_ids_bulk({
            rid: sig.freeze().id for rid, sig in zip(request_ids, signatures)
        })

    chain(*signatures).apply_async()


# Must be the last router included — these are the most generic handlers
# and will swallow any message that hasn't been matched by earlier routers.

@router.message(F.text)
async def handle_link(message: Message) -> None:
    await _process_url_message(message, message.text or "", reply_on_no_url=True)


@router.message(F.caption)
async def handle_caption_link(message: Message) -> None:
    await _process_url_message(message, message.caption or "", reply_on_no_url=False)


async def _process_url_message(message: Message, text: str, reply_on_no_url: bool) -> None:
    settings = get_settings()
    redis = get_redis()
    limiter = RateLimiter(redis)

    user_id = message.from_user.id

    allowed, ban_ttl = check_rate_limit(user_id, settings, redis, limiter)
    if not allowed:
        await message.answer(f"⛔ Слишком много сообщений. Временный бан: {ban_ttl} сек.")
        return

    magnet_uri = extract_magnet_uri(text)
    if magnet_uri:
        await run_in_session(lambda session: UserRepository(session).upsert_user(
            user_id=user_id,
            username=message.from_user.username,
            first_name=message.from_user.first_name,
        ))
        await enqueue_torrent_download(message, user_id, message.chat.id, message.message_id, magnet_uri)
        return

    raw_url = extract_url(text)
    if not raw_url:
        if reply_on_no_url:
            await message.answer("Пришли обычную ссылку на видео.")
        return

    if not is_valid_url(raw_url):
        await message.answer("Ссылка выглядит некорректно.")
        return

    normalized = normalize_url(raw_url)
    quality_value = redis.get(f"user_quality:{user_id}") or settings.default_quality
    quality_value = normalize_quality(quality_value, settings.default_quality)

    await run_in_session(lambda session: UserRepository(session).upsert_user(
        user_id=user_id,
        username=message.from_user.username,
        first_name=message.from_user.first_name,
    ))

    if is_rezka_url(normalized):
        # rezka.ag needs a translator (and, for series, season/episode)
        # picked via inline buttons before there's even a URL worth
        # queuing — see app.bot.routers.rezka_flow. canonicalize_rezka_url
        # strips any extra sub-segment (e.g. a translator-specific link
        # someone shared) back to the title's own page first.
        from app.bot.routers.rezka_flow import start_rezka_flow

        canonical = canonicalize_rezka_url(normalized)
        await start_rezka_flow(message, raw_url, canonical, quality_value)
        return

    await enqueue_download(message, user_id, message.chat.id, message.message_id, raw_url, normalized, quality_value)
