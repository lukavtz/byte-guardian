from __future__ import annotations

from typing import Any, Protocol

from aiogram import Bot
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.config import Settings
from app.runtime.queue import ClaimedJob, Queue
from app.services import broadcast as service

KIND = "broadcast.send"


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


async def handle(job: ClaimedJob, ctx: JobContext) -> None:
    payload = _parse_payload(job.payload)
    if ctx.redis is None:
        raise RuntimeError("broadcast requires REDIS_URL")

    broadcast_id = payload["broadcastId"]
    await service.reclaim_pending(
        ctx.session_factory,
        broadcast_id,
        ctx.settings.queue_visibility_timeout_seconds,
    )
    row = await service.broadcast_status(ctx.session_factory, broadcast_id)
    if row is None or row["status"] == "done":
        return

    last_user_id = int(row["last_user_id"] or 0)
    sent = int(row["sent"])
    failed = int(row["failed"])
    await service.start_broadcast(ctx.session_factory, broadcast_id)

    while True:
        page = await service.broadcast_page(ctx.session_factory, last_user_id)
        if not page:
            break

        results: list[tuple[int, bool, str | None]] = []
        for chat_id in page:
            if not await service.claim_delivery(ctx.session_factory, broadcast_id, chat_id):
                continue
            await service.take_rate_slot(ctx.redis)
            ok, error = await service.send_with_retry(ctx.bot, chat_id, payload["text"])
            if ok:
                sent += 1
            else:
                failed += 1
            results.append((chat_id, ok, error))

        last_user_id = page[-1]
        await service.record_page(
            ctx.session_factory, broadcast_id, results, last_user_id, sent, failed
        )

    await service.finish_broadcast(ctx.session_factory, broadcast_id)