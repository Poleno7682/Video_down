from __future__ import annotations

import logging

from redis import Redis

logger = logging.getLogger(__name__)

# Lua script: atomically check ban, increment rate counter, ban if exceeded.
# Returns [allowed (0/1), ban_ttl]
_HIT_OR_BAN_LUA = """
local ban_key = KEYS[1]
local rate_key = KEYS[2]
local window = tonumber(ARGV[1])
local max_msgs = tonumber(ARGV[2])
local ban_seconds = tonumber(ARGV[3])

local ban_ttl = redis.call('TTL', ban_key)
if ban_ttl > 0 then
    return {0, ban_ttl}
end

local count = redis.call('INCR', rate_key)
local current_ttl = redis.call('TTL', rate_key)
if current_ttl < 0 then
    redis.call('EXPIRE', rate_key, window)
end

if count > max_msgs then
    redis.call('SETEX', ban_key, ban_seconds, '1')
    return {0, ban_seconds}
end

return {1, 0}
"""

# Lua script: atomically increment slot counter, set TTL on first use,
# roll back if over limit. Returns 1 on success, 0 on failure.
_ACQUIRE_SLOT_LUA = """
local key = KEYS[1]
local max_active = tonumber(ARGV[1])
local ttl = tonumber(ARGV[2])

local count = redis.call('INCR', key)
local current_ttl = redis.call('TTL', key)
if current_ttl < 0 then
    redis.call('EXPIRE', key, ttl)
end
if count > max_active then
    redis.call('DECR', key)
    return 0
end
return 1
"""


class RateLimiter:
    def __init__(self, redis: Redis):
        self.redis = redis
        self._hit_or_ban = redis.register_script(_HIT_OR_BAN_LUA)
        self._acquire_slot = redis.register_script(_ACQUIRE_SLOT_LUA)

    def is_banned(self, user_id: int) -> int:
        ttl = self.redis.ttl(f"ban:user:{user_id}")
        return max(ttl, 0)

    def ban(self, user_id: int, seconds: int) -> None:
        self.redis.setex(f"ban:user:{user_id}", seconds, "1")

    def hit_or_ban(
        self,
        user_id: int,
        window_seconds: int,
        max_messages: int,
        ban_seconds: int,
    ) -> tuple[bool, int]:
        result = self._hit_or_ban(
            keys=[f"ban:user:{user_id}", f"rate:user:{user_id}"],
            args=[window_seconds, max_messages, ban_seconds],
        )
        return bool(int(result[0])), int(result[1])

    def acquire_user_download_slot(self, user_id: int, max_active: int, ttl: int) -> bool:
        result = self._acquire_slot(
            keys=[f"active_downloads:user:{user_id}"],
            args=[max_active, ttl],
        )
        return bool(int(result))

    def release_user_download_slot(self, user_id: int) -> None:
        key = f"active_downloads:user:{user_id}"
        try:
            val = self.redis.get(key)
            if val is not None and int(val) > 0:
                self.redis.decr(key)
        except Exception:
            logger.warning("Failed to release download slot for user %s", user_id)

    def acquire_video_lock(self, url_hash: str, quality: str, ttl: int) -> bool:
        key = f"lock:video:{url_hash}:{quality}"
        return bool(self.redis.set(key, "1", ex=ttl, nx=True))

    def release_video_lock(self, url_hash: str, quality: str) -> None:
        self.redis.delete(f"lock:video:{url_hash}:{quality}")
