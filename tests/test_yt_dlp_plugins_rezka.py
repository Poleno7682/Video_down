from __future__ import annotations

import inspect
import re

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


def test_rezka_extractor_only_calls_methods_that_exist_on_infoextractor():
    """Regression guard: report_error() doesn't exist on this yt-dlp
    version's InfoExtractor (production hit AttributeError from it) — any
    self.<method>(...) call the extractor makes must actually exist on the
    base class, or a page-parsing failure crashes with a confusing
    AttributeError instead of yt-dlp's normal "video unavailable" message."""
    import yt_dlp_plugins.extractor.rezka as rezka_module

    source = inspect.getsource(rezka_module.RezkaIE)
    called_methods = set(re.findall(r"self\.([a-zA-Z_][a-zA-Z0-9_]*)\(", source))
    # RezkaIE's own methods (e.g. call_rezkaAPI) count too, not just
    # InfoExtractor's — only flag calls that resolve on neither.
    missing = {m for m in called_methods if not hasattr(rezka_module.RezkaIE, m)}
    assert not missing, f"self.<method>() calls with no matching method: {missing}"
