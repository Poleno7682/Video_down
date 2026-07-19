_H264_VCODEC_FILTER = "[vcodec~='^(avc1|h264)']"


def _video_format(max_height: int | None) -> str:
    """Build a yt-dlp format string with portrait (Shorts) streams preferred.

    YouTube often serves Shorts as a 16:9 container with letterboxing. Native
    vertical streams have aspect_ratio < 1 (width/height). We try those first,
    then fall back to the usual landscape selectors.

    H.264 is preferred in every tier: when merged into MP4 with -c copy,
    VP9 (WebM codec) ends up in a non-standard VP9-in-MP4 container that many
    Telegram clients cannot decode as video, showing only the first keyframe
    (static image) while audio plays normally. AV1-in-MP4 has similar issues on
    older clients. Falling back to any codec is still offered so downloads work
    even when H.264 is unavailable. Sites report the codec name differently —
    YouTube uses "avc1", TikTok/others use "h264" — so the filter matches both.

    The H.264 preference also applies to the already-muxed "best[...]" tiers,
    not just the bestvideo+bestaudio split ones: sites like TikTok never offer
    separate video-only/audio-only formats, so bestvideo+bestaudio never
    matches anything there and every download falls through to "best[...]".
    Without an explicit codec preference there, yt-dlp's default format sort
    picks by resolution/HDR long before codec or even audio presence, which
    can select a higher-resolution but mislabeled video-only format over a
    lower-resolution H.264 format that actually has audio.

    max_height caps the quality-defining SHORT edge of the frame, not the
    literal pixel "height" field — for a portrait clip that's the width, not
    the height (a 720p portrait TikTok/Shorts clip is ~720x1280, i.e. height
    1280 > 720). Filtering portrait streams with height<=720 would exclude
    every real format of such a clip and fall through the whole selector
    chain to the codec/resolution-agnostic last-resort tier, which is how a
    higher-resolution but audio-less format could get picked over a
    lower-resolution one that actually has audio.
    """
    h_filter = f"[height<={max_height}]" if max_height is not None else ""
    w_filter = f"[width<={max_height}]" if max_height is not None else ""

    portrait = (
        # H.264 portrait — best MP4 compatibility
        f"bestvideo{_H264_VCODEC_FILTER}[aspect_ratio<1]{w_filter}+bestaudio[ext=m4a]/"
        # Any codec portrait (AV1 / VP9 fallback)
        f"bestvideo[aspect_ratio<1]{w_filter}+bestaudio/"
        # Combined (already-muxed) portrait stream, H.264 preferred —
        # common on TikTok/Instagram, which never split video/audio.
        f"best{_H264_VCODEC_FILTER}[aspect_ratio<1]{w_filter}/"
        f"best[aspect_ratio<1]{w_filter}"
    )
    landscape = (
        # H.264 landscape
        f"bestvideo{_H264_VCODEC_FILTER}{h_filter}+bestaudio[ext=m4a]/"
        # Any codec landscape
        f"bestvideo{h_filter}+bestaudio/"
        # Combined landscape, H.264 preferred
        f"best{_H264_VCODEC_FILTER}{h_filter}/"
        f"best{h_filter}"
    )
    # Absolute last resort for direct CDN links yt-dlp can't fully probe
    # (no height/codec metadata available before download): grab whatever a
    # plain HTTP(S) request returns rather than failing outright.
    direct_fallback = "best[protocol^=http]/best"
    return f"{portrait}/{landscape}/{direct_fallback}"


QUALITY_FORMATS: dict[str, str] = {
    "360p": _video_format(360),
    "480p": _video_format(480),
    "720p": _video_format(720),
    "1080p": _video_format(1080),
    "best": _video_format(None),
    "audio": "bestaudio[ext=m4a]/bestaudio/best",
}


def normalize_quality(value: str | None, default: str = "720p") -> str:
    if not value:
        return default
    value = value.strip().lower()
    return value if value in QUALITY_FORMATS else default


def format_selector(quality: str) -> str:
    return QUALITY_FORMATS[normalize_quality(quality)]
