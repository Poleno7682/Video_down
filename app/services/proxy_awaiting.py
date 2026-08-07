from __future__ import annotations

from redis import Redis

from app.services.redis_ttl import clear_ttl, get_ttl, set_ttl

_AWAITING_PREFIX = "admin_awaiting_proxy:"
_AWAITING_TTL = 300  # seconds before the "awaiting proxy input" state expires


def set_proxy_awaiting(admin_id: int, scheme: str, redis: Redis) -> None:
    """Remember which scheme (socks5h/https/...) the admin picked, until they
    send the actual proxy string (or the TTL expires)."""
    set_ttl(redis, f"{_AWAITING_PREFIX}{admin_id}", scheme, _AWAITING_TTL)


def get_proxy_awaiting(admin_id: int, redis: Redis) -> str | None:
    return get_ttl(redis, f"{_AWAITING_PREFIX}{admin_id}")


def clear_proxy_awaiting(admin_id: int, redis: Redis) -> None:
    clear_ttl(redis, f"{_AWAITING_PREFIX}{admin_id}")
