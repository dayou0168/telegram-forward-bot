from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta, timezone
from typing import Any

from aiogram import Bot
from aiogram.client.session.middlewares.base import BaseRequestMiddleware
from aiogram.exceptions import TelegramAPIError, TelegramRetryAfter
from aiogram.methods import CopyMessage, SendMessage
from aiogram.methods.base import TelegramMethod
from aiogram.types import Message, MessageId
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from bot.config import Settings
from bot import repositories as repo


BEIJING_TZ = timezone(timedelta(hours=8), "Asia/Shanghai")
PRIVATE_CLEANUP_CHECK_SECONDS = 30
SYSTEM_AUDIT_USER_ID = 0

logger = logging.getLogger(__name__)


def _chat_id_to_int(value: int | str | None) -> int | None:
    if value is None:
        return None
    if isinstance(value, int):
        return value
    if not isinstance(value, str):
        return None
    normalized = value.strip()
    if not normalized.lstrip("-").isdigit():
        return None
    return int(normalized)


def _result_message_id(result: Any) -> int | None:
    if isinstance(result, Message):
        return result.message_id
    if isinstance(result, MessageId):
        return result.message_id
    message_id = getattr(result, "message_id", None)
    return int(message_id) if isinstance(message_id, int) else None


class PrivateChatCleanupRequestMiddleware(BaseRequestMiddleware):
    def __init__(self, session_factory: async_sessionmaker[AsyncSession], settings: Settings) -> None:
        self.session_factory = session_factory
        self.settings = settings

    async def __call__(
        self,
        make_request: Any,
        bot: Bot,
        method: TelegramMethod[Any],
    ) -> Any:
        result = await make_request(bot, method)
        if isinstance(method, (SendMessage, CopyMessage)):
            await self._record_private_outgoing(method, result)
        return result

    async def _record_private_outgoing(self, method: TelegramMethod[Any], result: Any) -> None:
        chat_id = _chat_id_to_int(getattr(method, "chat_id", None))
        message_id = _result_message_id(result)
        if chat_id is None or chat_id <= 0 or message_id is None:
            return

        try:
            async with self.session_factory() as session:
                role = await repo.get_user_role(session, chat_id, self.settings.owner_user_ids)
                if role != "operator":
                    return
                flags = await repo.get_operator_feature_flags(session, chat_id)
                if not flags.private_cleanup_enabled:
                    return
                await repo.record_private_chat_message(
                    session,
                    operator_user_id=chat_id,
                    chat_id=chat_id,
                    message_id=message_id,
                    direction="outgoing",
                )
                await session.commit()
        except Exception:
            logger.exception("Failed to record outgoing private chat message for cleanup")


async def private_chat_cleanup_loop(
    bot: Bot,
    session_factory: async_sessionmaker[AsyncSession],
    *,
    check_seconds: int = PRIVATE_CLEANUP_CHECK_SECONDS,
) -> None:
    while True:
        try:
            await run_private_chat_cleanup_once(bot, session_factory)
        except Exception:
            logger.exception("Private chat cleanup loop failed")
        await asyncio.sleep(check_seconds)


async def run_private_chat_cleanup_once(
    bot: Bot,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    now_local = datetime.now(BEIJING_TZ)
    run_date = now_local.date().isoformat()
    async with session_factory() as session:
        targets = await repo.list_due_operator_private_cleanup_targets(session, now_local=now_local)

    for target in targets:
        try:
            await _run_operator_private_cleanup(
                bot=bot,
                session_factory=session_factory,
                operator_user_id=target.user_id,
                run_date=run_date,
            )
        except Exception:
            logger.exception("Failed to clean private chat for operator %s", target.user_id)


async def _run_operator_private_cleanup(
    *,
    bot: Bot,
    session_factory: async_sessionmaker[AsyncSession],
    operator_user_id: int,
    run_date: str,
) -> None:
    async with session_factory() as session:
        records = await repo.list_private_chat_messages(session, operator_user_id)

    attempted_count = len(records)
    deleted_count = 0
    max_record_id = max((record.id for record in records), default=None)
    for record in records:
        if await _delete_recorded_message(bot, record.chat_id, record.message_id):
            deleted_count += 1

    async with session_factory() as session:
        await repo.finish_private_chat_cleanup(
            session,
            operator_user_id=operator_user_id,
            run_date=run_date,
            max_record_id=max_record_id,
            attempted_count=attempted_count,
            deleted_count=deleted_count,
            changed_by=SYSTEM_AUDIT_USER_ID,
        )
        await session.commit()


async def _delete_recorded_message(bot: Bot, chat_id: int, message_id: int) -> bool:
    try:
        await bot.delete_message(chat_id=chat_id, message_id=message_id)
        return True
    except TelegramRetryAfter as exc:
        await asyncio.sleep(exc.retry_after)
        try:
            await bot.delete_message(chat_id=chat_id, message_id=message_id)
            return True
        except TelegramAPIError:
            return False
    except TelegramAPIError:
        return False
