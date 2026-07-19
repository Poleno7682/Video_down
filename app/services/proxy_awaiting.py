from __future__ import annotations

from redis import Redis

_AWAITING_PREFIX = "admin_awaiting_proxy:"
_AWAITING_TTL = 300  # seconds before the "awaiting proxy input" state expires


def set_proxy_awaiting(admin_id: int, scheme: str, redis: Redis) -> None:
    """Remember which scheme (socks5h/https/...) the admin picked, until they
    send the actual proxy string (or the TTL expires)."""
    redis.setex(f"{_AWAITING_PREFIX}{admin_id}", _AWAITING_TTL, scheme)


def get_proxy_awaiting(admin_id: int, redis: Redis) -> str | None:
    raw = redis.get(f"{_AWAITING_PREFIX}{admin_id}")
    if raw is None:
        return None
    return raw.decode() if isinstance(raw, bytes) else raw


def clear_proxy_awaiting(admin_id: int, redis: Redis) -> None:
    redis.delete(f"{_AWAITING_PREFIX}{admin_id}")
