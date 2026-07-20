from __future__ import annotations

import json

from redis import Redis

_SESSION_PREFIX = "rezka_session:"
_SESSION_TTL = 600  # seconds — enough for a user to click through translator -> season -> episode


def _key(user_id: int) -> str:
    return f"{_SESSION_PREFIX}{user_id}"


def set_rezka_session(user_id: int, data: dict, redis: Redis) -> None:
    """(Re)store the in-progress translator/season/episode selection for
    user_id, resetting the TTL — called after every step so a user who
    takes their time clicking through doesn't lose progress mid-flow."""
    redis.setex(_key(user_id), _SESSION_TTL, json.dumps(data))


def get_rezka_session(user_id: int, redis: Redis) -> dict | None:
    raw = redis.get(_key(user_id))
    if not raw:
        return None
    try:
        return json.loads(raw)
    except (TypeError, ValueError):
        return None


def clear_rezka_session(user_id: int, redis: Redis) -> None:
    redis.delete(_key(user_id))
