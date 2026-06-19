from __future__ import annotations

import asyncio
import logging

from aiogram import Bot, Dispatcher
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import BotCommand, BotCommandScopeAllGroupChats, BotCommandScopeAllPrivateChats

from bot.config import load_settings
from bot.db import DbSessionMiddleware, init_db, make_session_factory
from bot.handlers import router
from bot.repositories import bootstrap_legacy_operator_group_permissions, ensure_owner_users


async def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    settings = load_settings()
    session_factory = make_session_factory(settings.database_url)
    await init_db(session_factory)

    async with session_factory() as session:
        await ensure_owner_users(session, settings.owner_user_ids)
        await bootstrap_legacy_operator_group_permissions(session, min(settings.owner_user_ids))
        await session.commit()

    bot = Bot(token=settings.bot_token)
    dispatcher = Dispatcher(storage=MemoryStorage())
    dispatcher.update.middleware(DbSessionMiddleware(session_factory, settings))
    dispatcher.include_router(router)

    private_commands = [
        BotCommand(command="start", description="打开机器人菜单"),
        BotCommand(command="menu", description="打开机器人菜单"),
        BotCommand(command="id", description="查询我的 Telegram UID"),
    ]
    group_commands = [
        BotCommand(command="register", description="登记当前群组"),
    ]
    await bot.set_my_commands(private_commands)
    await bot.set_my_commands(private_commands, scope=BotCommandScopeAllPrivateChats())
    await bot.set_my_commands(group_commands, scope=BotCommandScopeAllGroupChats())
    await bot.delete_webhook(drop_pending_updates=True)
    await dispatcher.start_polling(bot, allowed_updates=dispatcher.resolve_used_update_types())


if __name__ == "__main__":
    asyncio.run(main())
