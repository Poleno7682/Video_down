def _video_format(max_height: int | None) -> str:
    """Build a yt-dlp format string with portrait (Shorts) streams preferred.

    YouTube often serves Shorts as a 16:9 container with letterboxing. Native
    vertical streams have aspect_ratio < 1 (width/height). We try those first,
    then fall back to the usual landscape selectors.
    """
    if max_height is None:
        portrait = "bestvideo[aspect_ratio<1]+bestaudio/best[aspect_ratio<1]"
        landscape = "bestvideo+bestaudio/best"
        return f"{portrait}/{landscape}/best"

    h = max_height
    portrait = (
        f"bestvideo[aspect_ratio<1][height<={h}]+bestaudio/"
        f"best[aspect_ratio<1][height<={h}]"
    )
    landscape = (
        f"bestvideo[height<={h}]+bestaudio/"
        f"best[height<={h}]"
    )
    return f"{portrait}/{landscape}/best"


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
