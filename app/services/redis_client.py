from __future__ import annotations

import threading

from redis import Redis

from app.core.config import get_settings

_redis: Redis | None = None
_lock = threading.Lock()


def get_redis() -> Redis:
    global _redis
    with _lock:
        if _redis is None:
            settings = get_settings()
            _redis = Redis.from_url(settings.redis_url, decode_responses=True)
    return _redis
