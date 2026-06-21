from __future__ import annotations

from aiogram import Router

from app.bot.routers.user import router as user_router
from app.bot.routers.admin import router as admin_router
from app.bot.routers.oauth import router as oauth_router
from app.bot.routers.broadcast import router as broadcast_router
from app.bot.routers.cookies import router as cookies_router
from app.bot.routers.url_handler import router as url_router

# Backward-compat re-exports used by existing tests and main.py.
from app.bot.access import _check_access, _is_admin, _is_allowed  # noqa: F401

router = Router()

# Registration order for non-command (generic) message handlers:
#   admin_router   — _AdminAwaitingFilter(F.text) must precede url_router's F.text
#   broadcast_router — BroadcastModeFilter must precede cookies_router's F.document
#   cookies_router — F.document must precede url_router's F.text / F.caption
#   url_router     — most generic; registered last
router.include_router(user_router)
router.include_router(admin_router)
router.include_router(oauth_router)
router.include_router(broadcast_router)
router.include_router(cookies_router)
router.include_router(url_router)
