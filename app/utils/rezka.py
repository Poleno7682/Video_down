from __future__ import annotations

import re

from HdRezkaApi import HdRezkaApi
from HdRezkaApi.types import Movie

_URL_RE = re.compile(r'^https?://h?d?rezka(?:-ua)?\..*/\d+-[^/]+-\d+(?:-.*)?\.html', re.IGNORECASE)

# Preference order when the requested quality (e.g. "720p") isn't offered
# for this title — highest available wins.
_QUALITY_FALLBACK_ORDER = ["1080p Ultra", "1080p", "720p", "480p", "360p"]

# HdRezkaApi's own default User-Agent is a Chrome build from 2020 — a
# plausible bot-detection trigger on its own. Use a current one, matching
# what app.worker.downloader's browser-retry fallback already uses.
_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "en-US,en;q=0.9",
}


class RezkaResolveError(RuntimeError):
    """Could not resolve a direct stream URL for a rezka.ag page."""


def is_rezka_url(url: str) -> bool:
    return bool(_URL_RE.match(url))


def _closest_quality(available: list[str], quality: str) -> str:
    if quality in available:
        return quality
    for candidate in _QUALITY_FALLBACK_ORDER:
        if candidate in available:
            return candidate
    return available[0]


def resolve_rezka_stream(url: str, quality: str, proxy: str | None = None) -> tuple[str, str]:
    """Resolve a rezka.ag/hdrezka.* movie page to a direct video URL.

    Returns (direct_url, title). Picks the first/priority translator
    (voiceover) automatically — the worker runs unattended so there's no
    way to ask which one to use. Only movies are supported: a TV series
    page needs a season/episode the bot has no UI to collect, so it fails
    with a clear message instead of guessing.
    """
    proxies = {"http": proxy, "https": proxy} if proxy else {}
    rezka = HdRezkaApi(url, proxy=proxies, headers=_HEADERS)
    if not rezka.ok:
        raise RezkaResolveError(f"Не удалось открыть страницу rezka: {rezka.exception}")

    try:
        content_type = rezka.type
    except Exception as exc:
        # The page loaded (rezka.ok passed) but doesn't have the markup a
        # real movie/series page has — most likely an anti-bot/CAPTCHA
        # interstitial or a redirect that HdRezkaApi's own Sign-In/Verify
        # title check doesn't catch. Surface the actual page title so the
        # real cause shows up in logs instead of a bare AttributeError.
        page_title = None
        try:
            title_tag = rezka.soup.title
            page_title = title_tag.get_text(strip=True) if title_tag else None
        except Exception:
            pass
        hint = f" (заголовок страницы: {page_title!r})" if page_title else ""
        raise RezkaResolveError(f"Страница rezka не похожа на страницу фильма{hint}: {exc}") from exc

    if content_type != Movie:
        raise RezkaResolveError(
            "Сериалы с rezka.ag пока не поддерживаются — нужно выбрать сезон и серию."
        )

    try:
        stream = rezka.getStream()
    except Exception as exc:
        raise RezkaResolveError(f"Не удалось получить поток видео: {exc}") from exc

    available = list(stream.videos.keys())
    if not available:
        raise RezkaResolveError("Для этого перевода нет доступных потоков видео.")

    target_quality = _closest_quality(available, quality)
    return stream.videos[target_quality][0], rezka.name
