from __future__ import annotations

import inspect

from yt_dlp import YoutubeDL

from yt_dlp_plugins.extractor.rezka import RezkaIE


def test_rezka_ie_matches_films_url():
    assert RezkaIE.suitable("https://rezka.ag/films/detective/807-advokat-dyavola-1997.html")


def test_rezka_ie_matches_hdrezka_domain():
    assert RezkaIE.suitable("https://hdrezka.me/series/drama/12345-some-show-2020.html")


def test_rezka_ie_rejects_unrelated_url():
    assert not RezkaIE.suitable("https://www.youtube.com/watch?v=dQw4w9WgXcQ")


def test_rezka_ie_discovered_by_ytdlp():
    ydl = YoutubeDL({"quiet": True})
    ie = ydl.get_info_extractor("Rezka")
    assert type(ie).__name__ == "RezkaIE"


def test_rezka_extractor_never_blocks_on_interactive_input():
    """Regression guard: the upstream plugin blocks on input() to ask which
    translator/voiceover to use when a page offers several and none is
    preselected — fatal in an unattended Celery task (no timeout catches a
    blocked stdin read). Our vendored copy must auto-pick one instead."""
    import yt_dlp_plugins.extractor.rezka as rezka_module

    source = inspect.getsource(rezka_module.RezkaIE)
    assert "input(" not in source
