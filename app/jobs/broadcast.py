from __future__ import annotations

import asyncio
import time
from typing import Any, Protocol

from aiogram import Bot
from aiogram.exceptions import TelegramRetryAfter
from redis.asyncio import Redis
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.config import Settings
from app.runtime.queue import ClaimedJob, Queue

KIND = "broadcast.send"
GLOBAL_PER_SECOND = 30
RATE_KEY_PREFIX = "broadcast:rate:"
MAX_ERROR_LENGTH = 512

RECLAIM_SQL = text(
    """
    DELETE FROM broadcast_deliveries
    WHERE broadcast_id = :broadcast_id
      AND status = 'pending'
      AND sent_at < now() - make_interval(secs => :visibility)
    """
)
SELECT_BROADCAST_SQL = text(
    "SELECT last_user_id, sent, failed, status FROM broadcasts WHERE id = :broadcast_id"
)
START_SQL = text(
    "UPDATE broadcasts SET status = 'running', finished_at = NULL WHERE id = :broadcast_id"
)
PAGE_USERS_SQL = text(
    "SELECT id FROM users WHERE id > :last_user_id::bigint ORDER BY id LIMIT 100"
)
CLAIM_DELIVERY_SQL = text(
    """
    INSERT INTO broadcast_deliveries (broadcast_id, chat_id, status)
    VALUES (:broadcast_id, :chat_id, 'pending')
    ON CONFLICT (broadcast_id, chat_id) DO NOTHING
    RETURNING chat_id
    """
)
UPDATE_DELIVERY_SQL = text(
    """
    UPDATE broadcast_deliveries
    SET status = :status, error = :error, sent_at = now()
    WHERE broadcast_id = :broadcast_id AND chat_id = :chat_id
    """
)
UPDATE_PROGRESS_SQL = text(
    """
    UPDATE broadcasts
    SET last_user_id = :last_user_id::bigint, sent = :sent, failed = :failed,
        status = 'running'
    WHERE id = :broadcast_id
    """
)
FINISH_SQL = text(
    "UPDATE broadcasts SET status = 'done', finished_at = now() WHERE id = :broadcast_id"
)


class JobContext(Protocol):
    bot: Bot
    session_factory: async_sessionmaker[AsyncSession]
    queue: Queue
    settings: Settings
    redis: Redis | None


def _parse_payload(payload: Any) -> dict[str, Any]:
    if not isinstance(payload, dict):
        raise ValueError("invalid broadcast payload")
    broadcast_id = payload.get("broadcastId")
    message_text = payload.get("text")
    created_by = payload.get("createdBy")
    if not isinstance(broadcast_id, str) or not broadcast_id:
        raise ValueError("invalid broadcast payload")
    if not isinstance(message_text, str) or not message_text:
        raise ValueError("invalid broadcast payload")
    if not isinstance(created_by, int) or isinstance(created_by, bool):
        raise ValueError("invalid broadcast payload")
    return {"broadcastId": broadcast_id, "text": message_text, "createdBy": created_by}


def _clip_error(error: BaseException) -> str:
    return str(error)[:MAX_ERROR_LENGTH]


# Global fixed window shared by every worker:
# INCR broadcast:rate:<second> and EXPIRE 2. Over-limit workers wait for the
# next second instead of bursting past Telegram's 30 messages/second cap.
async def take_rate_slot(redis: Redis) -> None:
    while True:
        key = RATE_KEY_PREFIX + str(int(time.time()))
        count = await redis.incr(key)
        if count == 1:
            await redis.expire(key, 2)
        if count <= GLOBAL_PER_SECOND:
            return
        await asyncio.sleep(max(0.001, 1.0 - (time.time() % 1.0)))


async def send_with_retry(bot: Bot, chat_id: int, message_text: str) -> tuple[bool, str | None]:
    try:
        await bot.send_message(chat_id, message_text)
        return True, None
    except TelegramRetryAfter as error:
        await asyncio.sleep(error.retry_after)
        try:
            await bot.send_message(chat_id, message_text)
            return True, None
        except Exception as retry_error:  # noqa: BLE001
            return False, _clip_error(retry_error)
    except Exception as error:  # noqa: BLE001
        return False, _clip_error(error)


async def handle(job: ClaimedJob, ctx: JobContext) -> None:
    payload = _parse_payload(job.payload)
    redis = ctx.redis
    if redis is None:
        raise RuntimeError("broadcast requires REDIS_URL")

    broadcast_id = payload["broadcastId"]
    async with ctx.session_factory() as session:
        async with session.begin():
            await session.execute(
                RECLAIM_SQL,
                {
                    "broadcast_id": broadcast_id,
                    "visibility": ctx.settings.queue_visibility_timeout_seconds,
                },
            )
            row = (
                await session.execute(SELECT_BROADCAST_SQL, {"broadcast_id": broadcast_id})
            ).mappings().first()
    if row is None or row["status"] == "done":
        return

    last_user_id = int(row["last_user_id"])
    sent = int(row["sent"])
    failed = int(row["failed"])

    async with ctx.session_factory() as session:
        async with session.begin():
            await session.execute(START_SQL, {"broadcast_id": broadcast_id})

    while True:
        async with ctx.session_factory() as session:
            page = [
                int(page_row[0])
                for page_row in await session.execute(
                    PAGE_USERS_SQL, {"last_user_id": last_user_id}
                )
            ]
        if not page:
            break

        results: list[tuple[int, bool, str | None]] = []
        for chat_id in page:
            async with ctx.session_factory() as session:
                async with session.begin():
                    # The delivery primary key makes the claim exclusive across
                    # workers: only the worker that inserts the row sends.
                    claimed = (
                        await session.execute(
                            CLAIM_DELIVERY_SQL,
                            {"broadcast_id": broadcast_id, "chat_id": chat_id},
                        )
                    ).first()
            if claimed is None:
                continue

            await take_rate_slot(redis)
            ok, error = await send_with_retry(ctx.bot, chat_id, payload["text"])
            if ok:
                sent += 1
            else:
                failed += 1
            results.append((chat_id, ok, error))

        last_user_id = page[-1]
        # Delivery statuses, counters, and the cursor commit together so a
        # restart resumes at exactly this page boundary.
        async with ctx.session_factory() as session:
            async with session.begin():
                for chat_id, ok, error in results:
                    await session.execute(
                        UPDATE_DELIVERY_SQL,
                        {
                            "broadcast_id": broadcast_id,
                            "chat_id": chat_id,
                            "status": "sent" if ok else "failed",
                            "error": error,
                        },
                    )
                await session.execute(
                    UPDATE_PROGRESS_SQL,
                    {
                        "broadcast_id": broadcast_id,
                        "last_user_id": last_user_id,
                        "sent": sent,
                        "failed": failed,
                    },
                )

    async with ctx.session_factory() as session:
        async with session.begin():
            await session.execute(FINISH_SQL, {"broadcast_id": broadcast_id})
