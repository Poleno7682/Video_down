from __future__ import annotations

import re

# Accepted proxy formats besides a full scheme://... URL:
#   IP:PORT
#   IP:PORT@LOGIN:PASSWORD
#   IP:PORT:LOGIN:PASSWORD
#   IP:PORT;LOGIN:PASSWORD
_HOSTPORT_RE = re.compile(r"^[\w.\-]+:\d{1,5}$")
_KNOWN_SCHEMES = {"socks5h", "socks5", "socks4", "http", "https"}


def _build_url(scheme: str, hostport: str, login: str | None, password: str | None) -> str | None:
    if not _HOSTPORT_RE.match(hostport):
        return None
    if login and password:
        return f"{scheme}://{login}:{password}@{hostport}"
    return f"{scheme}://{hostport}"


def parse_proxy_input(raw: str, scheme: str) -> str | None:
    """Normalize a proxy string to a scheme://[login:pass@]host:port URL.

    Accepts an already-schemed URL (returned as-is if the scheme is
    recognized) or one of IP:PORT, IP:PORT@LOGIN:PASSWORD,
    IP:PORT:LOGIN:PASSWORD, IP:PORT;LOGIN:PASSWORD — normalized using the
    given scheme (the format the admin picked via the /addproxy keyboard).
    Returns None if raw doesn't match any of these shapes.
    """
    raw = raw.strip()
    if "://" in raw:
        given_scheme = raw.split("://", 1)[0].lower()
        return raw if given_scheme in _KNOWN_SCHEMES else None

    for delimiter in ("@", ";"):
        if delimiter in raw:
            hostport, _, cred = raw.partition(delimiter)
            login, _, password = cred.partition(":")
            return _build_url(scheme, hostport, login or None, password or None)

    parts = raw.split(":")
    if len(parts) == 4:
        host, port, login, password = parts
        return _build_url(scheme, f"{host}:{port}", login, password)
    if len(parts) == 2:
        host, port = parts
        return _build_url(scheme, f"{host}:{port}", None, None)
    return None
