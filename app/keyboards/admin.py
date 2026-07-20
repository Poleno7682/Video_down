from __future__ import annotations

from aiogram.types import InlineKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder

from app.services.runtime_config import EDITABLE_LIMITS, format_value


def admin_keyboard(bot_disabled: bool) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    if bot_disabled:
        builder.button(text="🟢 Включить бот для всех", callback_data="admin:toggle_access")
    else:
        builder.button(text="🔴 Выключить бот для всех", callback_data="admin:toggle_access")
    builder.button(text="📢 Рассылка", callback_data="admin:broadcast")
    builder.button(text="⚙️ Лимиты", callback_data="admin:limits")
    builder.adjust(1)
    return builder.as_markup()


def limits_keyboard(effective_values: dict[str, int]) -> InlineKeyboardMarkup:
    """Keyboard showing every editable limit with its current effective value."""
    builder = InlineKeyboardBuilder()
    for field, spec in EDITABLE_LIMITS.items():
        val = effective_values.get(field, 0)
        display = format_value(field, val)
        builder.button(
            text=f"{spec.emoji} {spec.label}: {display}",
            callback_data=f"limits:edit:{field}",
        )
    builder.button(text="↩️ Сбросить всё к настройкам .env", callback_data="limits:reset_all")
    builder.button(text="🔙 Назад", callback_data="limits:back")
    builder.adjust(1)
    return builder.as_markup()


def proxy_scheme_keyboard() -> InlineKeyboardMarkup:
    """Asks the admin to pick a scheme before entering the proxy itself —
    needed because the plain IP:PORT[...] formats don't carry a scheme.

    HTTP and HTTPS are genuinely different here, not just a label: in a
    proxy URL the scheme picks how *we* connect to the proxy itself
    (plain TCP vs. a TLS handshake to the proxy), independent of what the
    proxy then does with the request. Most proxy-list providers hand out
    plain HTTP proxies — picking HTTPS for one of those fails with
    SSL/wrong-version errors, not because the proxy is bad.
    """
    builder = InlineKeyboardBuilder()
    builder.button(text="🧦 SOCKS5", callback_data="proxy:scheme:socks5h")
    builder.button(text="🔌 HTTP", callback_data="proxy:scheme:http")
    builder.button(text="🔒 HTTPS", callback_data="proxy:scheme:https")
    builder.adjust(3)
    return builder.as_markup()


def broadcast_cancel_keyboard() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text="❌ Отменить рассылку", callback_data="broadcast:cancel")
    builder.adjust(1)
    return builder.as_markup()
