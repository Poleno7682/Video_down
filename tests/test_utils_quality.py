import pytest

from app.utils.quality import QUALITY_FORMATS, format_selector, normalize_quality


class TestNormalizeQuality:
    def test_valid_720p(self):
        assert normalize_quality("720p") == "720p"

    def test_valid_1080p(self):
        assert normalize_quality("1080p") == "1080p"

    def test_valid_audio(self):
        assert normalize_quality("audio") == "audio"

    def test_case_insensitive(self):
        assert normalize_quality("720P") == "720p"
        assert normalize_quality("BEST") == "best"

    def test_strips_whitespace(self):
        assert normalize_quality("  720p  ") == "720p"

    def test_none_returns_default(self):
        assert normalize_quality(None) == "720p"

    def test_empty_returns_default(self):
        assert normalize_quality("") == "720p"

    def test_invalid_returns_default(self):
        assert normalize_quality("4k") == "720p"

    def test_custom_default(self):
        assert normalize_quality("bogus", default="1080p") == "1080p"

    @pytest.mark.parametrize("q", list(QUALITY_FORMATS.keys()))
    def test_all_valid_qualities_pass_through(self, q):
        assert normalize_quality(q) == q


class TestFormatSelector:
    def test_returns_string(self):
        result = format_selector("720p")
        assert isinstance(result, str)
        assert len(result) > 0

    def test_portrait_streams_preferred_for_shorts(self):
        fmt = format_selector("720p")
        assert "aspect_ratio<1" in fmt
        # Portrait selectors must appear before landscape ones
        assert fmt.index("aspect_ratio<1") < fmt.index("bestvideo[height<=720]")

    def test_h264_preferred_before_any_codec(self):
        fmt = format_selector("720p")
        # H.264 variant must come before the codec-agnostic variant
        assert "vcodec~='^(avc1|h264)'" in fmt
        assert fmt.index("vcodec~='^(avc1|h264)'") < fmt.index("bestvideo[aspect_ratio<1][height<=720]+bestaudio/")

    def test_h264_filter_matches_both_avc1_and_h264_naming(self):
        """YouTube reports vcodec as 'avc1', TikTok/others as 'h264' — the
        selector must engage its H.264 preference on both."""
        import re

        from app.utils.quality import _H264_VCODEC_FILTER

        match = re.search(r"vcodec~='(.+?)'", _H264_VCODEC_FILTER)
        assert match is not None
        pattern = re.compile(match.group(1))
        assert pattern.match("avc1.640028")
        assert pattern.match("h264")
        assert not pattern.match("vp9")
        assert not pattern.match("av1")

    def test_h264_preferred_in_combined_best_tier_too(self):
        """Sites like TikTok never split video/audio, so bestvideo+bestaudio
        never matches there — every download falls through to best[...].
        That tier must also prefer H.264 over letting yt-dlp's default sort
        (resolution/HDR first) pick an arbitrary, possibly audio-less codec."""
        fmt = format_selector("720p")
        assert "best[vcodec~='^(avc1|h264)']" in fmt
        assert fmt.index("best[vcodec~='^(avc1|h264)']") < fmt.index("best[aspect_ratio<1][height<=720]")

    def test_no_forced_mp4_video_stream(self):
        fmt = format_selector("720p")
        assert "[ext=mp4]" not in fmt

    @pytest.mark.parametrize("q", list(QUALITY_FORMATS.keys()))
    def test_all_qualities_return_format(self, q):
        result = format_selector(q)
        assert result == QUALITY_FORMATS[q]
