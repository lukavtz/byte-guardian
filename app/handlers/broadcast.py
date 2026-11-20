from __future__ import annotations

import re

from aiogram import Bot, Dispatcher, Router
from aiogram.filters import Command, CommandObject
from aiogram.types import Message
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.config import Settings
from app.jobs.broadcast import KIND as BROADCAST_KIND
from app.runtime.queue import Queue

ADMIN_ONLY_TEXT = "This command is restricted to administrators."
UUID_PATTERN = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$", re.I)

router = Router(name="broadcast")


def _is_admin(settings: Settings, user_id: int | None) -> bool:
    return user_id is not None and user_id in settings.admin_id_set


@router.message(Command("broadcast"))
async def broadcast_command(
    message: Message,
    command: CommandObject,
    session_factory: async_sessionmaker[AsyncSession],
    queue: Queue,
    settings: Settings,
) -> None:
    user = message.from_user
    if user is None or not _is_admin(settings, user.id):
        await message.answer(ADMIN_ONLY_TEXT)
        return
    broadcast_text = (command.args or "").strip()
    if not broadcast_text:
        await message.answer("Usage: /broadcast <text>")
        return
    async with session_factory() as session:
        async with session.begin():
            result = await session.execute(
                text(
                    "INSERT INTO broadcasts (text, created_by) "
                    "VALUES (:text, :created_by) RETURNING id"
                ),
                {"text": broadcast_text, "created_by": user.id},
            )
            row = result.first()
    if row is None:
        raise RuntimeError("broadcast insert failed")
    broadcast_id = str(row[0])
    await queue.enqueue(
        BROADCAST_KIND,
        {"broadcastId": broadcast_id, "text": broadcast_text, "createdBy": user.id},
        idempotency_key="broadcast:" + broadcast_id,
    )
    await message.answer("Broadcast " + broadcast_id + " queued.")


@router.message(Command("broadcast_status"))
async def broadcast_status(
    message: Message,
    command: CommandObject,
    session_factory: async_sessionmaker[AsyncSession],
    settings: Settings,
) -> None:
    user = message.from_user
    if user is None or not _is_admin(settings, user.id):
        await message.answer(ADMIN_ONLY_TEXT)
        return
    broadcast_id = (command.args or "").strip()
    if not UUID_PATTERN.match(broadcast_id):
        await message.answer("Usage: /broadcast_status <broadcast id>")
        return
    async with session_factory() as session:
        async with session.begin():
            row = (
                await session.execute(
                    text(
                        "SELECT status, sent, failed FROM broadcasts WHERE id = :id"
                    ),
                    {"id": broadcast_id},
                )
            ).mappings().first()
    if row is None:
        await message.answer("Broadcast not found.")
        return
    await message.answer(
        "status=" + str(row["status"]) + " sent=" + str(row["sent"]) + " failed=" + str(row["failed"])
    )


def register(dp: Dispatcher, _bot: Bot) -> None:
    dp.include_router(router)
