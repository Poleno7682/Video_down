from __future__ import annotations

# Shared across app.utils.rezka (Anubis antibot bypass, direct HTTP resolve)
# and app.worker.downloader (browser-retry fallback for yt-dlp) — both need
# to present the same modern-Chrome fingerprint, so keep it in one place.
MODERN_CHROME_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "en-US,en;q=0.9",
}
