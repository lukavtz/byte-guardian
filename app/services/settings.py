"""Persistent key/value settings and short-lived broadcast drafts."""

from __future__ import annotations

import json
import time
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

DRAFT_TTL_SECONDS = 3600
DRAFT_KEY_PREFIX = "broadcast_draft:"

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


def _decode(value: Any) -> Any:
    return json.loads(value) if isinstance(value, str) else value


async def settings_get(
    session_factory: async_sessionmaker[AsyncSession], key: str
) -> Any | None:
    async with session_factory() as session:
        async with session.begin():
            row = (await session.execute(SELECT_SQL, {"key": key})).first()
    if row is None:
        return None
    value = row[0]
    return json.loads(value) if isinstance(value, str) else value


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
    value = _decode(row[0])
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