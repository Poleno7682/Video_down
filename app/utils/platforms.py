from __future__ import annotations

from app.utils.url_tools import domain_name

# Canonical platform keys used for per-user cookies and the global cookie files.
YOUTUBE = "youtube"
INSTAGRAM = "instagram"
TIKTOK = "tiktok"
FACEBOOK = "facebook"

PLATFORMS: tuple[str, ...] = (YOUTUBE, INSTAGRAM, TIKTOK, FACEBOOK)

# Single source of truth: domain substring -> platform key. OCP: extend here to
# support a new platform's cookies everywhere (bot upload, worker download).
_DOMAIN_TO_PLATFORM: list[tuple[str, str]] = [
    ("facebook.", FACEBOOK),
    ("fb.watch", FACEBOOK),
    ("instagram.", INSTAGRAM),
    ("tiktok.", TIKTOK),
    ("youtube.", YOUTUBE),
    ("youtu.be", YOUTUBE),
]

# platform -> Settings attribute holding the global (shared) cookie file path.
PLATFORM_COOKIE_SETTING: dict[str, str] = {
    FACEBOOK: "facebook_cookies_file",
    INSTAGRAM: "instagram_cookies_file",
    TIKTOK: "tiktok_cookies_file",
    YOUTUBE: "youtube_cookies_file",
}


def detect_platform(url: str) -> str | None:
    """Return the platform key for a URL, or None if unsupported/unknown."""
    domain = domain_name(url)
    for pattern, platform in _DOMAIN_TO_PLATFORM:
        if pattern in domain:
            return platform
    return None


def platform_from_filename(filename: str | None) -> str | None:
    """Map an uploaded cookie filename (e.g. ``youtube.txt``) to a platform key."""
    if not filename:
        return None
    name = filename.strip().lower()
    if not name.endswith(".txt"):
        return None
    stem = name[: -len(".txt")]
    return stem if stem in PLATFORMS else None
