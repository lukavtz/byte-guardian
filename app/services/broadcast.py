"""Broadcast audience, persistence and paced delivery primitives."""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Any

import asyncpg
from aiogram import Bot
from aiogram.exceptions import TelegramRetryAfter
from redis.asyncio import Redis
from sqlalchemy import text
from sqlalchemy.exc import ProgrammingError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

logger = logging.getLogger(__name__)

PER_SECOND = 25
PAGE_SIZE = 100
MAX_AUDIENCE = 2**31 - 1
RATE_KEY_PREFIX = "broadcast:rate:"
MAX_ERROR_LENGTH = 512

AUDIENCE_SQL = text(
    "SELECT id FROM users WHERE broadcasts_opt_in IS TRUE "
    "AND id > :after_id ORDER BY id LIMIT :limit"
)
AUDIENCE_FALLBACK_SQL = text(
    "SELECT id FROM users WHERE id > :after_id ORDER BY id LIMIT :limit"
)
AUDIENCE_COUNT_SQL = text("SELECT count(*) FROM users WHERE broadcasts_opt_in IS TRUE")
AUDIENCE_COUNT_FALLBACK_SQL = text("SELECT count(*) FROM users")
CREATE_SQL = text(
    "INSERT INTO broadcasts (text, created_by, total) "
    "VALUES (:text, :created_by, :total) RETURNING id"
)
STATUS_SQL = text(
    "SELECT status, last_user_id, sent, failed, total, created_at, finished_at "
    "FROM broadcasts WHERE id = :broadcast_id"
)
LATEST_SQL = text(
    "SELECT id, status, last_user_id, sent, failed, total, created_at, finished_at "
    "FROM broadcasts ORDER BY created_at DESC LIMIT 1"
)
START_SQL = text(
    "UPDATE broadcasts SET status = 'running', finished_at = NULL WHERE id = :broadcast_id"
)
FINISH_SQL = text(
    "UPDATE broadcasts SET status = 'done', finished_at = now() WHERE id = :broadcast_id"
)
RECLAIM_SQL = text(
    """
    DELETE FROM broadcast_deliveries
    WHERE broadcast_id = :broadcast_id
      AND status = 'pending'
      AND sent_at < now() - make_interval(secs => :visibility)
    """
)
CLAIM_SQL = text(
    """
    INSERT INTO broadcast_deliveries (broadcast_id, chat_id, status)
    VALUES (:broadcast_id, :chat_id, 'pending')
    ON CONFLICT (broadcast_id, chat_id) DO NOTHING
    RETURNING chat_id
    """
)
DELIVERY_SQL = text(
    """
    UPDATE broadcast_deliveries
    SET status = :status, error = :error, sent_at = now()
    WHERE broadcast_id = :broadcast_id AND chat_id = :chat_id
    """
)
PROGRESS_SQL = text(
    """
    UPDATE broadcasts
    SET last_user_id = :last_user_id, sent = :sent, failed = :failed, status = 'running'
    WHERE id = :broadcast_id
    """
)
OPT_IN_SQL = text("UPDATE users SET broadcasts_opt_in = :opt_in WHERE id = :user_id")


async def _fetch_page(
    session_factory: async_sessionmaker[AsyncSession], after_id: int, limit: int
) -> list[int]:
    params = {"after_id": after_id, "limit": limit}
    try:
        return await _select_ids(session_factory, AUDIENCE_SQL, params)
    except (ProgrammingError, asyncpg.UndefinedColumnError) as error:
        if "broadcasts_opt_in" not in str(error):
            raise
        # ponytail: schema gap fallback; drop once the opt-in migration is universal.
        logger.warning("users.broadcasts_opt_in missing; treating every user as opted in")
        return await _select_ids(session_factory, AUDIENCE_FALLBACK_SQL, params)


async def _select_ids(
    session_factory: async_sessionmaker[AsyncSession], sql: Any, params: dict[str, Any]
) -> list[int]:
    async with session_factory() as session:
        async with session.begin():
            result = await session.execute(sql, params)
            return [int(row[0]) for row in result]


async def broadcast_audience(
    session_factory: async_sessionmaker[AsyncSession],
) -> list[int]:
    return await _fetch_page(session_factory, 0, MAX_AUDIENCE)


async def broadcast_page(
    session_factory: async_sessionmaker[AsyncSession],
    after_id: int,
    limit: int = PAGE_SIZE,
) -> list[int]:
    return await _fetch_page(session_factory, after_id, limit)


async def audience_size(session_factory: async_sessionmaker[AsyncSession]) -> int:
    """Opted-in users right now, with the same schema-gap fallback as pages."""
    try:
        return await _scalar(session_factory, AUDIENCE_COUNT_SQL)
    except (ProgrammingError, asyncpg.UndefinedColumnError) as error:
        if "broadcasts_opt_in" not in str(error):
            raise
        return await _scalar(session_factory, AUDIENCE_COUNT_FALLBACK_SQL)


async def _scalar(session_factory: async_sessionmaker[AsyncSession], sql: Any) -> int:
    async with session_factory() as session:
        async with session.begin():
            row = (await session.execute(sql)).first()
    return int(row[0]) if row is not None else 0


async def create_broadcast(
    session_factory: async_sessionmaker[AsyncSession], html: str, created_by: int
) -> str:
    total = await audience_size(session_factory)
    async with session_factory() as session:
        async with session.begin():
            row = (
                await session.execute(
                    CREATE_SQL, {"text": html, "created_by": created_by, "total": total}
                )
            ).first()
    if row is None:
        raise RuntimeError("broadcast insert failed")
    return str(row[0])


async def broadcast_status(
    session_factory: async_sessionmaker[AsyncSession], broadcast_id: str
) -> dict[str, Any] | None:
    async with session_factory() as session:
        async with session.begin():
            row = (
                await session.execute(STATUS_SQL, {"broadcast_id": broadcast_id})
            ).mappings().first()
    return dict(row) if row is not None else None


async def latest_broadcast(
    session_factory: async_sessionmaker[AsyncSession],
) -> dict[str, Any] | None:
    """The newest broadcast, for the status card opened without an id."""
    async with session_factory() as session:
        async with session.begin():
            row = (await session.execute(LATEST_SQL)).mappings().first()
    return dict(row) if row is not None else None


async def start_broadcast(
    session_factory: async_sessionmaker[AsyncSession], broadcast_id: str
) -> None:
    async with session_factory() as session:
        async with session.begin():
            await session.execute(START_SQL, {"broadcast_id": broadcast_id})


async def finish_broadcast(
    session_factory: async_sessionmaker[AsyncSession], broadcast_id: str
) -> None:
    async with session_factory() as session:
        async with session.begin():
            await session.execute(FINISH_SQL, {"broadcast_id": broadcast_id})


async def reclaim_pending(
    session_factory: async_sessionmaker[AsyncSession],
    broadcast_id: str,
    visibility_seconds: int,
) -> None:
    async with session_factory() as session:
        async with session.begin():
            await session.execute(
                RECLAIM_SQL,
                {"broadcast_id": broadcast_id, "visibility": visibility_seconds},
            )


async def claim_delivery(
    session_factory: async_sessionmaker[AsyncSession],
    broadcast_id: str,
    chat_id: int,
) -> bool:
    async with session_factory() as session:
        async with session.begin():
            row = (
                await session.execute(
                    CLAIM_SQL, {"broadcast_id": broadcast_id, "chat_id": chat_id}
                )
            ).first()
    return row is not None


async def record_page(
    session_factory: async_sessionmaker[AsyncSession],
    broadcast_id: str,
    results: list[tuple[int, bool, str | None]],
    last_user_id: int,
    sent: int,
    failed: int,
) -> None:
    # Delivery statuses, counters and the cursor commit together, so a restart
    # resumes at exactly this page boundary.
    async with session_factory() as session:
        async with session.begin():
            for chat_id, ok, error in results:
                await session.execute(
                    DELIVERY_SQL,
                    {
                        "broadcast_id": broadcast_id,
                        "chat_id": chat_id,
                        "status": "sent" if ok else "failed",
                        "error": error,
                    },
                )
            await session.execute(
                PROGRESS_SQL,
                {
                    "broadcast_id": broadcast_id,
                    "last_user_id": last_user_id,
                    "sent": sent,
                    "failed": failed,
                },
            )


async def set_opt_in(
    session_factory: async_sessionmaker[AsyncSession], user_id: int, opt_in: bool
) -> None:
    async with session_factory() as session:
        async with session.begin():
            await session.execute(OPT_IN_SQL, {"opt_in": opt_in, "user_id": user_id})


def clip_error(error: BaseException) -> str:
    return str(error)[:MAX_ERROR_LENGTH]


async def take_rate_slot(redis: Redis) -> None:
    """Hold a shared fixed window at ``PER_SECOND`` messages per second."""
    while True:
        key = RATE_KEY_PREFIX + str(int(time.time()))
        count = await redis.incr(key)
        if count == 1:
            await redis.expire(key, 2)
        if count <= PER_SECOND:
            return
        await asyncio.sleep(max(0.001, 1.0 - (time.time() % 1.0)))


async def send_with_retry(
    bot: Bot, chat_id: int, message_text: str
) -> tuple[bool, str | None]:
    """Send once, honouring ``retry_after``; never raise for one bad chat."""
    try:
        await bot.send_message(chat_id, message_text)
        return True, None
    except TelegramRetryAfter as error:
        await asyncio.sleep(error.retry_after)
        try:
            await bot.send_message(chat_id, message_text)
            return True, None
        except Exception as retry_error:  # noqa: BLE001
            return False, clip_error(retry_error)
    except Exception as error:  # noqa: BLE001
        # Blocked bots, deleted chats and missing users are recorded and skipped.
        return False, clip_error(error)