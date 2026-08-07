from __future__ import annotations

from redis import Redis

from app.services.redis_ttl import clear_ttl, get_ttl_json, set_ttl_json

_SESSION_PREFIX = "rezka_session:"
_SESSION_TTL = 600  # seconds — enough for a user to click through translator -> season -> episode


def _key(user_id: int) -> str:
    return f"{_SESSION_PREFIX}{user_id}"


def set_rezka_session(user_id: int, data: dict, redis: Redis) -> None:
    """(Re)store the in-progress translator/season/episode selection for
    user_id, resetting the TTL — called after every step so a user who
    takes their time clicking through doesn't lose progress mid-flow."""
    set_ttl_json(redis, _key(user_id), data, _SESSION_TTL)


def get_rezka_session(user_id: int, redis: Redis) -> dict | None:
    return get_ttl_json(redis, _key(user_id))


def clear_rezka_session(user_id: int, redis: Redis) -> None:
    clear_ttl(redis, _key(user_id))
