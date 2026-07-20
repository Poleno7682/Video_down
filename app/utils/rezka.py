from __future__ import annotations

import json
import re
import urllib.parse

from HdRezkaApi import HdRezkaApi
from HdRezkaApi.types import Movie

_URL_RE = re.compile(r'^https?://h?d?rezka(?:-ua)?\..*/\d+-[^/]+-\d+(?:-.*)?\.html', re.IGNORECASE)

# Preference order when the requested quality (e.g. "720p") isn't offered
# for this title — highest available wins.
_QUALITY_FALLBACK_ORDER = ["1080p Ultra", "1080p", "720p", "480p", "360p"]

# HdRezkaApi's own default User-Agent is a Chrome build from 2020 — a
# plausible bot-detection trigger on its own. Use a current one, matching
# what app.worker.downloader's browser-retry fallback already uses. The
# headless browser (when the anti-bot bypass is enabled) is also launched
# with this same UA, since cookies earned under one UA can get rejected by
# a server checking they're paired with the UA that requested them.
_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "en-US,en;q=0.9",
}

# The title rezka.ag's anti-bot interstitial (Anubis — a proof-of-work
# challenge, not a Cloudflare-style managed challenge) shows while its
# client-side JS is still computing the proof of work.
_CHALLENGE_TITLE = "Проверяем, что вы не бот!"

_CHALLENGE_TIMEOUT_MS = 30_000

# How long a solved challenge's cookies are trusted before re-solving
# anyway, even if they still work — a conservative middle ground between
# "solve on every single download" (what made this slow enough to need
# caching) and trusting them indefinitely (they will eventually expire or
# get rotated server-side; better to refresh proactively than repeatedly
# fail with a stale cookie first).
_COOKIE_CACHE_TTL_SECONDS = 6 * 3600
_COOKIE_CACHE_PREFIX = "rezka_antibot_cookies:"


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


def _cache_key(domain: str) -> str:
    return f"{_COOKIE_CACHE_PREFIX}{domain}"


def _load_cached_cookies(redis, domain: str) -> dict[str, str] | None:
    if redis is None:
        return None
    raw = redis.get(_cache_key(domain))
    if not raw:
        return None
    try:
        return json.loads(raw)
    except (TypeError, ValueError):
        return None


def _store_cached_cookies(redis, domain: str, cookies: dict[str, str]) -> None:
    if redis is None or not cookies:
        return
    redis.setex(_cache_key(domain), _COOKIE_CACHE_TTL_SECONDS, json.dumps(cookies))


def _clear_cached_cookies(redis, domain: str) -> None:
    if redis is not None:
        redis.delete(_cache_key(domain))


def _solve_challenge_with_browser(url: str, timeout_ms: int = _CHALLENGE_TIMEOUT_MS) -> dict[str, str]:
    """Load url in a real headless Chromium so its anti-bot JS challenge
    actually runs and resolves, returning the cookies it earned for plain
    `requests` calls (what HdRezkaApi itself makes) to reuse afterwards.

    Anubis's proof-of-work challenge has no separate solver service to
    delegate to the way a Cloudflare managed challenge does (FlareSolverr,
    tried first, doesn't even recognize it as a challenge type it handles)
    — nothing but actually running the real client-side JS computes the
    right answer, so a genuine browser engine is unavoidable here.
    """
    # Imported lazily: playwright + its browser binary are a heavy,
    # optional dependency only needed when the antibot bypass is actually
    # enabled — no reason to require them just to import this module.
    from playwright.sync_api import TimeoutError as PlaywrightTimeoutError
    from playwright.sync_api import sync_playwright

    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True, args=["--no-sandbox"])
            try:
                context = browser.new_context(user_agent=_HEADERS["User-Agent"])
                page = context.new_page()
                page.goto(url, timeout=timeout_ms, wait_until="domcontentloaded")
                page.wait_for_function(
                    "(title) => document.title !== title",
                    arg=_CHALLENGE_TITLE,
                    timeout=timeout_ms,
                )
                # Best-effort only: some sites keep a background connection
                # open forever (analytics, ads) so "network idle" never
                # actually fires. The title change above already confirms
                # the challenge passed and the real page loaded — a
                # timeout here just means we grab cookies a beat earlier,
                # not that anything failed.
                try:
                    page.wait_for_load_state("networkidle", timeout=5_000)
                except PlaywrightTimeoutError:
                    pass
                return {c["name"]: c["value"] for c in context.cookies()}
            finally:
                browser.close()
    except PlaywrightTimeoutError as exc:
        raise RezkaResolveError(
            f"Не удалось пройти антибот-проверку rezka за отведённое время: {exc}"
        ) from exc
    except Exception as exc:
        raise RezkaResolveError(f"Ошибка headless-браузера при обходе антибот-проверки: {exc}") from exc


class _NotMoviePageError(Exception):
    """rezka.type raised — carries the api object along so the caller can
    still build a diagnostic message (page title) from it, and decide
    whether the failure is worth retrying with freshly-solved cookies."""

    def __init__(self, rezka, cause: Exception) -> None:
        self.rezka = rezka
        self.cause = cause
        super().__init__(str(cause))


def _open_movie_page(url: str, proxies: dict, headers: dict, cookies: dict):
    """Fetch url via HdRezkaApi and return (api, content_type).

    Raises RezkaResolveError if the page couldn't be fetched at all, or
    _NotMoviePageError if it fetched but doesn't look like a movie/series
    page (most likely the anti-bot interstitial) — kept distinct from
    RezkaResolveError so callers can retry only the latter case.
    """
    rezka = HdRezkaApi(url, proxy=proxies, headers=headers, cookies=cookies)
    if not rezka.ok:
        raise RezkaResolveError(f"Не удалось открыть страницу rezka: {rezka.exception}")
    try:
        content_type = rezka.type
    except Exception as exc:
        raise _NotMoviePageError(rezka, exc) from exc
    return rezka, content_type


def _page_diagnostic_error(rezka, exc: Exception) -> RezkaResolveError:
    # The page loaded (rezka.ok passed) but doesn't have the markup a real
    # movie/series page has — most likely the anti-bot interstitial (bypass
    # off, the bypass itself failed silently, or cached cookies went stale)
    # or a redirect HdRezkaApi's own Sign-In/Verify title check doesn't
    # catch. Surface the actual page title so the real cause shows up in
    # logs instead of a bare AttributeError.
    page_title = None
    try:
        title_tag = rezka.soup.title
        page_title = title_tag.get_text(strip=True) if title_tag else None
    except Exception:
        pass
    hint = f" (заголовок страницы: {page_title!r})" if page_title else ""
    return RezkaResolveError(f"Страница rezka не похожа на страницу фильма{hint}: {exc}")


def resolve_rezka_stream(
    url: str,
    quality: str,
    proxy: str | None = None,
    bypass_antibot: bool = False,
    redis=None,
) -> tuple[str, str]:
    """Resolve a rezka.ag/hdrezka.* movie page to a direct video URL.

    Returns (direct_url, title). Picks the first/priority translator
    (voiceover) automatically — the worker runs unattended so there's no
    way to ask which one to use. Only movies are supported: a TV series
    page needs a season/episode the bot has no UI to collect, so it fails
    with a clear message instead of guessing.

    rezka.ag sits behind an Anubis proof-of-work JS challenge ("Проверяем,
    что вы не бот!") that a plain HTTP request can never pass on its own.
    bypass_antibot, when True, drives a real headless browser through that
    challenge and reuses the cookies it earns for HdRezkaApi's own plain
    requests calls. Solving it takes 10-30+ seconds, so when redis is
    given the resulting cookies are cached per-domain — most downloads
    then skip the browser entirely. A cache hit that turns out to be
    stale (cookie expired/rotated server-side) is detected the same way
    a cold solve's result would be and triggers exactly one fresh solve
    before giving up, so a stale cache entry costs one retry, not a
    failure.
    """
    headers = dict(_HEADERS)
    proxies = {"http": proxy, "https": proxy} if proxy else {}
    domain = urllib.parse.urlparse(url).hostname or ""

    cookies: dict[str, str] = {}
    used_cached_cookies = False
    if bypass_antibot:
        cached = _load_cached_cookies(redis, domain)
        if cached:
            cookies, used_cached_cookies = cached, True
        else:
            cookies = _solve_challenge_with_browser(url)
            _store_cached_cookies(redis, domain, cookies)

    try:
        rezka, content_type = _open_movie_page(url, proxies, headers, cookies)
    except _NotMoviePageError as exc:
        if not (bypass_antibot and used_cached_cookies):
            raise _page_diagnostic_error(exc.rezka, exc.cause) from exc.cause
        # Cached cookies didn't work — solve fresh once and retry before
        # giving up, instead of failing on a cache entry that just expired.
        _clear_cached_cookies(redis, domain)
        cookies = _solve_challenge_with_browser(url)
        _store_cached_cookies(redis, domain, cookies)
        try:
            rezka, content_type = _open_movie_page(url, proxies, headers, cookies)
        except _NotMoviePageError as exc2:
            raise _page_diagnostic_error(exc2.rezka, exc2.cause) from exc2.cause

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
