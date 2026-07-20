from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from app.utils.rezka import RezkaResolveError, is_rezka_url, resolve_rezka_stream


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
