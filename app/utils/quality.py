QUALITY_FORMATS: dict[str, str] = {
    "360p": "bestvideo[height<=360][ext=mp4]+bestaudio[ext=m4a]/best[height<=360][ext=mp4]/best[height<=360]/best",
    "480p": "bestvideo[height<=480][ext=mp4]+bestaudio[ext=m4a]/best[height<=480][ext=mp4]/best[height<=480]/best",
    "720p": "bestvideo[height<=720][ext=mp4]+bestaudio[ext=m4a]/best[height<=720][ext=mp4]/best[height<=720]/best",
    "1080p": "bestvideo[height<=1080][ext=mp4]+bestaudio[ext=m4a]/best[height<=1080][ext=mp4]/best[height<=1080]/best",
    "best": "bestvideo[ext=mp4]+bestaudio[ext=m4a]/best[ext=mp4]/best",
    "audio": "bestaudio[ext=m4a]/bestaudio/best",
}


def normalize_quality(value: str | None, default: str = "720p") -> str:
    if not value:
        return default
    value = value.strip().lower()
    return value if value in QUALITY_FORMATS else default


def format_selector(quality: str) -> str:
    return QUALITY_FORMATS[normalize_quality(quality)]
