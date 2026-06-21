from __future__ import annotations

import asyncio
import logging

from aiohttp import web
from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.types import BotCommand, BotCommandScopeChat, BotCommandScopeDefault
from aiogram.webhook.aiohttp_server import SimpleRequestHandler, setup_application
from alembic.config import Config as AlembicConfig
from alembic import command as alembic_command

from app.bot.router import router
from app.core.config import get_settings
from app.core.logging import setup_logging


_USER_COMMANDS = [
    BotCommand(command="start",        description="Справка по боту"),
    BotCommand(command="quality",      description="Выбрать качество видео"),
    BotCommand(command="status",       description="Статус очереди"),
    BotCommand(command="cookies",      description="Личные cookies для скачивания"),
    BotCommand(command="link_google",  description="Привязать Google-аккаунт (YouTube)"),
    BotCommand(command="unlink_google", description="Отвязать Google-аккаунт"),
]

_ADMIN_COMMANDS = _USER_COMMANDS + [
    BotCommand(command="admin",      description="⚙️ Панель администратора"),
    BotCommand(command="broadcast",  description="📢 Рассылка всем пользователям"),
    BotCommand(command="adduser",    description="Добавить доверенного пользователя"),
    BotCommand(command="removeuser", description="Удалить пользователя из доверенных"),
    BotCommand(command="listusers",  description="Список доверенных пользователей"),
]


def _apply_migrations() -> None:
    alembic_cfg = AlembicConfig("alembic.ini")
    alembic_command.upgrade(alembic_cfg, "head")
    logging.info("Database migrations applied")


async def _register_commands(bot: Bot) -> None:
    settings = get_settings()

    # Default command list for regular users
    await bot.set_my_commands(_USER_COMMANDS, scope=BotCommandScopeDefault())

    # Extended command list for each admin (personal scope overrides default)
    for admin_id in settings.admin_user_ids:
        try:
            await bot.set_my_commands(
                _ADMIN_COMMANDS,
                scope=BotCommandScopeChat(chat_id=admin_id),
            )
        except Exception:
            # Admin hasn't started the bot yet — commands will be set on first access
            pass

    logging.info("Bot commands registered (%d admin(s))", len(settings.admin_user_ids))


async def on_startup(bot: Bot) -> None:
    settings = get_settings()

    _apply_migrations()

    await bot.set_webhook(
        url=settings.webhook_url,
        secret_token=settings.webhook_secret,
        drop_pending_updates=True,
    )
    logging.info("Webhook set: %s", settings.webhook_url)

    await _register_commands(bot)


async def on_shutdown(bot: Bot) -> None:
    await bot.delete_webhook(drop_pending_updates=False)
    await bot.session.close()
    logging.info("Webhook deleted")


async def health(request: web.Request) -> web.Response:
    return web.json_response({"status": "ok"})


def _run_webhook(bot: Bot, dp: Dispatcher, settings) -> None:
    dp.startup.register(on_startup)
    dp.shutdown.register(on_shutdown)

    app = web.Application()
    app.router.add_get("/health", health)

    SimpleRequestHandler(
        dispatcher=dp,
        bot=bot,
        secret_token=settings.webhook_secret,
    ).register(app, path=settings.webhook_path)

    setup_application(app, dp, bot=bot)

    web.run_app(app, host=settings.app_host, port=settings.app_port)


def _run_polling(bot: Bot, dp: Dispatcher, settings) -> None:
    async def _runner() -> None:
        _apply_migrations()
        # Alembic reconfigures logging during migrations — restore ours.
        setup_logging(settings.log_dir)

        # Remove any existing webhook so getUpdates (polling) is allowed.
        await bot.delete_webhook(drop_pending_updates=True)
        await _register_commands(bot)

        # Lightweight health endpoint so Docker/healthchecks keep working.
        app = web.Application()
        app.router.add_get("/health", health)
        runner = web.AppRunner(app)
        await runner.setup()
        site = web.TCPSite(runner, settings.app_host, settings.app_port)
        await site.start()
        logging.info("Polling mode started (health on %s:%s)", settings.app_host, settings.app_port)

        try:
            await dp.start_polling(bot)
        finally:
            await runner.cleanup()
            await bot.session.close()

    asyncio.run(_runner())


def main() -> None:
    settings = get_settings()
    setup_logging(settings.log_dir)

    bot = Bot(
        token=settings.bot_token,
        default=DefaultBotProperties(parse_mode=ParseMode.HTML),
    )

    dp = Dispatcher()
    dp.include_router(router)

    if settings.bot_mode == "polling":
        _run_polling(bot, dp, settings)
    else:
        _run_webhook(bot, dp, settings)


if __name__ == "__main__":  # pragma: no cover
    main()
