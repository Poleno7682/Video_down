from __future__ import annotations

import json

from redis import Redis


def set_ttl(redis: Redis, key: str, value: str, ttl: int) -> None:
    redis.setex(key, ttl, value)


def get_ttl(redis: Redis, key: str) -> str | None:
    raw = redis.get(key)
    if raw is None:
        return None
    return raw.decode() if isinstance(raw, bytes) else raw


def set_ttl_json(redis: Redis, key: str, value: dict, ttl: int) -> None:
    redis.setex(key, ttl, json.dumps(value))


def get_ttl_json(redis: Redis, key: str) -> dict | None:
    raw = redis.get(key)
    if not raw:
        return None
    try:
        return json.loads(raw)
    except (TypeError, ValueError):
        return None


def exists_ttl(redis: Redis, key: str) -> bool:
    return bool(redis.exists(key))


def clear_ttl(redis: Redis, key: str) -> None:
    redis.delete(key)
