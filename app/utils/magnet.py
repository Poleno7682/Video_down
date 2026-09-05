from __future__ import annotations

import re

# xt=urn:btih:<info-hash> is the only mandatory part of a magnet URI (BEP 9);
# everything else (dn=, tr=, ...) is optional, so this doesn't try to
# validate the full querystring - just that it's plausibly a magnet link.
MAGNET_RE = re.compile(r"magnet:\?xt=urn:btih:[a-zA-Z0-9]+[^\s]*", re.IGNORECASE)


def extract_magnet_uri(text: str) -> str | None:
    match = MAGNET_RE.search(text or "")
    if not match:
        return None
    return match.group(0).strip().rstrip(").,;]")


def is_magnet_uri(text: str) -> bool:
    return extract_magnet_uri(text) is not None


# A Telegram document upload is treated as a torrent request when either its
# filename or (client-declared, so not trusted alone) MIME type says so -
# checking both catches clients that send one without the other.
_TORRENT_MIME_TYPES = frozenset({"application/x-bittorrent"})


def is_torrent_document(file_name: str | None, mime_type: str | None) -> bool:
    if file_name and file_name.lower().endswith(".torrent"):
        return True
    return mime_type in _TORRENT_MIME_TYPES
