from __future__ import annotations

import json
import urllib.error
import urllib.parse
import urllib.request
from http.cookiejar import CookieJar
from urllib.request import HTTPCookieProcessor, build_opener

# YouTube TV-app OAuth2 credentials — designed for the device-authorization flow.
# Publicly documented in yt-dlp's git history; revoke scope is YouTube read/write.
_CLIENT_ID = "861556708454-d6dlm3lh05idd8npek18k6be8ba3oc68.apps.googleusercontent.com"
_CLIENT_SECRET = "SboVhoG9s0rNafixCSGGKXAT"
_SCOPE = "https://www.googleapis.com/auth/youtube"

_DEVICE_CODE_URL = "https://oauth2.googleapis.com/device/code"
_TOKEN_URL = "https://oauth2.googleapis.com/token"
_REVOKE_URL = "https://oauth2.googleapis.com/revoke"
_OAUTH_LOGIN_URL = "https://accounts.google.com/OAuthLogin"

# ChromeCast UA matches the TV-app client expectations.
_UA = (
    "Mozilla/5.0 (ChromeCast; Linux) AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/120.0.0.0 Safari/537.36 CrKey/1.59.240716"
)


class DeviceFlowPending(Exception):
    """User hasn't approved the request yet — keep polling."""


class DeviceFlowExpired(Exception):
    """Device code expired — user must restart the flow."""


def start_device_flow() -> dict:
    """Start a Google device-authorization flow for YouTube access.

    Returns dict with: device_code, user_code, verification_url, expires_in, interval.
    """
    data = urllib.parse.urlencode({"client_id": _CLIENT_ID, "scope": _SCOPE}).encode()
    req = urllib.request.Request(_DEVICE_CODE_URL, data=data)
    with urllib.request.urlopen(req, timeout=15) as resp:
        return json.loads(resp.read())


def poll_token(device_code: str) -> dict:
    """Poll the token endpoint once.

    Returns dict with access_token, refresh_token, expires_in on success.
    Raises DeviceFlowPending if not yet approved, DeviceFlowExpired if code expired.
    """
    data = urllib.parse.urlencode({
        "client_id": _CLIENT_ID,
        "client_secret": _CLIENT_SECRET,
        "device_code": device_code,
        "grant_type": "urn:ietf:params:oauth:grant-type:device_code",
    }).encode()
    req = urllib.request.Request(_TOKEN_URL, data=data)
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            return json.loads(resp.read())
    except urllib.error.HTTPError as e:
        body = json.loads(e.read())
        err = body.get("error", "")
        if err == "authorization_pending":
            raise DeviceFlowPending()
        if err in ("expired_token", "bad_verification_code"):
            raise DeviceFlowExpired()
        raise


def refresh_access_token(refresh_token: str) -> dict:
    """Exchange a stored refresh_token for a new access_token."""
    data = urllib.parse.urlencode({
        "client_id": _CLIENT_ID,
        "client_secret": _CLIENT_SECRET,
        "refresh_token": refresh_token,
        "grant_type": "refresh_token",
    }).encode()
    req = urllib.request.Request(_TOKEN_URL, data=data)
    with urllib.request.urlopen(req, timeout=15) as resp:
        return json.loads(resp.read())


def revoke_token(token: str) -> None:
    """Revoke an OAuth2 access or refresh token (best-effort)."""
    data = urllib.parse.urlencode({"token": token}).encode()
    req = urllib.request.Request(_REVOKE_URL, data=data)
    try:
        urllib.request.urlopen(req, timeout=10)
    except Exception:
        pass


def generate_youtube_cookies(access_token: str) -> str:
    """Convert an OAuth2 access_token into YouTube session cookies (Netscape format).

    Calls Google's OAuthLogin endpoint, follows redirects to YouTube, and collects
    all cookies (SAPISID, SID, HSID, SSID, APISID) that yt-dlp needs for
    authenticated requests.
    """
    jar = CookieJar()
    opener = build_opener(HTTPCookieProcessor(jar))
    opener.addheaders = [("User-Agent", _UA)]

    url = (
        f"{_OAUTH_LOGIN_URL}"
        f"?source=device&issueuserinfo=1&access_token={urllib.parse.quote(access_token)}"
    )
    opener.open(url, timeout=30)

    lines = ["# Netscape HTTP Cookie File\n"]
    for cookie in jar:
        domain = cookie.domain or ".youtube.com"
        domain_initial_dot = "TRUE" if domain.startswith(".") else "FALSE"
        path = cookie.path or "/"
        secure = "TRUE" if cookie.secure else "FALSE"
        expires = str(int(cookie.expires)) if cookie.expires else "0"
        lines.append(
            f"{domain}\t{domain_initial_dot}\t{path}\t{secure}\t{expires}"
            f"\t{cookie.name}\t{cookie.value}\n"
        )

    if len(lines) <= 1:
        raise RuntimeError("Google OAuthLogin returned no cookies — token may be invalid")

    return "".join(lines)
