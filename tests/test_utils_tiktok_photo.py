import json
from pathlib import Path

import pytest

from app.utils import tiktok_photo as tp


def _html(item: dict) -> str:
    data = {"__DEFAULT_SCOPE__": {"webapp.video-detail": {"itemInfo": {"itemStruct": item}}}}
    return f'<script id="__UNIVERSAL_DATA_FOR_REHYDRATION__" type="application/json">{json.dumps(data)}</script>'


def test_find_photo_url_from_ytdlp_error():
    msg = "ERROR: Unsupported URL: https://www.tiktok.com/@user.name/photo/123?_r=1&_t=X"
    assert tp.find_photo_url(msg) == "https://www.tiktok.com/@user.name/photo/123?_r=1&_t=X"


def test_find_photo_url_ignores_video_urls():
    assert tp.find_photo_url("Unsupported URL: https://www.tiktok.com/@u/video/1") is None


def test_parse_photo_post_extracts_images_music_title():
    item = {
        "desc": "hello",
        "imagePost": {"images": [{"imageURL": {"urlList": ["http://a/1.jpg"]}}, {"imageURL": {"urlList": []}},
                                 {"imageURL": {"urlList": ["http://a/2.jpg"]}}]},
        "music": {"playUrl": "http://a/m.mp3"},
    }
    assert tp.parse_photo_post(_html(item)) == (["http://a/1.jpg", "http://a/2.jpg"], "http://a/m.mp3", "hello")


def test_parse_photo_post_without_images_raises():
    with pytest.raises(tp.TikTokPhotoError):
        tp.parse_photo_post(_html({"desc": "x"}))


def test_parse_photo_post_without_page_data_raises():
    with pytest.raises(tp.TikTokPhotoError):
        tp.parse_photo_post("<html></html>")


def test_slideshow_command_with_and_without_audio():
    imgs = [Path("a.img"), Path("b.img")]
    with_audio = tp.build_slideshow_command(imgs, Path("m.mp3"), 4.0, Path("o.mp4"))
    assert "-stream_loop" in with_audio and "2:a" in with_audio and "8.00" in with_audio
    no_audio = tp.build_slideshow_command(imgs, None, 4.0, Path("o.mp4"))
    assert "-stream_loop" not in no_audio and "aac" not in no_audio
