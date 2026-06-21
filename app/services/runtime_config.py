from __future__ import annotations

from dataclasses import dataclass

from redis import Redis

from app.core.config import Settings

_LIMIT_PREFIX = "runtime_limit:"
_AWAITING_PREFIX = "admin_awaiting_limit:"
_AWAITING_TTL = 300  # seconds before the "awaiting input" state expires


@dataclass(frozen=True)
class LimitSpec:
    emoji: str
    label: str          # shown in messages and keyboard buttons
    unit: str           # e.g. "шт.", "МБ", "сек.", "ч."
    min_val: int
    max_val: int
    zero_disables: bool # True → user can set 0 to disable the limit entirely


# Single source of truth for all admin-editable limits.
# Order determines display order in the keyboard.
EDITABLE_LIMITS: dict[str, LimitSpec] = {
    "user_daily_limit": LimitSpec(
        "📅", "Загрузок в сутки / пользователь", "шт.", 0, 10_000, True,
    ),
    "user_queue_limit": LimitSpec(
        "📋", "Задач в очереди / пользователь", "шт.", 0, 50, True,
    ),
    "global_queue_limit": LimitSpec(
        "🌐", "Глобальная очередь", "задач", 0, 1_000, True,
    ),
    "max_active_downloads_per_user": LimitSpec(
        "⬇️", "Параллельных загрузок / пользователь", "шт.", 0, 20, True,
    ),
    "max_file_mb": LimitSpec(
        "📦", "Макс. размер файла", "МБ", 0, 2_000, True,
    ),
    "rate_limit_max_messages": LimitSpec(
        "✉️", "Сообщений в окне rate-limit", "шт.", 0, 500, True,
    ),
    "rate_limit_window_seconds": LimitSpec(
        "⏱", "Окно rate-limit", "сек.", 10, 3_600, False,
    ),
    "ban_seconds": LimitSpec(
        "🚫", "Длительность бана", "сек.", 0, 86_400, True,
    ),
    "max_download_duration_seconds": LimitSpec(
        "⏳", "Макс. время одной загрузки", "сек.", 60, 7_200, False,
    ),
    "cache_ttl_hours": LimitSpec(
        "🗄", "TTL кэша file_id", "ч.", 1, 8_760, False,
    ),
}


def get_limit(field: str, settings: Settings, redis: Redis) -> int:
    """Return the effective value: Redis override if set, otherwise from Settings."""
    raw = redis.get(f"{_LIMIT_PREFIX}{field}")
    if raw is not None:
        return int(raw)
    return int(getattr(settings, field))


def set_limit(field: str, value: int, redis: Redis) -> None:
    redis.set(f"{_LIMIT_PREFIX}{field}", str(value))


def reset_limit(field: str, redis: Redis) -> None:
    redis.delete(f"{_LIMIT_PREFIX}{field}")


def reset_all_limits(redis: Redis) -> None:
    keys = redis.keys(f"{_LIMIT_PREFIX}*")
    if keys:
        redis.delete(*keys)


def format_value(field: str, value: int) -> str:
    """Human-readable value display, e.g. '∞' when the limit is disabled."""
    spec = EDITABLE_LIMITS[field]
    if spec.zero_disables and value == 0:
        return "∞ (отключён)"
    return f"{value} {spec.unit}"


# ---------------------------------------------------------------------------
# Admin "awaiting limit input" state
# ---------------------------------------------------------------------------

def set_awaiting(admin_id: int, field: str, redis: Redis) -> None:
    redis.setex(f"{_AWAITING_PREFIX}{admin_id}", _AWAITING_TTL, field)


def get_awaiting(admin_id: int, redis: Redis) -> str | None:
    raw = redis.get(f"{_AWAITING_PREFIX}{admin_id}")
    return raw.decode() if raw else None


def clear_awaiting(admin_id: int, redis: Redis) -> None:
    redis.delete(f"{_AWAITING_PREFIX}{admin_id}")
