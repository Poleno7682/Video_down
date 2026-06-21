from __future__ import annotations

from redis import Redis

from app.core.config import Settings

_KEY_BOT_DISABLED = "bot:disabled"
_KEY_TRUSTED_USERS = "trusted_users"


def _is_admin(user_id: int, settings: Settings) -> bool:
    return user_id in settings.admin_user_ids


def _check_access(user_id: int, settings: Settings, redis: Redis) -> tuple[bool, str]:
    """Return (is_allowed, denial_message).

    Priority order:
    1. Admins → always allowed.
    2. Global kill switch (bot:disabled) → deny all non-admins.
    3. Static whitelist (ALLOWED_USERS in .env) → check membership.
    4. Dynamic trusted list (Redis SET trusted_users) → check membership.
    5. Neither list populated → public bot, everyone allowed.
    """
    if _is_admin(user_id, settings):
        return True, ""

    if redis.exists(_KEY_BOT_DISABLED):
        return False, "🔴 Бот временно недоступен. Попробуй позже."

    if settings.allowed_user_ids:
        if user_id in settings.allowed_user_ids:
            return True, ""
        return False, "⛔ У тебя нет доступа к этому боту."

    if redis.scard(_KEY_TRUSTED_USERS) > 0:
        if redis.sismember(_KEY_TRUSTED_USERS, str(user_id)):
            return True, ""
        return False, "⛔ У тебя нет доступа к этому боту."

    return True, ""


def _is_allowed(user_id: int, allowed: set[int]) -> bool:
    return not allowed or user_id in allowed
