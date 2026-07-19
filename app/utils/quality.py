from app.utils.codecs import HEVC_VCODEC_FILTER as _HEVC_VCODEC_FILTER
from app.utils.codecs import H264_VCODEC_FILTER as _H264_VCODEC_FILTER
from app.utils.platforms import FACEBOOK, detect_platform


def _video_format(max_height: int | None) -> str:
    """Build a yt-dlp format string with portrait (Shorts) streams preferred.

    YouTube often serves Shorts as a 16:9 container with letterboxing. Native
    vertical streams have aspect_ratio < 1 (width/height). We try those first,
    then fall back to the usual landscape selectors.

    Codec preference order in every tier is H.264, then HEVC, then any codec
    (see app.utils.codecs for why H.264 and HEVC are separate tiers rather
    than merged into one "safe codec" tier, and why VP9/AV1 are avoided at
    all: merged into MP4 with -c copy, they can end up in a non-standard
    container that many Telegram clients render as a static frame with only
    audio playing). Falling back to any codec is still offered so downloads
    work even when neither H.264 nor HEVC is available.

    The codec preference also applies to the already-muxed "best[...]"
    tiers, not just the bestvideo+bestaudio split ones: sites like TikTok
    never offer separate video-only/audio-only formats, so bestvideo+bestaudio
    never matches anything there and every download falls through to
    "best[...]". Without an explicit codec preference there, yt-dlp's
    default format sort picks by resolution/HDR long before codec or even
    audio presence, which can select a higher-resolution but mislabeled
    video-only format over a lower-resolution format that actually has audio.

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
        # HEVC portrait — only reached when no H.264 format exists at all
        f"bestvideo{_HEVC_VCODEC_FILTER}[aspect_ratio<1]{w_filter}+bestaudio[ext=m4a]/"
        # Any codec portrait (AV1 / VP9 fallback)
        f"bestvideo[aspect_ratio<1]{w_filter}+bestaudio/"
        # Combined (already-muxed) portrait stream, H.264 then HEVC preferred —
        # common on TikTok/Instagram, which never split video/audio.
        f"best{_H264_VCODEC_FILTER}[aspect_ratio<1]{w_filter}/"
        f"best{_HEVC_VCODEC_FILTER}[aspect_ratio<1]{w_filter}/"
        f"best[aspect_ratio<1]{w_filter}"
    )
    landscape = (
        # H.264 landscape
        f"bestvideo{_H264_VCODEC_FILTER}{h_filter}+bestaudio[ext=m4a]/"
        # HEVC landscape
        f"bestvideo{_HEVC_VCODEC_FILTER}{h_filter}+bestaudio[ext=m4a]/"
        # Any codec landscape
        f"bestvideo{h_filter}+bestaudio/"
        # Combined landscape, H.264 then HEVC preferred
        f"best{_H264_VCODEC_FILTER}{h_filter}/"
        f"best{_HEVC_VCODEC_FILTER}{h_filter}/"
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


def format_selector(quality: str, url: str | None = None) -> str:
    quality = normalize_quality(quality)
    base = QUALITY_FORMATS[quality]
    if quality != "audio" and url and detect_platform(url) == FACEBOOK:
        # Facebook's legacy progressive "hd" format is typically a single,
        # already-muxed H.264+AAC file — cleaner and more reliable than its
        # DASH formats, which are often video-only AV1 needing our own
        # codec detection + transcode fallback (and yt-dlp can't report
        # "hd"'s own codec/resolution ahead of time, so it can't be targeted
        # through the filter-based selectors above). As a literal format-id
        # fallback this is harmless everywhere else: yt-dlp simply skips it
        # when the id doesn't exist and falls through to the normal chain.
        return f"hd/{base}"
    return base
