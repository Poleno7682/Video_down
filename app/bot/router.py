from __future__ import annotations

from aiogram import Router

from app.bot.middleware import AccessMiddleware
from app.bot.routers.admin import router as admin_router
from app.bot.routers.broadcast import router as broadcast_router
from app.bot.routers.cookies import router as cookies_router
from app.bot.routers.oauth import router as oauth_router
from app.bot.routers.url_handler import router as url_router
from app.bot.routers.user import router as user_router

# Backward-compat re-exports used by main.py and any external imports.
from app.bot.access import _check_access, _is_admin, _is_allowed  # noqa: F401

# ---------------------------------------------------------------------------
# Protected sub-router: AccessMiddleware blocks non-authorised users before
# any Message handler registered here reaches the handler body.
# ---------------------------------------------------------------------------
_protected = Router()
_protected.message.middleware(AccessMiddleware())
_protected.include_router(oauth_router)
_protected.include_router(broadcast_router)   # BroadcastModeFilter first → catches admin docs
_protected.include_router(cookies_router)      # F.document before F.text / F.caption
_protected.include_router(url_router)          # most generic — last

# ---------------------------------------------------------------------------
# Main aggregator
# ---------------------------------------------------------------------------
router = Router()

# Registration order for non-command message handlers:
#   admin_router  — _AdminAwaitingFilter(F.text) must precede url_router's F.text
#   user_router   — /start and /quality are public; /status has no access guard
#                   so it relies on middleware via _protected (TODO: move in refactor 03)
# Note: user /start and /quality are public (no access check needed).
#       /status goes through AccessMiddleware once moved, but currently stays public
#       because it's useful for debugging even when disabled.
router.include_router(user_router)
router.include_router(admin_router)
router.include_router(_protected)
