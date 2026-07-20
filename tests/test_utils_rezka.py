from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import pytest

from app.utils.rezka import RezkaResolveError, is_rezka_url, resolve_rezka_stream
from app.utils.rezka import _solve_challenge_with_browser


def test_is_rezka_url_matches_films():
    assert is_rezka_url("https://rezka.ag/films/detective/807-advokat-dyavola-1997.html")


def test_is_rezka_url_matches_hdrezka_domain():
    assert is_rezka_url("https://hdrezka.me/series/drama/12345-some-show-2020.html")


def test_is_rezka_url_rejects_unrelated_url():
    assert not is_rezka_url("https://www.youtube.com/watch?v=dQw4w9WgXcQ")


def _mock_movie_api(videos: dict[str, list[str]], name: str = "Advocate of the Devil"):
    from HdRezkaApi.types import Movie

    mock_stream = MagicMock()
    mock_stream.videos = videos

    mock_api = MagicMock()
    mock_api.ok = True
    mock_api.type = Movie()
    mock_api.name = name
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
