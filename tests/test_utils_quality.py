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
        assert fmt.index("vcodec~='^(avc1|h264)'") < fmt.index("bestvideo[aspect_ratio<1][width<=720]+bestaudio/")

    def test_portrait_tier_filters_by_width_not_height(self):
        """A portrait clip's quality-defining short edge is width (a 720p
        portrait clip is ~720x1280, i.e. height 1280 > 720) — filtering
        portrait streams by height<=720 would exclude every real format of
        such a clip and fall through to the codec/resolution-agnostic
        last-resort tier."""
        fmt = format_selector("720p")
        portrait_part = fmt.split("/bestvideo[vcodec~='^(avc1|h264)'][height<=720]")[0]
        assert "width<=720" in portrait_part
        assert "height<=720" not in portrait_part

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
        assert fmt.index("best[vcodec~='^(avc1|h264)']") < fmt.index("best[aspect_ratio<1][width<=720]")

    def test_no_forced_mp4_video_stream(self):
        fmt = format_selector("720p")
        assert "[ext=mp4]" not in fmt

    @pytest.mark.parametrize("q", list(QUALITY_FORMATS.keys()))
    def test_all_qualities_return_format(self, q):
        result = format_selector(q)
        assert result == QUALITY_FORMATS[q]


class TestFormatSelectorAgainstRealFormatList:
    """Regression test reproducing a real TikTok format list (portrait,
    height > our old 720p cap, one codec mislabeled as having audio when it
    doesn't) to make sure the selector actually resolves to a working format
    end-to-end, not just that the selector string looks right."""

    def _tiktok_formats(self):
        # Real portrait dimensions from production: width is the
        # quality-defining short edge (576/720), height is the long edge
        # (1024/1280) — well above any of our height<=X quality caps.
        return [
            {
                "format_id": "h264_540p_2685682-0", "vcodec": "h264", "acodec": "aac",
                "width": 576, "height": 1024, "aspect_ratio": round(576 / 1024, 2),
                "ext": "mp4", "protocol": "https", "tbr": 2685, "url": "https://x/h264",
            },
            {
                # bytevc1_720p_1041683-1: production confirmed via ffprobe
                # that this format has NO audio stream at all, despite
                # yt-dlp's own metadata claiming acodec=aac for it.
                "format_id": "bytevc1_720p_1041683-1", "vcodec": "h265", "acodec": "aac",
                "width": 720, "height": 1280, "aspect_ratio": round(720 / 1280, 2),
                "ext": "mp4", "protocol": "https", "tbr": 1041, "url": "https://x/bytevc1",
            },
        ]

    def _resolve(self, quality: str) -> str:
        from yt_dlp import YoutubeDL

        sel = format_selector(quality)
        ydl = YoutubeDL({"format": sel, "quiet": True})
        selector_fn = ydl.build_format_selector(sel)
        ctx = {"formats": self._tiktok_formats(), "incomplete_formats": False}
        chosen = list(selector_fn(ctx))
        assert len(chosen) == 1
        result = chosen[0]
        return result["requested_formats"][0]["format_id"] if "requested_formats" in result else result["format_id"]

    def test_picks_h264_format_with_real_audio_over_higher_res_h265(self):
        assert self._resolve("720p") == "h264_540p_2685682-0"

    def _hevc_only_formats(self):
        # Real Facebook DASH formats: no H.264 exists at all for this video,
        # only two HEVC tiers at different resolutions/bitrates.
        return [
            {
                "format_id": "bytevc1_540p_299618-0", "vcodec": "h265", "acodec": "aac",
                "width": 576, "height": 1024, "aspect_ratio": round(576 / 1024, 2),
                "ext": "mp4", "protocol": "https", "tbr": 299, "url": "https://x/540p",
            },
            {
                "format_id": "bytevc1_720p_662411-0", "vcodec": "h265", "acodec": "aac",
                "width": 720, "height": 1280, "aspect_ratio": round(720 / 1280, 2),
                "ext": "mp4", "protocol": "https", "tbr": 662, "url": "https://x/720p",
            },
        ]

    def test_picks_best_hevc_when_no_h264_available(self):
        """Real-world case: a Facebook video only offered HEVC formats. HEVC
        isn't in RISKY_TELEGRAM_VIDEO_CODECS (unlike vp9/av1), so there's no
        correctness reason to settle for the lower-resolution/lower-bitrate
        one just because it's the only codec tier we check first."""
        from yt_dlp import YoutubeDL

        sel = format_selector("720p")
        ydl = YoutubeDL({"format": sel, "quiet": True})
        selector_fn = ydl.build_format_selector(sel)
        ctx = {"formats": self._hevc_only_formats(), "incomplete_formats": False}
        chosen = list(selector_fn(ctx))
        result = chosen[0]
        fid = result["requested_formats"][0]["format_id"] if "requested_formats" in result else result["format_id"]
        assert fid == "bytevc1_720p_662411-0"


class TestFacebookHdFallback:
    def test_prepends_hd_for_facebook_url(self):
        fmt = format_selector("720p", "https://www.facebook.com/share/r/abc123/")
        assert fmt.startswith("hd/")
        # Everything after "hd/" must be the normal chain, unmodified.
        assert fmt[len("hd/"):] == format_selector("720p")

    def test_prepends_hd_for_fb_watch_url(self):
        fmt = format_selector("720p", "https://fb.watch/abc123/")
        assert fmt.startswith("hd/")

    def test_no_hd_fallback_without_url(self):
        assert not format_selector("720p").startswith("hd/")

    def test_no_hd_fallback_for_other_platforms(self):
        fmt = format_selector("720p", "https://www.youtube.com/watch?v=x")
        assert not fmt.startswith("hd/")

    def test_no_hd_fallback_for_audio_quality(self):
        """hd is a muxed video+audio format — irrelevant/wrong for an
        audio-only request."""
        fmt = format_selector("audio", "https://www.facebook.com/share/r/abc123/")
        assert not fmt.startswith("hd/")
