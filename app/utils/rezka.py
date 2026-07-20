from __future__ import annotations

import json
import re
import urllib.parse
from dataclasses import dataclass

from HdRezkaApi import HdRezkaApi
from HdRezkaApi.types import Movie, TVSeries

# Query params appended to the base rezka URL (after normalize_url, which
# only strips known tracking params) to carry the admin/user's translator
# (voiceover) + season/episode choice from the bot's inline-keyboard flow
# through to the worker — and, since they change the URL, naturally give
# each distinct selection its own url_hash in the existing Video/
# DownloadRequest cache, so re-requesting the exact same episode+voiceover
# is served from Telegram's cached file_id without downloading again.
_PARAM_TRANSLATOR = "rezka_tr"
_PARAM_SEASON = "rezka_s"
_PARAM_EPISODE = "rezka_e"

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


_TITLE_SEGMENT_RE = re.compile(r'^\d+-[^/]+$')


def canonicalize_rezka_url(url: str) -> str:
    """Strip any extra path segment appended after the title's own
    <id>-<slug>[-suffix] segment, e.g.:

        .../2136-rik-i-morti-2013-latest/66-syenduk.html
        -> .../2136-rik-i-morti-2013-latest.html

    Some rezka.ag pages (seen on multi-season "franchise" titles like Rick
    and Morty) get shared with a trailing translator/voiceover sub-segment.
    HdRezkaApi's own url.split(".html")[0] + ".html" normalization doesn't
    catch this shape (there's only one ".html" in the whole URL — it's the
    filename, not an earlier segment to truncate at) and the translator
    list markup it parses isn't even present on that sub-page, so this
    needs to happen on our side before HdRezkaApi ever sees the URL.
    """
    parsed = urllib.parse.urlparse(url)
    segments = parsed.path.split("/")
    for i, segment in enumerate(segments[:-1]):
        if _TITLE_SEGMENT_RE.match(segment):
            new_path = "/".join(segments[: i + 1]) + ".html"
            return urllib.parse.urlunparse(parsed._replace(path=new_path))
    return url


def _closest_quality(available: list[str], quality: str) -> str:
    if quality in available:
        return quality
    for candidate in _QUALITY_FALLBACK_ORDER:
        if candidate in available:
            return candidate
    return available[0]


def build_selection_url(
    base_url: str,
    translator_id: int | None,
    season: int | None = None,
    episode: int | None = None,
) -> str:
    """Append the chosen translator/season/episode to base_url as query
    params. HdRezkaApi strips everything after ".html" itself when it
    builds its own requests, so this is purely our own encoding — see the
    module-level comment on the _PARAM_* constants for why."""
    params = {}
    if translator_id is not None:
        params[_PARAM_TRANSLATOR] = translator_id
    if season is not None:
        params[_PARAM_SEASON] = season
    if episode is not None:
        params[_PARAM_EPISODE] = episode
    if not params:
        return base_url
    separator = "&" if "?" in base_url else "?"
    return base_url + separator + urllib.parse.urlencode(params)


def _parse_selection(url: str) -> tuple[int | None, int | None, int | None]:
    query = urllib.parse.parse_qs(urllib.parse.urlparse(url).query)

    def _first_int(key: str) -> int | None:
        values = query.get(key)
        return int(values[0]) if values else None

    return _first_int(_PARAM_TRANSLATOR), _first_int(_PARAM_SEASON), _first_int(_PARAM_EPISODE)


def seasons_for_translator(episodes_info: list[dict], translator_id: int) -> list[int]:
    seasons = set()
    for season_entry in episodes_info:
        for ep in season_entry["episodes"]:
            if any(tr["translator_id"] == translator_id for tr in ep["translations"]):
                seasons.add(season_entry["season"])
    return sorted(seasons)


def episodes_for_translator_season(episodes_info: list[dict], translator_id: int, season: int) -> list[int]:
    for season_entry in episodes_info:
        if season_entry["season"] != season:
            continue
        return sorted(
            ep["episode"] for ep in season_entry["episodes"]
            if any(tr["translator_id"] == translator_id for tr in ep["translations"])
        )
    return []


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


def _open_rezka_session(url: str, proxy: str | None, bypass_antibot: bool, redis):
    """Open url via HdRezkaApi, transparently handling the antibot bypass +
    cookie cache + one stale-cache retry. Returns (rezka_api, content_type).

    Shared by resolve_rezka_stream (getting the actual video stream) and
    get_rezka_content_info (listing translators/seasons/episodes for the
    bot's inline-keyboard flow) — both need the exact same "get past the
    challenge" dance first.
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
        return _open_movie_page(url, proxies, headers, cookies)
    except _NotMoviePageError as exc:
        if not (bypass_antibot and used_cached_cookies):
            raise _page_diagnostic_error(exc.rezka, exc.cause) from exc.cause
        # Cached cookies didn't work — solve fresh once and retry before
        # giving up, instead of failing on a cache entry that just expired.
        _clear_cached_cookies(redis, domain)
        cookies = _solve_challenge_with_browser(url)
        _store_cached_cookies(redis, domain, cookies)
        try:
            return _open_movie_page(url, proxies, headers, cookies)
        except _NotMoviePageError as exc2:
            raise _page_diagnostic_error(exc2.rezka, exc2.cause) from exc2.cause


def resolve_rezka_stream(
    url: str,
    quality: str,
    proxy: str | None = None,
    bypass_antibot: bool = False,
    redis=None,
) -> tuple[str, str]:
    """Resolve a rezka.ag/hdrezka.* page to a direct video URL.

    Returns (direct_url, title). url may carry a translator/season/episode
    selection appended by build_selection_url() (see the bot's inline-
    keyboard flow in app.bot.routers.rezka_flow) — without one, the first/
    priority translator is used automatically and a series page fails with
    a clear message, since there'd be no season/episode to pick.

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
    translator_id, season, episode = _parse_selection(url)
    rezka, content_type = _open_rezka_session(url, proxy, bypass_antibot, redis)

    if content_type == Movie:
        if season is not None or episode is not None:
            raise RezkaResolveError("Это фильм, а не сериал — сезон/серия не применимы.")
        try:
            stream = rezka.getStream(translation=translator_id) if translator_id is not None else rezka.getStream()
        except Exception as exc:
            raise RezkaResolveError(f"Не удалось получить поток видео: {exc}") from exc
    elif content_type == TVSeries:
        if season is None or episode is None:
            raise RezkaResolveError(
                "Сериалы с rezka.ag требуют выбора сезона и серии — используйте /-ссылку через бота, "
                "а не пересылайте её напрямую."
            )
        try:
            stream = rezka.getStream(season=season, episode=episode, translation=translator_id)
        except Exception as exc:
            raise RezkaResolveError(f"Не удалось получить поток видео: {exc}") from exc
    else:
        raise RezkaResolveError("Неизвестный тип контента на странице rezka.")

    available = list(stream.videos.keys())
    if not available:
        raise RezkaResolveError("Для этого перевода нет доступных потоков видео.")

    target_quality = _closest_quality(available, quality)
    return stream.videos[target_quality][0], rezka.name


@dataclass
class RezkaContentInfo:
    title: str
    is_series: bool
    # translator_id -> display name (voiceover/studio), in HdRezkaApi's own
    # priority order.
    translators: dict[int, str]
    # Only set when is_series — HdRezkaApi's raw episodesInfo structure:
    # [{"season": int, "episodes": [{"episode": int, "translations": [...]}]}]
    episodes_info: list[dict] | None = None

    def seasons_for_translator(self, translator_id: int) -> list[int]:
        return seasons_for_translator(self.episodes_info or [], translator_id)

    def episodes_for(self, translator_id: int, season: int) -> list[int]:
        return episodes_for_translator_season(self.episodes_info or [], translator_id, season)


def get_rezka_content_info(
    url: str,
    bypass_antibot: bool = False,
    redis=None,
) -> RezkaContentInfo:
    """Fetch a rezka.ag/hdrezka.* page's translators (and, for a series,
    its season/episode listing) — the data the bot's inline-keyboard flow
    needs before it can even ask the user which voiceover/episode to
    download. Goes through the same antibot bypass as resolve_rezka_stream.
    """
    rezka, content_type = _open_rezka_session(url, None, bypass_antibot, redis)
    try:
        translators = {tr_id: info["name"] for tr_id, info in rezka.translators.items()}
    except Exception as exc:
        # Seen in production on a franchise-style URL (a show's ".../<id>-
        # <slug>-latest/<n>-<translator-slug>.html" page) whose translator
        # list markup HdRezkaApi's own .translators parser doesn't expect —
        # surfacing this as a clear message beats a raw KeyError/AttributeError.
        raise RezkaResolveError(
            f"Не удалось получить список озвучек для этой страницы rezka: {exc}. "
            "Попробуйте прислать основную ссылку на тайтл, без дополнительных сегментов в адресе."
        ) from exc

    if content_type == Movie:
        return RezkaContentInfo(title=rezka.name, is_series=False, translators=translators)
    if content_type == TVSeries:
        try:
            episodes_info = rezka.episodesInfo
        except Exception as exc:
            raise RezkaResolveError(f"Не удалось получить список серий: {exc}") from exc
        return RezkaContentInfo(
            title=rezka.name, is_series=True, translators=translators, episodes_info=episodes_info
        )
    raise RezkaResolveError("Неизвестный тип контента на странице rezka.")
