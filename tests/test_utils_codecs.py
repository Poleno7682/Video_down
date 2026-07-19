from __future__ import annotations

import re

from app.utils.codecs import HEVC_VCODEC_FILTER, RISKY_TELEGRAM_VIDEO_CODECS, H264_VCODEC_FILTER


def _pattern_from_filter(vcodec_filter: str) -> re.Pattern[str]:
    match = re.search(r"vcodec~='(.+?)'", vcodec_filter)
    assert match is not None
    return re.compile(match.group(1))


class TestRiskyTelegramVideoCodecs:
    def test_contains_vp9_and_av1(self):
        assert RISKY_TELEGRAM_VIDEO_CODECS == {"vp9", "av1"}

    def test_does_not_contain_h264_or_hevc(self):
        """H.264/HEVC don't have the static-frame rendering bug — only
        vp9/av1 do. If either ever ends up in this set by mistake, the
        format selector would start avoiding a codec that's actually fine."""
        assert "h264" not in RISKY_TELEGRAM_VIDEO_CODECS
        assert "hevc" not in RISKY_TELEGRAM_VIDEO_CODECS
        assert "h265" not in RISKY_TELEGRAM_VIDEO_CODECS


class TestH264VcodecFilter:
    def test_matches_youtube_and_tiktok_naming(self):
        pattern = _pattern_from_filter(H264_VCODEC_FILTER)
        assert pattern.match("avc1.640028")
        assert pattern.match("h264")

    def test_does_not_match_hevc_or_risky_codecs(self):
        pattern = _pattern_from_filter(H264_VCODEC_FILTER)
        assert not pattern.match("hevc")
        assert not pattern.match("h265")
        assert not pattern.match("vp9")
        assert not pattern.match("av1")


class TestHevcVcodecFilter:
    def test_matches_common_hevc_naming_variants(self):
        pattern = _pattern_from_filter(HEVC_VCODEC_FILTER)
        assert pattern.match("hevc")
        assert pattern.match("h265")
        assert pattern.match("hvc1")
        assert pattern.match("hev1")

    def test_does_not_match_h264_or_risky_codecs(self):
        pattern = _pattern_from_filter(HEVC_VCODEC_FILTER)
        assert not pattern.match("h264")
        assert not pattern.match("avc1.640028")
        assert not pattern.match("vp9")
        assert not pattern.match("av1")
