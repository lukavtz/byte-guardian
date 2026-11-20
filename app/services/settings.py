"""Persistent key/value settings and short-lived broadcast drafts."""

from __future__ import annotations

import json
import math
import time
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

DRAFT_TTL_SECONDS = 3600
DRAFT_KEY_PREFIX = "broadcast_draft:"
CHAT_MODE_PREFIX = "chat_mode:"
EDIT_KEY_PREFIX = "admin_edit:"
# A field prompt that is not answered in ten minutes is stale: the admin
# moved on, and the next plain message must not overwrite a card.
EDIT_TTL_SECONDS = 600
EDIT_AT_KEY = "_at"

SELECT_SQL = text("SELECT value FROM settings WHERE key = :key")
UPSERT_SQL = text(
    """
    INSERT INTO settings (key, value)
    VALUES (:key, CAST(:value AS jsonb))
    ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value
    """
)
DELETE_SQL = text("DELETE FROM settings WHERE key = :key")
TAKE_SQL = text("DELETE FROM settings WHERE key = :key RETURNING value")


async def settings_get(
    session_factory: async_sessionmaker[AsyncSession], key: str
) -> Any | None:
    async with session_factory() as session:
        async with session.begin():
            row = (await session.execute(SELECT_SQL, {"key": key})).first()
    if row is None:
        return None
    # The asyncpg dialect decodes jsonb itself; a second json.loads here turns
    # "1" into 1 and crashes on plain strings.
    return row[0]


async def settings_set(
    session_factory: async_sessionmaker[AsyncSession], key: str, value: Any
) -> None:
    async with session_factory() as session:
        async with session.begin():
            await session.execute(UPSERT_SQL, {"key": key, "value": json.dumps(value)})


async def settings_delete(
    session_factory: async_sessionmaker[AsyncSession], key: str
) -> None:
    async with session_factory() as session:
        async with session.begin():
            await session.execute(DELETE_SQL, {"key": key})


def draft_key(admin_id: int) -> str:
    return DRAFT_KEY_PREFIX + str(admin_id)


def chat_mode_key(tg_id: int) -> str:
    return CHAT_MODE_PREFIX + str(tg_id)


def edit_key(tg_id: int) -> str:
    return EDIT_KEY_PREFIX + str(tg_id)


async def edit_pending_save(
    session_factory: async_sessionmaker[AsyncSession], tg_id: int, value: dict[str, str]
) -> None:
    # jsonb values stay strings: the epoch rides as text next to the field.
    await settings_set(
        session_factory,
        edit_key(tg_id),
        {**value, EDIT_AT_KEY: str(time.time())},
    )


async def edit_pending(
    session_factory: async_sessionmaker[AsyncSession], tg_id: int
) -> dict[str, str] | None:
    value = await settings_get(session_factory, edit_key(tg_id))
    if not isinstance(value, dict):
        return None
    if not all(isinstance(item, str) for item in value.values()):
        return None
    stamp = value.pop(EDIT_AT_KEY, None)
    if stamp is None:
        await edit_pending_discard(session_factory, tg_id)
        return None
    try:
        saved_at = float(stamp)
    except ValueError:
        await edit_pending_discard(session_factory, tg_id)
        return None
    if not math.isfinite(saved_at):
        await edit_pending_discard(session_factory, tg_id)
        return None
    if time.time() - saved_at >= EDIT_TTL_SECONDS:
        await edit_pending_discard(session_factory, tg_id)
        return None
    return value


async def edit_pending_take(
    session_factory: async_sessionmaker[AsyncSession], tg_id: int
) -> dict[str, str] | None:
    value = await edit_pending(session_factory, tg_id)
    if value is not None:
        await settings_delete(session_factory, edit_key(tg_id))
    return value


async def edit_pending_discard(
    session_factory: async_sessionmaker[AsyncSession], tg_id: int
) -> None:
    await settings_delete(session_factory, edit_key(tg_id))


async def chat_mode_on(
    session_factory: async_sessionmaker[AsyncSession], tg_id: int
) -> bool:
    return await settings_get(session_factory, chat_mode_key(tg_id)) == "1"


async def set_chat_mode(
    session_factory: async_sessionmaker[AsyncSession], tg_id: int, on: bool
) -> None:
    await settings_set(session_factory, chat_mode_key(tg_id), "1" if on else "0")


async def draft_save(
    session_factory: async_sessionmaker[AsyncSession], admin_id: int, html: str
) -> None:
    await settings_set(
        session_factory,
        draft_key(admin_id),
        {"html": html, "created_at": time.time()},
    )


async def draft_take(
    session_factory: async_sessionmaker[AsyncSession], admin_id: int
) -> str | None:
    # DELETE ... RETURNING consumes the draft atomically: a double tap can
    # never broadcast the same draft twice.
    key = draft_key(admin_id)
    async with session_factory() as session:
        async with session.begin():
            row = (await session.execute(TAKE_SQL, {"key": key})).first()
    if row is None:
        return None
    value = row[0]
    if not isinstance(value, dict):
        return None
    created_at = value.get("created_at")
    html = value.get("html")
    if not isinstance(created_at, (int, float)) or isinstance(created_at, bool):
        return None
    if time.time() - float(created_at) >= DRAFT_TTL_SECONDS:
        return None
    return html if isinstance(html, str) and html else None


async def draft_discard(
    session_factory: async_sessionmaker[AsyncSession], admin_id: int
) -> None:
    await settings_delete(session_factory, draft_key(admin_id))