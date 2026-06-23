from __future__ import annotations

from collections.abc import AsyncIterator, Awaitable, Callable
from typing import Any

from aiogram import BaseMiddleware
from sqlalchemy import event, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from bot.config import Settings, ensure_sqlite_parent_dir
from bot.models import Base


def make_session_factory(database_url: str) -> async_sessionmaker[AsyncSession]:
    ensure_sqlite_parent_dir(database_url)
    engine = create_async_engine(database_url, echo=False, future=True)

    if database_url.startswith("sqlite+aiosqlite"):
        @event.listens_for(engine.sync_engine, "connect")
        def _enable_sqlite_foreign_keys(dbapi_connection: Any, _: Any) -> None:
            cursor = dbapi_connection.cursor()
            cursor.execute("PRAGMA foreign_keys=ON")
            cursor.close()

    return async_sessionmaker(engine, expire_on_commit=False)


async def init_db(session_factory: async_sessionmaker[AsyncSession]) -> None:
    engine = session_factory.kw["bind"]
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        if str(engine.url).startswith("sqlite+aiosqlite"):
            result = await conn.execute(text("PRAGMA table_info(operator_feature_permissions)"))
            columns = {row[1] for row in result.fetchall()}
            if "allow_manage_operators" not in columns:
                await conn.execute(
                    text(
                        "ALTER TABLE operator_feature_permissions "
                        "ADD COLUMN allow_manage_operators BOOLEAN NOT NULL DEFAULT 1"
                    )
                )
            if "receive_sent_notifications" not in columns:
                await conn.execute(
                    text(
                        "ALTER TABLE operator_feature_permissions "
                        "ADD COLUMN receive_sent_notifications BOOLEAN NOT NULL DEFAULT 0"
                    )
                )
            if "receive_reply_notifications" not in columns:
                await conn.execute(
                    text(
                        "ALTER TABLE operator_feature_permissions "
                        "ADD COLUMN receive_reply_notifications BOOLEAN NOT NULL DEFAULT 0"
                    )
                )


class DbSessionMiddleware(BaseMiddleware):
    def __init__(self, session_factory: async_sessionmaker[AsyncSession], settings: Settings) -> None:
        self.session_factory = session_factory
        self.settings = settings

    async def __call__(
        self,
        handler: Callable[[Any, dict[str, Any]], Awaitable[Any]],
        event: Any,
        data: dict[str, Any],
    ) -> Any:
        async with self.session_factory() as session:
            data["session"] = session
            data["settings"] = self.settings
            try:
                result = await handler(event, data)
                await session.commit()
                return result
            except Exception:
                await session.rollback()
                raise
