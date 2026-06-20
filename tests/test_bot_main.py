from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from aiohttp import web


# ---------------------------------------------------------------------------
# on_startup
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_on_startup_runs_alembic_and_sets_webhook():
    from app.bot.main import on_startup

    settings = MagicMock()
    settings.webhook_url = "https://example.com/bot/webhook"
    settings.webhook_secret = "s3cr3t"
    settings.admin_user_ids = set()

    bot = MagicMock()
    bot.set_webhook = AsyncMock()
    bot.set_my_commands = AsyncMock()

    with patch("app.bot.main.get_settings", return_value=settings), \
         patch("app.bot.main.alembic_command") as mock_alembic, \
         patch("app.bot.main.AlembicConfig"):
        await on_startup(bot)

    mock_alembic.upgrade.assert_called_once()
    assert mock_alembic.upgrade.call_args[0][1] == "head"
    bot.set_webhook.assert_awaited_once_with(
        url=settings.webhook_url,
        secret_token=settings.webhook_secret,
        drop_pending_updates=True,
    )


@pytest.mark.asyncio
async def test_on_startup_calls_upgrade_head():
    from app.bot.main import on_startup

    settings = MagicMock()
    settings.admin_user_ids = set()
    bot = MagicMock()
    bot.set_webhook = AsyncMock()
    bot.set_my_commands = AsyncMock()

    with patch("app.bot.main.get_settings", return_value=settings), \
         patch("app.bot.main.alembic_command") as mock_alembic, \
         patch("app.bot.main.AlembicConfig"):
        await on_startup(bot)

    args = mock_alembic.upgrade.call_args[0]
    assert args[1] == "head"


@pytest.mark.asyncio
async def test_on_startup_sets_user_commands():
    from app.bot.main import on_startup, _USER_COMMANDS

    settings = MagicMock()
    settings.admin_user_ids = set()
    bot = MagicMock()
    bot.set_webhook = AsyncMock()
    bot.set_my_commands = AsyncMock()

    with patch("app.bot.main.get_settings", return_value=settings), \
         patch("app.bot.main.alembic_command"), \
         patch("app.bot.main.AlembicConfig"):
        await on_startup(bot)

    # First call should be the default scope with user commands
    first_call = bot.set_my_commands.call_args_list[0]
    assert first_call[0][0] == _USER_COMMANDS


@pytest.mark.asyncio
async def test_on_startup_sets_admin_commands_for_each_admin():
    from app.bot.main import on_startup, _ADMIN_COMMANDS

    settings = MagicMock()
    settings.admin_user_ids = {111, 222}
    bot = MagicMock()
    bot.set_webhook = AsyncMock()
    bot.set_my_commands = AsyncMock()

    with patch("app.bot.main.get_settings", return_value=settings), \
         patch("app.bot.main.alembic_command"), \
         patch("app.bot.main.AlembicConfig"):
        await on_startup(bot)

    # set_my_commands: once for default + once per admin
    assert bot.set_my_commands.await_count == 1 + len(settings.admin_user_ids)


@pytest.mark.asyncio
async def test_on_startup_swallows_admin_commands_exception():
    """Exception setting admin commands (admin not started bot) is swallowed → main.py:57-59."""
    from app.bot.main import on_startup

    settings = MagicMock()
    settings.admin_user_ids = {99}
    bot = MagicMock()
    bot.set_webhook = AsyncMock()
    # First call (default scope) succeeds; second call (admin scope) raises
    bot.set_my_commands = AsyncMock(
        side_effect=[None, Exception("admin hasn't started bot")]
    )

    with patch("app.bot.main.get_settings", return_value=settings), \
         patch("app.bot.main.alembic_command"), \
         patch("app.bot.main.AlembicConfig"):
        await on_startup(bot)  # must not raise

    assert bot.set_my_commands.await_count == 2


# ---------------------------------------------------------------------------
# on_shutdown
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_on_shutdown_deletes_webhook_and_closes_session():
    from app.bot.main import on_shutdown

    bot = MagicMock()
    bot.delete_webhook = AsyncMock()
    bot.session.close = AsyncMock()

    await on_shutdown(bot)

    bot.delete_webhook.assert_awaited_once_with(drop_pending_updates=False)
    bot.session.close.assert_awaited_once()


# ---------------------------------------------------------------------------
# health endpoint
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_health_returns_ok():
    from app.bot.main import health
    import json

    request = MagicMock()
    response = await health(request)

    assert isinstance(response, web.Response)
    data = json.loads(response.body)
    assert data["status"] == "ok"


# ---------------------------------------------------------------------------
# main() function
# ---------------------------------------------------------------------------

def test_main_sets_up_bot_and_runs_app():
    from app.bot.main import main

    settings = MagicMock()
    settings.bot_token = "123456789:AATestToken"
    settings.webhook_secret = "secret"
    settings.webhook_path = "/bot/webhook"
    settings.app_host = "0.0.0.0"
    settings.app_port = 8080
    settings.log_dir = "/tmp/logs"

    with patch("app.bot.main.get_settings", return_value=settings), \
         patch("app.bot.main.setup_logging"), \
         patch("app.bot.main.Bot"), \
         patch("app.bot.main.Dispatcher") as mock_dp_cls, \
         patch("app.bot.main.SimpleRequestHandler"), \
         patch("app.bot.main.setup_application"), \
         patch("app.bot.main.web.Application"), \
         patch("app.bot.main.web.run_app") as mock_run:

        mock_dp = MagicMock()
        mock_dp_cls.return_value = mock_dp

        main()

    mock_dp.include_router.assert_called_once()
    mock_dp.startup.register.assert_called_once()
    mock_dp.shutdown.register.assert_called_once()
    mock_run.assert_called_once()


def test_main_registers_health_route():
    from app.bot.main import main

    settings = MagicMock()
    settings.bot_token = "123456789:AATestToken"
    settings.webhook_secret = "secret"
    settings.webhook_path = "/bot/webhook"
    settings.app_host = "0.0.0.0"
    settings.app_port = 8080
    settings.log_dir = "/tmp/logs"

    added_routes = []
    mock_app = MagicMock()
    mock_app.router.add_get = MagicMock(
        side_effect=lambda path, handler: added_routes.append(path)
    )

    with patch("app.bot.main.get_settings", return_value=settings), \
         patch("app.bot.main.setup_logging"), \
         patch("app.bot.main.Bot"), \
         patch("app.bot.main.Dispatcher"), \
         patch("app.bot.main.SimpleRequestHandler"), \
         patch("app.bot.main.setup_application"), \
         patch("app.bot.main.web.Application", return_value=mock_app), \
         patch("app.bot.main.web.run_app"):
        main()

    assert "/health" in added_routes
