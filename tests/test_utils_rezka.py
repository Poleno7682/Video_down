from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import pytest

from app.utils.rezka import (
    RezkaResolveError,
    build_selection_url,
    canonicalize_rezka_url,
    get_rezka_content_info,
    is_rezka_url,
    resolve_rezka_stream,
)
from app.utils.rezka import _display_title, _open_movie_page, _parse_selection, _solve_challenge_with_browser


def test_is_rezka_url_matches_films():
    assert is_rezka_url("https://rezka.ag/films/detective/807-advokat-dyavola-1997.html")


def test_is_rezka_url_matches_hdrezka_domain():
    assert is_rezka_url("https://hdrezka.me/series/drama/12345-some-show-2020.html")


def test_is_rezka_url_rejects_unrelated_url():
    assert not is_rezka_url("https://www.youtube.com/watch?v=dQw4w9WgXcQ")


def test_is_rezka_url_matches_mirror_domains():
    """Regression guard: production used rezka-ag.net (a mirror domain,
    presumably because rezka.ag was blocked in their region) — the old
    strict-prefix regex only recognized rezka.ag/hdrezka.*/rezka-ua.* and
    silently let this fall through to the plain (non-rezka) download path."""
    assert is_rezka_url("https://rezka-ag.net/films/drama/763-forrest-gamp-1994.html")
    assert is_rezka_url("https://rezka-ua.tv/films/x/1-y-2020.html")


# ---------------------------------------------------------------------------
# canonicalize_rezka_url
# ---------------------------------------------------------------------------

def test_canonicalize_rezka_url_strips_extra_segment():
    """Regression guard: production hit a raw KeyError trying to parse
    translators off a franchise-style sub-page — the fix is to never send
    HdRezkaApi that sub-page URL in the first place."""
    url = canonicalize_rezka_url(
        "https://rezka.ag/cartoons/comedy/2136-rik-i-morti-2013-latest/66-syenduk.html"
    )
    assert url == "https://rezka.ag/cartoons/comedy/2136-rik-i-morti-2013-latest.html"


def test_canonicalize_rezka_url_leaves_normal_url_unchanged():
    url = "https://rezka.ag/films/detective/807-advokat-dyavola-1997.html"
    assert canonicalize_rezka_url(url) == url


def test_canonicalize_rezka_url_leaves_series_url_unchanged():
    url = "https://rezka.ag/series/drama/12345-some-show-2020.html"
    assert canonicalize_rezka_url(url) == url


def _mock_movie_api(videos: dict[str, list[str]], name: str = "Advocate of the Devil"):
    from HdRezkaApi.types import Movie

    mock_stream = MagicMock()
    mock_stream.videos = videos

    mock_api = MagicMock()
    mock_api.ok = True
    mock_api.type = Movie()
    mock_api.name = name
    mock_api.translators = {}
    mock_api.releaseYear = None
    mock_api.getStream.return_value = mock_stream
    return mock_api


def test_resolve_rezka_stream_picks_exact_quality_match():
    mock_api = _mock_movie_api({"360p": ["u360"], "720p": ["u720"], "1080p": ["u1080"]})
    with patch("app.utils.rezka.HdRezkaApi", return_value=mock_api):
        url, title = resolve_rezka_stream("https://rezka.ag/films/x/1-y-2020.html", "720p")
    assert url == "u720"
    assert title == "Advocate of the Devil"


def test_resolve_rezka_stream_falls_back_to_closest_lower_quality():
    mock_api = _mock_movie_api({"360p": ["u360"], "480p": ["u480"]})
    with patch("app.utils.rezka.HdRezkaApi", return_value=mock_api):
        url, _ = resolve_rezka_stream("https://rezka.ag/films/x/1-y-2020.html", "1080p")
    assert url == "u480"


def test_resolve_rezka_stream_raises_when_page_fails_to_load():
    mock_api = MagicMock()
    mock_api.ok = False
    mock_api.exception = RuntimeError("blocked")
    with patch("app.utils.rezka.HdRezkaApi", return_value=mock_api):
        with pytest.raises(RezkaResolveError, match="Не удалось открыть"):
            resolve_rezka_stream("https://rezka.ag/films/x/1-y-2020.html", "720p")


def test_resolve_rezka_stream_reports_page_title_when_not_a_movie_page():
    """Regression guard: production hit a bare AttributeError when the page
    HdRezkaApi fetched (rezka.ok passed) had no og:type meta tag — e.g. an
    anti-bot interstitial neither HdRezkaApi's own Sign-In/Verify title
    check nor our rezka.ok check caught. Must surface the page title
    instead of crashing with AttributeError."""
    class _FakeTitle:
        @staticmethod
        def get_text(strip=True):
            return "Just a moment..."

    class _FakeSoup:
        title = _FakeTitle()

    class _FakeApi:
        ok = True
        soup = _FakeSoup()

        @property
        def type(self):
            raise AttributeError("no og:type")

    with patch("app.utils.rezka.HdRezkaApi", return_value=_FakeApi()):
        with pytest.raises(RezkaResolveError, match="Just a moment"):
            resolve_rezka_stream("https://rezka.ag/films/x/1-y-2020.html", "720p")


def test_resolve_rezka_stream_passes_modern_user_agent():
    mock_api = _mock_movie_api({"720p": ["u720"]})
    with patch("app.utils.rezka.HdRezkaApi", return_value=mock_api) as mock_cls:
        resolve_rezka_stream("https://rezka.ag/films/x/1-y-2020.html", "720p")
    _, kwargs = mock_cls.call_args
    assert "Chrome" in kwargs["headers"]["User-Agent"]


def test_resolve_rezka_stream_rejects_series():
    from HdRezkaApi.types import TVSeries

    mock_api = MagicMock()
    mock_api.ok = True
    mock_api.type = TVSeries()
    with patch("app.utils.rezka.HdRezkaApi", return_value=mock_api):
        with pytest.raises(RezkaResolveError, match="Сериалы"):
            resolve_rezka_stream("https://rezka.ag/series/x/1-y-2020.html", "720p")


def test_resolve_rezka_stream_raises_when_get_stream_fails():
    mock_api = _mock_movie_api({})
    mock_api.getStream.side_effect = Exception("network error")
    with patch("app.utils.rezka.HdRezkaApi", return_value=mock_api):
        with pytest.raises(RezkaResolveError, match="Не удалось получить поток"):
            resolve_rezka_stream("https://rezka.ag/films/x/1-y-2020.html", "720p")


def test_resolve_rezka_stream_get_stream_failure_logs_diagnostic_when_translator_known(caplog):
    """Regression guard: production hit HdRezkaApi.errors.FetchFailed
    ("Failed to fetch stream!") with zero detail about *why* the site's
    /ajax/get_cdn_series/ endpoint returned success=false. When the
    translator_id is known, resolve_rezka_stream should re-issue the same
    request itself purely to log the raw response for diagnosis."""
    import logging

    mock_api = _mock_movie_api({})
    mock_api.id = 3356
    mock_api.origin = "https://rezka.ag"
    mock_api.HEADERS = {"User-Agent": "x"}
    mock_api.proxy = {}
    mock_api.cookies = {}
    mock_api.getStream.side_effect = Exception("Failed to fetch stream!")

    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.text = '{"success": false, "message": "premium required"}'

    url = build_selection_url("https://rezka.ag/films/x/1-y-2020.html", 80)
    with caplog.at_level(logging.INFO, logger="app.utils.rezka"), \
         patch("app.utils.rezka.HdRezkaApi", return_value=mock_api), \
         patch("app.utils.rezka.requests.post", return_value=mock_response) as mock_post:
        with pytest.raises(RezkaResolveError, match="Не удалось получить поток"):
            resolve_rezka_stream(url, "720p")

    mock_post.assert_called_once()
    assert mock_post.call_args.kwargs["data"]["translator_id"] == 80
    assert mock_post.call_args.kwargs["data"]["action"] == "get_movie"
    assert "premium required" in caplog.text


def test_resolve_rezka_stream_get_stream_failure_skips_diagnostic_without_translator_id():
    mock_api = _mock_movie_api({})
    mock_api.getStream.side_effect = Exception("Failed to fetch stream!")
    with patch("app.utils.rezka.HdRezkaApi", return_value=mock_api), \
         patch("app.utils.rezka.requests.post") as mock_post:
        with pytest.raises(RezkaResolveError):
            resolve_rezka_stream("https://rezka.ag/films/x/1-y-2020.html", "720p")
    mock_post.assert_not_called()


def test_resolve_rezka_stream_raises_when_no_videos_available():
    mock_api = _mock_movie_api({})
    with patch("app.utils.rezka.HdRezkaApi", return_value=mock_api):
        with pytest.raises(RezkaResolveError, match="нет доступных потоков"):
            resolve_rezka_stream("https://rezka.ag/films/x/1-y-2020.html", "720p")


def test_resolve_rezka_stream_passes_proxy_through():
    mock_api = _mock_movie_api({"720p": ["u720"]})
    with patch("app.utils.rezka.HdRezkaApi", return_value=mock_api) as mock_cls:
        resolve_rezka_stream("https://rezka.ag/films/x/1-y-2020.html", "720p", proxy="http://p:8080")
    _, kwargs = mock_cls.call_args
    assert kwargs["proxy"] == {"http": "http://p:8080", "https": "http://p:8080"}


# ---------------------------------------------------------------------------
# _solve_challenge_with_browser (Playwright)
# ---------------------------------------------------------------------------

def _mock_sync_playwright(cookies):
    mock_page = MagicMock()
    mock_context = MagicMock()
    mock_context.new_page.return_value = mock_page
    mock_context.cookies.return_value = cookies
    mock_browser = MagicMock()
    mock_browser.new_context.return_value = mock_context
    mock_p = MagicMock()
    mock_p.chromium.launch.return_value = mock_browser
    mock_cm = MagicMock()
    mock_cm.__enter__.return_value = mock_p
    mock_cm.__exit__.return_value = False
    return mock_cm, mock_page, mock_browser


def test_solve_challenge_with_browser_extracts_cookies():
    mock_cm, mock_page, mock_browser = _mock_sync_playwright(
        [{"name": "within_since", "value": "abc123"}, {"name": "auth", "value": "xyz"}]
    )
    with patch("playwright.sync_api.sync_playwright", return_value=mock_cm):
        cookies = _solve_challenge_with_browser("https://rezka.ag/films/x/1-y-2020.html")
    assert cookies == {"within_since": "abc123", "auth": "xyz"}
    mock_page.goto.assert_called_once()
    mock_browser.close.assert_called_once()


def test_solve_challenge_with_browser_ignores_networkidle_timeout():
    """Regression guard: production hit a TimeoutError on
    wait_for_load_state("networkidle") even though the challenge had
    already passed (the title-change check succeeded first) — some sites
    never go network-idle (analytics/ads keep polling). That must not
    fail the whole bypass; cookies should still come back."""
    from playwright.sync_api import TimeoutError as PlaywrightTimeoutError

    mock_cm, mock_page, mock_browser = _mock_sync_playwright([{"name": "a", "value": "1"}])
    mock_page.wait_for_load_state.side_effect = PlaywrightTimeoutError("networkidle never fired")
    with patch("playwright.sync_api.sync_playwright", return_value=mock_cm):
        cookies = _solve_challenge_with_browser("https://rezka.ag/films/x/1-y-2020.html")
    assert cookies == {"a": "1"}


def test_solve_challenge_with_browser_raises_on_timeout():
    from playwright.sync_api import TimeoutError as PlaywrightTimeoutError

    mock_cm, mock_page, mock_browser = _mock_sync_playwright([])
    mock_page.wait_for_function.side_effect = PlaywrightTimeoutError("timeout")
    with patch("playwright.sync_api.sync_playwright", return_value=mock_cm):
        with pytest.raises(RezkaResolveError, match="антибот-проверку"):
            _solve_challenge_with_browser("https://rezka.ag/films/x/1-y-2020.html")
    mock_browser.close.assert_called_once()


def test_solve_challenge_with_browser_raises_on_generic_error():
    mock_cm, mock_page, mock_browser = _mock_sync_playwright([])
    mock_page.goto.side_effect = RuntimeError("browser crashed")
    with patch("playwright.sync_api.sync_playwright", return_value=mock_cm):
        with pytest.raises(RezkaResolveError, match="Ошибка headless-браузера"):
            _solve_challenge_with_browser("https://rezka.ag/films/x/1-y-2020.html")


def test_resolve_rezka_stream_uses_browser_cookies_when_bypass_enabled():
    mock_api = _mock_movie_api({"720p": ["u720"]})
    with patch("app.utils.rezka.HdRezkaApi", return_value=mock_api) as mock_cls, \
         patch("app.utils.rezka._solve_challenge_with_browser", return_value={"a": "1"}) as mock_solve:
        resolve_rezka_stream("https://rezka.ag/films/x/1-y-2020.html", "720p", bypass_antibot=True)
    mock_solve.assert_called_once_with("https://rezka.ag/films/x/1-y-2020.html")
    _, kwargs = mock_cls.call_args
    assert kwargs["cookies"] == {"a": "1"}


def test_resolve_rezka_stream_skips_browser_when_bypass_disabled():
    mock_api = _mock_movie_api({"720p": ["u720"]})
    with patch("app.utils.rezka.HdRezkaApi", return_value=mock_api), \
         patch("app.utils.rezka._solve_challenge_with_browser") as mock_solve:
        resolve_rezka_stream("https://rezka.ag/films/x/1-y-2020.html", "720p")
    mock_solve.assert_not_called()


# ---------------------------------------------------------------------------
# Cookie caching (redis)
# ---------------------------------------------------------------------------

def test_resolve_rezka_stream_uses_cached_cookies_and_skips_browser():
    mock_api = _mock_movie_api({"720p": ["u720"]})
    redis = MagicMock()
    redis.get.return_value = json.dumps({"cached": "cookie"})
    with patch("app.utils.rezka.HdRezkaApi", return_value=mock_api) as mock_cls, \
         patch("app.utils.rezka._solve_challenge_with_browser") as mock_solve:
        resolve_rezka_stream(
            "https://rezka.ag/films/x/1-y-2020.html", "720p", bypass_antibot=True, redis=redis,
        )
    mock_solve.assert_not_called()
    _, kwargs = mock_cls.call_args
    assert kwargs["cookies"] == {"cached": "cookie"}
    redis.get.assert_called_once_with("rezka_antibot_cookies:rezka.ag")


def test_resolve_rezka_stream_stores_freshly_solved_cookies_in_cache():
    mock_api = _mock_movie_api({"720p": ["u720"]})
    redis = MagicMock()
    redis.get.return_value = None
    with patch("app.utils.rezka.HdRezkaApi", return_value=mock_api), \
         patch("app.utils.rezka._solve_challenge_with_browser", return_value={"fresh": "cookie"}) as mock_solve:
        resolve_rezka_stream(
            "https://rezka.ag/films/x/1-y-2020.html", "720p", bypass_antibot=True, redis=redis,
        )
    mock_solve.assert_called_once()
    redis.setex.assert_called_once_with(
        "rezka_antibot_cookies:rezka.ag", 6 * 3600, json.dumps({"fresh": "cookie"})
    )


def test_resolve_rezka_stream_retries_once_when_cached_cookies_are_stale():
    """Regression guard for cache staleness: a cached cookie that no longer
    works (expired/rotated server-side) must trigger exactly one fresh
    solve-and-retry instead of failing outright."""
    good_api = _mock_movie_api({"720p": ["u720"]})

    class _StaleApi:
        ok = True
        soup = MagicMock()

        @property
        def type(self):
            raise AttributeError("stale cookie")

    redis = MagicMock()
    redis.get.return_value = json.dumps({"stale": "cookie"})

    with patch("app.utils.rezka.HdRezkaApi", side_effect=[_StaleApi(), good_api]) as mock_cls, \
         patch("app.utils.rezka._solve_challenge_with_browser", return_value={"fresh": "cookie"}) as mock_solve:
        url, _ = resolve_rezka_stream(
            "https://rezka.ag/films/x/1-y-2020.html", "720p", bypass_antibot=True, redis=redis,
        )

    assert url == "u720"
    mock_solve.assert_called_once()
    redis.delete.assert_called_once_with("rezka_antibot_cookies:rezka.ag")
    assert mock_cls.call_count == 2
    assert mock_cls.call_args_list[1].kwargs["cookies"] == {"fresh": "cookie"}


def test_resolve_rezka_stream_does_not_retry_when_cookies_were_freshly_solved():
    """A cache MISS that still fails isn't a staleness problem — retrying
    would just solve the same unsolvable challenge twice."""
    class _BadApi:
        ok = True
        soup = MagicMock()

        @property
        def type(self):
            raise AttributeError("still blocked")

    redis = MagicMock()
    redis.get.return_value = None

    with patch("app.utils.rezka.HdRezkaApi", return_value=_BadApi()) as mock_cls, \
         patch("app.utils.rezka._solve_challenge_with_browser", return_value={"fresh": "cookie"}) as mock_solve:
        with pytest.raises(RezkaResolveError, match="не похожа на страницу фильма"):
            resolve_rezka_stream(
                "https://rezka.ag/films/x/1-y-2020.html", "720p", bypass_antibot=True, redis=redis,
            )
    mock_solve.assert_called_once()
    assert mock_cls.call_count == 1


def test_resolve_rezka_stream_retries_get_stream_once_on_stale_session():
    """Regression guard: production hit this exactly — the page itself
    opened fine with cached (hours-old) antibot cookies, but rezka's own
    /ajax/get_cdn_series/ endpoint separately rejected getStream() with its
    own "Время сессии истекло" ("session expired") message. This is a
    different failure point than the page-open retry above (which can't
    see it at all, since the page opened successfully) — it needs its own
    retry around getStream specifically."""
    stale_api = _mock_movie_api({"720p": ["u720bad"]})
    stale_api.id = 3356
    stale_api.origin = "https://rezka.ag"
    stale_api.HEADERS = {}
    stale_api.proxy = {}
    stale_api.cookies = {}
    stale_api.getStream.side_effect = Exception("Failed to fetch stream!")

    fresh_api = _mock_movie_api({"720p": ["u720good"]})

    diag_response = MagicMock()
    diag_response.status_code = 200
    diag_response.json.return_value = {"success": False, "message": "Время сессии истекло"}

    redis = MagicMock()
    redis.get.return_value = json.dumps({"stale": "cookie"})

    url = build_selection_url("https://rezka.ag/films/x/1-y-2020.html", 80)
    with patch("app.utils.rezka.HdRezkaApi", side_effect=[stale_api, fresh_api]) as mock_cls, \
         patch("app.utils.rezka.requests.post", return_value=diag_response), \
         patch("app.utils.rezka._solve_challenge_with_browser", return_value={"fresh": "cookie"}) as mock_solve:
        resolved_url, _ = resolve_rezka_stream(url, "720p", bypass_antibot=True, redis=redis)

    assert resolved_url == "u720good"
    mock_solve.assert_called_once()
    redis.delete.assert_called_once_with("rezka_antibot_cookies:rezka.ag")
    assert mock_cls.call_count == 2


def test_resolve_rezka_stream_does_not_retry_get_stream_for_unrelated_failure():
    """A FetchFailed that isn't a session-expired message (e.g. a
    premium-only translator) shouldn't burn a browser solve retrying
    something a fresh session wouldn't fix anyway."""
    mock_api = _mock_movie_api({})
    mock_api.id = 3356
    mock_api.origin = "https://rezka.ag"
    mock_api.HEADERS = {}
    mock_api.proxy = {}
    mock_api.cookies = {}
    mock_api.getStream.side_effect = Exception("Failed to fetch stream!")

    diag_response = MagicMock()
    diag_response.status_code = 200
    diag_response.json.return_value = {"success": False, "message": "Премиум доступ недоступен"}

    redis = MagicMock()
    redis.get.return_value = json.dumps({"cached": "cookie"})

    url = build_selection_url("https://rezka.ag/films/x/1-y-2020.html", 80)
    with patch("app.utils.rezka.HdRezkaApi", return_value=mock_api) as mock_cls, \
         patch("app.utils.rezka.requests.post", return_value=diag_response), \
         patch("app.utils.rezka._solve_challenge_with_browser") as mock_solve:
        with pytest.raises(RezkaResolveError, match="Не удалось получить поток"):
            resolve_rezka_stream(url, "720p", bypass_antibot=True, redis=redis)

    mock_solve.assert_not_called()
    assert mock_cls.call_count == 1


# ---------------------------------------------------------------------------
# build_selection_url / _parse_selection
# ---------------------------------------------------------------------------

def test_build_selection_url_translator_only():
    url = build_selection_url("https://rezka.ag/films/x/1-y-2020.html", 56)
    assert url == "https://rezka.ag/films/x/1-y-2020.html?rezka_tr=56"


def test_build_selection_url_full_series_selection():
    url = build_selection_url("https://rezka.ag/series/x/1-y-2020.html", 56, 2, 5)
    parsed = _parse_selection(url)
    assert parsed == (56, 2, 5)


def test_build_selection_url_no_selection_returns_base_url_unchanged():
    url = build_selection_url("https://rezka.ag/films/x/1-y-2020.html", None)
    assert url == "https://rezka.ag/films/x/1-y-2020.html"


def test_parse_selection_missing_params_returns_none():
    assert _parse_selection("https://rezka.ag/films/x/1-y-2020.html") == (None, None, None)


def test_parse_selection_roundtrips_through_build():
    url = build_selection_url("https://rezka.ag/series/x/1-y-2020.html?other=1", 99, 3, 12)
    assert _parse_selection(url) == (99, 3, 12)


# ---------------------------------------------------------------------------
# resolve_rezka_stream: translator / season / episode selection
# ---------------------------------------------------------------------------

def _mock_series_api(episodes_info, videos, name="Some Show"):
    from HdRezkaApi.types import TVSeries

    mock_stream = MagicMock()
    mock_stream.videos = videos

    mock_api = MagicMock()
    mock_api.ok = True
    mock_api.type = TVSeries()
    mock_api.name = name
    mock_api.translators = {}
    mock_api.releaseYear = None
    mock_api.episodesInfo = episodes_info
    mock_api.getStream.return_value = mock_stream
    return mock_api


def test_resolve_rezka_stream_passes_translator_id_to_movie_getstream():
    mock_api = _mock_movie_api({"720p": ["u720"]})
    with patch("app.utils.rezka.HdRezkaApi", return_value=mock_api):
        resolve_rezka_stream(
            build_selection_url("https://rezka.ag/films/x/1-y-2020.html", 56), "720p",
        )
    mock_api.getStream.assert_called_once_with(translation=56)


def test_resolve_rezka_stream_movie_with_season_param_rejected():
    mock_api = _mock_movie_api({"720p": ["u720"]})
    with patch("app.utils.rezka.HdRezkaApi", return_value=mock_api):
        with pytest.raises(RezkaResolveError, match="а не сериал"):
            resolve_rezka_stream(
                build_selection_url("https://rezka.ag/films/x/1-y-2020.html", 56, 1, 1), "720p",
            )


def test_resolve_rezka_stream_series_without_selection_raises():
    mock_api = _mock_series_api([], {})
    with patch("app.utils.rezka.HdRezkaApi", return_value=mock_api):
        with pytest.raises(RezkaResolveError, match="Сериалы"):
            resolve_rezka_stream("https://rezka.ag/series/x/1-y-2020.html", "720p")


def test_resolve_rezka_stream_series_calls_getstream_with_season_episode_translator():
    mock_api = _mock_series_api([], {"720p": ["u720"]})
    url = build_selection_url("https://rezka.ag/series/x/1-y-2020.html", 56, 2, 5)
    with patch("app.utils.rezka.HdRezkaApi", return_value=mock_api):
        resolved_url, title = resolve_rezka_stream(url, "720p")
    mock_api.getStream.assert_called_once_with(season=2, episode=5, translation=56)
    assert resolved_url == "u720"
    assert title == "Some Show, 2 сезон, 5 серия"


def test_resolve_rezka_stream_series_title_includes_translator_season_episode():
    mock_api = _mock_series_api([], {"720p": ["u720"]}, name="Рик и Морти")
    mock_api.translators = {355: {"name": "Сыендук", "premium": False}}
    url = build_selection_url("https://rezka.ag/cartoons/x/1-y-2020.html", 355, 1, 3)
    with patch("app.utils.rezka.HdRezkaApi", return_value=mock_api):
        _, title = resolve_rezka_stream(url, "720p")
    assert title == "Рик и Морти, Сыендук, 1 сезон, 3 серия"


def test_display_title_includes_year_when_available():
    mock_api = MagicMock()
    mock_api.name = "Форрест Гамп"
    mock_api.releaseYear = 1994
    assert _display_title(mock_api) == "Форрест Гамп (1994)"


def test_display_title_omits_year_when_none():
    mock_api = MagicMock()
    mock_api.name = "Форрест Гамп"
    mock_api.releaseYear = None
    assert _display_title(mock_api) == "Форрест Гамп"


def test_display_title_swallows_release_year_errors():
    class _FakeApi:
        name = "Форрест Гамп"

        @property
        def releaseYear(self):
            raise AttributeError("boom")

    assert _display_title(_FakeApi()) == "Форрест Гамп"


def test_resolve_rezka_stream_appends_translator_name_to_title():
    mock_api = _mock_movie_api({"720p": ["u720"]}, name="Форрест Гамп")
    mock_api.translators = {56: {"name": "Дубляж", "premium": False}}
    url = build_selection_url("https://rezka.ag/films/x/1-y-2020.html", 56)
    with patch("app.utils.rezka.HdRezkaApi", return_value=mock_api):
        _, title = resolve_rezka_stream(url, "720p")
    assert title == "Форрест Гамп, Дубляж"


def test_resolve_rezka_stream_includes_release_year_in_title():
    mock_api = _mock_movie_api({"720p": ["u720"]}, name="Форрест Гамп")
    mock_api.translators = {56: {"name": "Дубляж", "premium": False}}
    mock_api.releaseYear = 1994
    url = build_selection_url("https://rezka.ag/films/x/1-y-2020.html", 56)
    with patch("app.utils.rezka.HdRezkaApi", return_value=mock_api):
        _, title = resolve_rezka_stream(url, "720p")
    assert title == "Форрест Гамп (1994), Дубляж"


def test_resolve_rezka_stream_omits_year_when_unavailable():
    mock_api = _mock_movie_api({"720p": ["u720"]}, name="Форрест Гамп")
    mock_api.translators = {56: {"name": "Дубляж", "premium": False}}
    mock_api.releaseYear = None
    url = build_selection_url("https://rezka.ag/films/x/1-y-2020.html", 56)
    with patch("app.utils.rezka.HdRezkaApi", return_value=mock_api):
        _, title = resolve_rezka_stream(url, "720p")
    assert title == "Форрест Гамп, Дубляж"


def test_resolve_rezka_stream_series_title_includes_year_before_translator():
    mock_api = _mock_series_api([], {"720p": ["u720"]}, name="Рик и Морти")
    mock_api.translators = {355: {"name": "Сыендук", "premium": False}}
    mock_api.releaseYear = 2013
    url = build_selection_url("https://rezka.ag/cartoons/x/1-y-2020.html", 355, 1, 3)
    with patch("app.utils.rezka.HdRezkaApi", return_value=mock_api):
        _, title = resolve_rezka_stream(url, "720p")
    assert title == "Рик и Морти (2013), Сыендук, 1 сезон, 3 серия"


def test_resolve_rezka_stream_uses_only_translator_name_when_id_not_selected():
    mock_api = _mock_movie_api({"720p": ["u720"]}, name="Форрест Гамп")
    mock_api.translators = {56: {"name": "Дубляж", "premium": False}}
    with patch("app.utils.rezka.HdRezkaApi", return_value=mock_api):
        _, title = resolve_rezka_stream("https://rezka.ag/films/x/1-y-2020.html", "720p")
    assert title == "Форрест Гамп, Дубляж"


def test_resolve_rezka_stream_falls_back_to_bare_title_when_translators_unavailable():
    mock_api = _mock_movie_api({"720p": ["u720"]}, name="Форрест Гамп")
    mock_api.translators = {}
    with patch("app.utils.rezka.HdRezkaApi", return_value=mock_api):
        _, title = resolve_rezka_stream("https://rezka.ag/films/x/1-y-2020.html", "720p")
    assert title == "Форрест Гамп"


# ---------------------------------------------------------------------------
# get_rezka_content_info
# ---------------------------------------------------------------------------

def test_get_rezka_content_info_movie():
    mock_api = _mock_movie_api({"720p": ["u720"]}, name="A Movie")
    mock_api.translators = {56: {"name": "Дубляж", "premium": False}, 99: {"name": "Оригинал", "premium": False}}
    with patch("app.utils.rezka.HdRezkaApi", return_value=mock_api):
        info = get_rezka_content_info("https://rezka.ag/films/x/1-y-2020.html")
    assert info.title == "A Movie"
    assert info.is_series is False
    assert info.translators == {56: "Дубляж", 99: "Оригинал"}
    assert info.episodes_info is None


def test_get_rezka_content_info_movie_title_includes_release_year():
    mock_api = _mock_movie_api({"720p": ["u720"]}, name="A Movie")
    mock_api.translators = {56: {"name": "Дубляж", "premium": False}}
    mock_api.releaseYear = 1999
    with patch("app.utils.rezka.HdRezkaApi", return_value=mock_api):
        info = get_rezka_content_info("https://rezka.ag/films/x/1-y-2020.html")
    assert info.title == "A Movie (1999)"


def test_get_rezka_content_info_series():
    episodes_info = [
        {
            "season": 1,
            "episodes": [
                {"episode": 1, "translations": [{"translator_id": 56, "translator_name": "Дубляж"}]},
                {"episode": 2, "translations": [{"translator_id": 56, "translator_name": "Дубляж"}]},
            ],
        },
        {
            "season": 2,
            "episodes": [
                {"episode": 1, "translations": [{"translator_id": 99, "translator_name": "Оригинал"}]},
            ],
        },
    ]
    mock_api = _mock_series_api(episodes_info, {}, name="A Show")
    mock_api.translators = {56: {"name": "Дубляж", "premium": False}, 99: {"name": "Оригинал", "premium": False}}
    with patch("app.utils.rezka.HdRezkaApi", return_value=mock_api):
        info = get_rezka_content_info("https://rezka.ag/series/x/1-y-2020.html")

    assert info.is_series is True
    assert info.seasons_for_translator(56) == [1]
    assert info.seasons_for_translator(99) == [2]
    assert info.episodes_for(56, 1) == [1, 2]
    assert info.episodes_for(99, 2) == [1]
    assert info.episodes_for(56, 2) == []


def test_get_rezka_content_info_raises_on_unknown_type():
    mock_api = MagicMock()
    mock_api.ok = True
    mock_api.type = MagicMock(name="unknown")
    with patch("app.utils.rezka.HdRezkaApi", return_value=mock_api):
        with pytest.raises(RezkaResolveError, match="Неизвестный тип"):
            get_rezka_content_info("https://rezka.ag/films/x/1-y-2020.html")


def test_sanitize_translators_list_removes_children_without_translator_id():
    """Regression guard: production hit KeyError('data-translator_id') on
    every rezka.ag page tried — the site's current #translators-list markup
    includes a non-translator child element HdRezkaApi's own parser doesn't
    skip. Must remove it in place, keeping the real translator entries."""
    from bs4 import BeautifulSoup

    from app.utils.rezka import _sanitize_translators_list

    soup = BeautifulSoup(
        '<div id="translators-list">'
        '<div class="wrapper-decoration"></div>'
        '<li data-translator_id="56">Дубляж</li>'
        '<li data-translator_id="99">Оригинал</li>'
        "</div>",
        "html.parser",
    )

    class _FakeApi:
        pass

    fake = _FakeApi()
    fake.soup = soup
    _sanitize_translators_list(fake)

    children = soup.find(id="translators-list").find_all(recursive=False)
    assert len(children) == 2
    assert all("data-translator_id" in c.attrs for c in children)


def test_sanitize_translators_list_noop_when_no_container():
    from bs4 import BeautifulSoup

    from app.utils.rezka import _sanitize_translators_list

    class _FakeApi:
        pass

    fake = _FakeApi()
    fake.soup = BeautifulSoup("<html></html>", "html.parser")
    _sanitize_translators_list(fake)  # must not raise


def test_sanitize_translators_list_leaves_old_style_markup_untouched():
    """Regression guard: production hit this on a 1994 title — every child
    of #translators-list only had style="float: left;", no
    data-translator_id at all (an older markup format, not "one decorative
    wrapper amid real entries"). Blindly removing all of them would leave
    an empty, useless translator list — must leave the DOM as-is instead."""
    from bs4 import BeautifulSoup

    from app.utils.rezka import _sanitize_translators_list

    soup = BeautifulSoup(
        '<div id="translators-list">'
        '<li style="float: left;">Дубляж</li>'
        '<li style="float: left;">Кириллица</li>'
        "</div>",
        "html.parser",
    )

    class _FakeApi:
        pass

    fake = _FakeApi()
    fake.soup = soup
    _sanitize_translators_list(fake)

    children = soup.find(id="translators-list").find_all(recursive=False)
    assert len(children) == 2


def test_sanitize_translators_list_hoists_nested_translator_id():
    """Regression guard: production hit this on rezka.ag's multi-season
    "umbrella" pages (e.g. Rick and Morty) — data-translator_id lives on a
    nested <a> inside each <li>, not on the <li> itself. HdRezkaApi's own
    parser only ever inspects the direct child's own attrs, so it must be
    hoisted onto the <li> (plus a "class" attr, since HdRezkaApi also does
    child['class'] right after) instead of being left/removed."""
    from bs4 import BeautifulSoup

    from app.utils.rezka import _sanitize_translators_list

    soup = BeautifulSoup(
        '<div id="translators-list">'
        '<li style="float: left;"><a class="b-translator__items" '
        'data-translator_id="355" href="https://rezka.ag/x/355.html" '
        'title="HDrezka Studio (18+)">HDrezka Studio (18+)</a></li>'
        '<li style="float: left;"><a class="b-translator__items" '
        'data-translator_id="623" href="https://rezka.ag/x/623.html" '
        'title="1win Studio">1win Studio</a></li>'
        "</div>",
        "html.parser",
    )

    class _FakeApi:
        pass

    fake = _FakeApi()
    fake.soup = soup
    _sanitize_translators_list(fake)

    children = soup.find(id="translators-list").find_all(recursive=False)
    assert len(children) == 2
    assert children[0]["data-translator_id"] == "355"
    assert children[1]["data-translator_id"] == "623"
    assert children[0]["class"] == []
    assert children[1]["class"] == []


def test_open_movie_page_merges_session_cookie_from_page_response():
    """Regression guard: production hit HdRezkaApi's own getStream() AJAX
    call failing with the site's "Session expired" message even though the
    page itself opened fine with the (cached, hours-old) antibot cookies —
    HdRezkaApi's page-fetch never merges any Set-Cookie the GET response
    issues back into self.cookies, only what's passed in at construction.
    _open_movie_page must merge it in itself so getStream() sees it too."""
    from HdRezkaApi.types import Movie

    mock_api = MagicMock()
    mock_api.ok = True
    mock_api.type = Movie()
    mock_api.cookies = {"antibot": "stale-but-valid"}
    mock_api.page.cookies.get_dict.return_value = {"PHPSESSID": "fresh-session"}
    mock_api.soup.find.return_value = None  # translators-list absent, sanitize is a no-op

    with patch("app.utils.rezka.HdRezkaApi", return_value=mock_api):
        rezka, _ = _open_movie_page("https://rezka.ag/films/x/1-y-2020.html", {}, {}, {})

    assert rezka.cookies == {"antibot": "stale-but-valid", "PHPSESSID": "fresh-session"}


def test_open_movie_page_swallows_cookie_merge_errors():
    from HdRezkaApi.types import Movie

    class _FakeSoup:
        def find(self, *args, **kwargs):
            return None

    class _FakeApi:
        ok = True
        type = Movie()
        cookies = {"antibot": "stale-but-valid"}
        soup = _FakeSoup()

        @property
        def page(self):
            raise RuntimeError("boom")

    with patch("app.utils.rezka.HdRezkaApi", return_value=_FakeApi()):
        rezka, content_type = _open_movie_page("https://rezka.ag/films/x/1-y-2020.html", {}, {}, {})

    assert content_type == Movie()
    assert rezka.cookies == {"antibot": "stale-but-valid"}


def test_sanitize_translators_list_swallows_soup_access_errors():
    from app.utils.rezka import _sanitize_translators_list

    class _FakeApi:
        @property
        def soup(self):
            raise AttributeError("boom")

    _sanitize_translators_list(_FakeApi())  # must not raise


def test_get_rezka_content_info_reports_unparseable_translator_markup():
    """Regression guard: production hit a raw KeyError on a franchise-style
    URL (.../2136-rik-i-morti-2013-latest/66-syenduk.html) whose translator
    list markup HdRezkaApi's own .translators parser doesn't expect."""
    from HdRezkaApi.types import Movie

    class _FakeApi:
        ok = True
        type = Movie()

        @property
        def translators(self):
            raise KeyError("data-translator_id")

    with patch("app.utils.rezka.HdRezkaApi", return_value=_FakeApi()):
        with pytest.raises(RezkaResolveError, match="список озвучек"):
            get_rezka_content_info("https://rezka.ag/cartoons/comedy/2136-x-2013-latest/66-y.html")
