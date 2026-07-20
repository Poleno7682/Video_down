from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from app.utils.rezka import RezkaResolveError, is_rezka_url, resolve_rezka_stream
from app.utils.rezka import _solve_challenge


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
# _solve_challenge (FlareSolverr)
# ---------------------------------------------------------------------------

def _mock_flaresolverr_response(status_code=200, json_data=None):
    resp = MagicMock()
    resp.status_code = status_code
    resp.raise_for_status = MagicMock()
    resp.json.return_value = json_data or {}
    return resp


def test_solve_challenge_extracts_cookies_and_user_agent():
    payload = {
        "status": "ok",
        "solution": {
            "cookies": [
                {"name": "__ddg1", "value": "abc123", "domain": "rezka.ag"},
                {"name": "session", "value": "xyz", "domain": "rezka.ag"},
            ],
            "userAgent": "Mozilla/5.0 (solved-by-flaresolverr)",
        },
    }
    with patch("app.utils.rezka.requests.post", return_value=_mock_flaresolverr_response(json_data=payload)):
        cookies, user_agent = _solve_challenge("https://rezka.ag/films/x/1-y-2020.html", "http://flaresolverr:8191/v1")
    assert cookies == {"__ddg1": "abc123", "session": "xyz"}
    assert user_agent == "Mozilla/5.0 (solved-by-flaresolverr)"


def test_solve_challenge_raises_on_non_ok_status():
    payload = {"status": "error", "message": "Challenge not solved"}
    with patch("app.utils.rezka.requests.post", return_value=_mock_flaresolverr_response(json_data=payload)):
        with pytest.raises(RezkaResolveError, match="не смог пройти проверку"):
            _solve_challenge("https://rezka.ag/films/x/1-y-2020.html", "http://flaresolverr:8191/v1")


def test_solve_challenge_raises_when_flaresolverr_unreachable():
    with patch("app.utils.rezka.requests.post", side_effect=ConnectionError("refused")):
        with pytest.raises(RezkaResolveError, match="Не удалось связаться с FlareSolverr"):
            _solve_challenge("https://rezka.ag/films/x/1-y-2020.html", "http://flaresolverr:8191/v1")


def test_resolve_rezka_stream_uses_flaresolverr_cookies_and_user_agent():
    mock_api = _mock_movie_api({"720p": ["u720"]})
    with patch("app.utils.rezka.HdRezkaApi", return_value=mock_api) as mock_cls, \
         patch(
             "app.utils.rezka._solve_challenge",
             return_value=({"ddg": "solved"}, "Mozilla/5.0 (solved-ua)"),
         ) as mock_solve:
        resolve_rezka_stream(
            "https://rezka.ag/films/x/1-y-2020.html", "720p",
            flaresolverr_url="http://flaresolverr:8191/v1",
        )
    mock_solve.assert_called_once_with("https://rezka.ag/films/x/1-y-2020.html", "http://flaresolverr:8191/v1")
    _, kwargs = mock_cls.call_args
    assert kwargs["cookies"] == {"ddg": "solved"}
    assert kwargs["headers"]["User-Agent"] == "Mozilla/5.0 (solved-ua)"


def test_resolve_rezka_stream_skips_flaresolverr_when_not_configured():
    mock_api = _mock_movie_api({"720p": ["u720"]})
    with patch("app.utils.rezka.HdRezkaApi", return_value=mock_api), \
         patch("app.utils.rezka._solve_challenge") as mock_solve:
        resolve_rezka_stream("https://rezka.ag/films/x/1-y-2020.html", "720p")
    mock_solve.assert_not_called()
