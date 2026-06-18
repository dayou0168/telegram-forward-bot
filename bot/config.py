from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv


def _parse_bool(value: str | None, default: bool) -> bool:
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "y", "on"}


def _parse_user_ids(value: str | None) -> frozenset[int]:
    if not value:
        return frozenset()

    user_ids: set[int] = set()
    for item in value.split(","):
        item = item.strip()
        if not item:
            continue
        try:
            user_ids.add(int(item))
        except ValueError as exc:
            raise RuntimeError(f"Invalid Telegram user id in OWNER_USER_IDS: {item}") from exc
    return frozenset(user_ids)


@dataclass(frozen=True)
class Settings:
    bot_token: str
    owner_user_ids: frozenset[int]
    database_url: str
    unauthorized_reply: bool
    send_delay_seconds: float


def load_settings() -> Settings:
    load_dotenv()

    bot_token = os.getenv("BOT_TOKEN", "").strip()
    if not bot_token:
        raise RuntimeError("BOT_TOKEN is required")

    owner_user_ids = _parse_user_ids(os.getenv("OWNER_USER_IDS"))
    if not owner_user_ids:
        raise RuntimeError("OWNER_USER_IDS is required")

    database_url = os.getenv("DATABASE_URL", "sqlite+aiosqlite:///./data/bot.db").strip()
    if not database_url:
        raise RuntimeError("DATABASE_URL is required")

    try:
        send_delay_seconds = float(os.getenv("SEND_DELAY_SECONDS", "0.08"))
    except ValueError as exc:
        raise RuntimeError("SEND_DELAY_SECONDS must be a number") from exc

    return Settings(
        bot_token=bot_token,
        owner_user_ids=owner_user_ids,
        database_url=database_url,
        unauthorized_reply=_parse_bool(os.getenv("UNAUTHORIZED_REPLY"), True),
        send_delay_seconds=max(0.0, send_delay_seconds),
    )


def ensure_sqlite_parent_dir(database_url: str) -> None:
    prefix = "sqlite+aiosqlite:///"
    if not database_url.startswith(prefix):
        return

    raw_path = database_url.removeprefix(prefix)
    if raw_path in {":memory:", ""}:
        return

    db_path = Path(raw_path)
    if db_path.parent:
        db_path.parent.mkdir(parents=True, exist_ok=True)
