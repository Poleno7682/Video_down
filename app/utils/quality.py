def _video_format(max_height: int | None) -> str:
    """Build a yt-dlp format string with portrait (Shorts) streams preferred.

    YouTube often serves Shorts as a 16:9 container with letterboxing. Native
    vertical streams have aspect_ratio < 1 (width/height). We try those first,
    then fall back to the usual landscape selectors.

    H.264 (avc1) is preferred in every tier: when merged into MP4 with -c copy,
    VP9 (WebM codec) ends up in a non-standard VP9-in-MP4 container that many
    Telegram clients cannot decode as video, showing only the first keyframe
    (static image) while audio plays normally. AV1-in-MP4 has similar issues on
    older clients. Falling back to any codec is still offered so downloads work
    even when H.264 is unavailable.
    """
    h_filter = f"[height<={max_height}]" if max_height is not None else ""

    portrait = (
        # H.264 portrait — best MP4 compatibility
        f"bestvideo[vcodec^=avc1][aspect_ratio<1]{h_filter}+bestaudio[ext=m4a]/"
        # Any codec portrait (AV1 / VP9 fallback)
        f"bestvideo[aspect_ratio<1]{h_filter}+bestaudio/"
        # Combined portrait stream (rare on YouTube, common on TikTok/Instagram)
        f"best[aspect_ratio<1]{h_filter}"
    )
    landscape = (
        # H.264 landscape
        f"bestvideo[vcodec^=avc1]{h_filter}+bestaudio[ext=m4a]/"
        # Any codec landscape
        f"bestvideo{h_filter}+bestaudio/"
        # Combined landscape
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
