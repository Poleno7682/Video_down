from __future__ import annotations

import pytest

from app.utils.platforms import (
    FACEBOOK,
    INSTAGRAM,
    TIKTOK,
    YOUTUBE,
    detect_platform,
    platform_from_filename,
)


@pytest.mark.parametrize(
    "url,expected",
    [
        ("https://www.youtube.com/watch?v=abc", YOUTUBE),
        ("https://youtu.be/abc", YOUTUBE),
        ("https://www.instagram.com/p/abc", INSTAGRAM),
        ("https://www.tiktok.com/@u/video/1", TIKTOK),
        ("https://www.facebook.com/watch?v=1", FACEBOOK),
        ("https://fb.watch/abc", FACEBOOK),
        ("https://vimeo.com/123", None),
        ("https://example.com/video", None),
    ],
)
def test_detect_platform(url, expected):
    assert detect_platform(url) == expected


@pytest.mark.parametrize(
    "filename,expected",
    [
        ("youtube.txt", YOUTUBE),
        ("YouTube.TXT", YOUTUBE),
        ("instagram.txt", INSTAGRAM),
        ("tiktok.txt", TIKTOK),
        ("facebook.txt", FACEBOOK),
        ("cookies.txt", None),
        ("youtube.json", None),
        ("youtube", None),
        (None, None),
        ("", None),
    ],
)
def test_platform_from_filename(filename, expected):
    assert platform_from_filename(filename) == expected
