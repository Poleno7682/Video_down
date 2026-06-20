import hashlib
import re
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse


URL_RE = re.compile(r"https?://[^\s]+", re.IGNORECASE)
TRACKING_PARAMS_PREFIXES = ("utm_",)
TRACKING_PARAMS = {"fbclid", "mibextid", "igshid", "si", "feature", "share_id"}


def extract_url(text: str) -> str | None:
    match = URL_RE.search(text or "")
    if not match:
        return None
    return match.group(0).strip().rstrip(").,;]")


def normalize_url(url: str) -> str:
    parsed = urlparse(url.strip())
    query_items = []
    for key, value in parse_qsl(parsed.query, keep_blank_values=True):
        key_lower = key.lower()
        if key_lower in TRACKING_PARAMS:
            continue
        if any(key_lower.startswith(prefix) for prefix in TRACKING_PARAMS_PREFIXES):
            continue
        query_items.append((key, value))

    cleaned = parsed._replace(
        scheme=parsed.scheme.lower(),
        netloc=parsed.netloc.lower(),
        query=urlencode(query_items),
        fragment="",
    )
    return urlunparse(cleaned)


def is_valid_url(url: str) -> bool:
    parsed = urlparse(url)
    return parsed.scheme in {"http", "https"} and bool(parsed.netloc)


def url_hash(url: str) -> str:
    return hashlib.sha256(normalize_url(url).encode("utf-8")).hexdigest()


def domain_name(url: str) -> str:
    return urlparse(url).netloc.lower().removeprefix("www.")
