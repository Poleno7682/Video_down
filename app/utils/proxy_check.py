from __future__ import annotations

from yt_dlp import YoutubeDL

# A short, always-available, non-controversial YouTube video ("Me at the
# zoo", the first video ever uploaded) used purely as a probe: if a proxy
# can extract its info, the proxy isn't currently IP-blocked by YouTube.
_PROBE_URL = "https://www.youtube.com/watch?v=jNQXAC9IVRw"

_ANTI_BOT_MARKERS = (
    "sign in to confirm",
    "confirm you're not a bot",
    "confirm you are not a bot",
    "confirm you’re not a bot",  # typographic apostrophe from yt-dlp
)

# "Expected 05 got 48" is a SOCKS5 client reading an HTTP response instead of
# a SOCKS5 handshake reply (0x05 is the SOCKS5 version byte; 0x48 is ASCII
# 'H', the start of "HTTP/1.1..."). It means the proxy is actually
# HTTP(S)-only but was added as SOCKS5 — a scheme mismatch, not a dead proxy.
_SCHEME_MISMATCH_MARKERS = ("expected 05 got 48", "expected 05, got 48")


class ProxyCheckError(RuntimeError):
    """The proxy failed the automatic connectivity/anti-bot probe."""


def check_proxy(proxy_url: str, timeout: int = 20) -> None:
    """Probe proxy_url against YouTube; raise ProxyCheckError if it's no good.

    Meant to run before a proxy is saved so a bad/blocked one is caught
    immediately instead of silently failing real downloads later. Raises
    with a human-readable (Russian) message suitable for a Telegram reply.
    """
    opts = {
        "quiet": True,
        "no_warnings": True,
        "skip_download": True,
        "noplaylist": True,
        "socket_timeout": timeout,
        "proxy": proxy_url,
    }
    try:
        with YoutubeDL(opts) as ydl:
            ydl.extract_info(_PROBE_URL, download=False)
    except Exception as exc:
        text = str(exc).lower()
        if any(marker in text for marker in _ANTI_BOT_MARKERS):
            raise ProxyCheckError(
                "YouTube заблокировал этот прокси (Sign in to confirm you're not a bot)."
            ) from exc
        if any(marker in text for marker in _SCHEME_MISMATCH_MARKERS):
            raise ProxyCheckError(
                "Похоже, это не SOCKS5-прокси (сервер ответил по HTTP). "
                "Попробуйте добавить его как HTTPS."
            ) from exc
        raise ProxyCheckError(f"Прокси не отвечает или соединение не удалось: {exc}") from exc
