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
