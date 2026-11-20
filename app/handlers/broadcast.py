from __future__ import annotations

import re

from aiogram import Bot, Dispatcher, F, Router
from aiogram.filters import Command, CommandObject
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
)
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.config import Settings
from app.handlers.admin import ADMIN_ONLY_TEXT
from app.jobs.broadcast import KIND as BROADCAST_KIND
from app.middlewares.allowlist import is_admin
from app.runtime.queue import Queue
from app.services.broadcast import (
    broadcast_status,
    create_broadcast,
    set_opt_in,
)
from app.services.callbacks import cb_broadcast
from app.services.settings import draft_discard, draft_save, draft_take

UUID_PATTERN = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$", re.I)
MAX_BROADCAST_CHARS = 4000
PREVIEW_TITLE = "Broadcast preview. Send it?"
STOP_TEXT = "Broadcasts are off. You will not receive mass messages."

router = Router(name="broadcast")


def _confirm_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="Send", callback_data=cb_broadcast(True)
                ),
                InlineKeyboardButton(
                    text="Cancel", callback_data=cb_broadcast(False)
                ),
            ]
        ]
    )


@router.message(Command("broadcast"))
async def broadcast_command(
    message: Message,
    command: CommandObject,
    session_factory: async_sessionmaker[AsyncSession],
    settings: Settings,
) -> None:
    user = message.from_user
    if user is None or not is_admin(user.id, settings.admin_id_set):
        await message.answer(ADMIN_ONLY_TEXT)
        return
    broadcast_text = (command.args or "").strip()
    if not broadcast_text:
        await message.answer("Usage: /broadcast <html text>")
        return
    if len(broadcast_text.encode("utf-16-le")) // 2 > MAX_BROADCAST_CHARS:
        await message.answer(f"Broadcast is limited to {MAX_BROADCAST_CHARS} characters.")
        return
    await draft_save(session_factory, user.id, broadcast_text)
    await message.answer(
        PREVIEW_TITLE + "\n\n" + broadcast_text,
        reply_markup=_confirm_keyboard(),
    )


@router.callback_query(F.data.in_({cb_broadcast(True), cb_broadcast(False)}))
async def broadcast_confirm(
    query: CallbackQuery,
    session_factory: async_sessionmaker[AsyncSession],
    queue: Queue,
    settings: Settings,
) -> None:
    user = query.from_user
    if user is None or not is_admin(user.id, settings.admin_id_set):
        await query.answer("Admins only.", show_alert=True)
        return

    if query.data == cb_broadcast(False):
        await draft_discard(session_factory, user.id)
        await query.answer("Cancelled.")
        await _edit(query, "Broadcast cancelled.")
        return

    broadcast_text = await draft_take(session_factory, user.id)
    if broadcast_text is None:
        await query.answer("Draft expired or missing.", show_alert=True)
        await _edit(query, "Draft expired. Send /broadcast again.")
        return

    broadcast_id = await create_broadcast(session_factory, broadcast_text, user.id)
    await queue.enqueue(
        BROADCAST_KIND,
        {"broadcastId": broadcast_id, "text": broadcast_text, "createdBy": user.id},
        idempotency_key="broadcast:" + broadcast_id,
    )
    await draft_discard(session_factory, user.id)
    await query.answer("Queued.")
    await _edit(query, "Broadcast " + broadcast_id + " queued.")


async def _edit(query: CallbackQuery, text_value: str) -> None:
    edit = getattr(query.message, "edit_text", None)
    if edit is not None:
        await edit(text_value, reply_markup=None)


@router.message(Command("broadcast_status"))
async def broadcast_status_command(
    message: Message,
    command: CommandObject,
    session_factory: async_sessionmaker[AsyncSession],
    settings: Settings,
) -> None:
    user = message.from_user
    if user is None or not is_admin(user.id, settings.admin_id_set):
        await message.answer(ADMIN_ONLY_TEXT)
        return
    broadcast_id = (command.args or "").strip()
    if not UUID_PATTERN.match(broadcast_id):
        await message.answer("Usage: /broadcast_status <broadcast id>")
        return
    row = await broadcast_status(session_factory, broadcast_id)
    if row is None:
        await message.answer("Broadcast not found.")
        return
    await message.answer(
        "status=" + str(row["status"]) + " sent=" + str(row["sent"]) + " failed=" + str(row["failed"])
    )


@router.message(Command("stop"))
async def stop_broadcasts(
    message: Message,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    user = message.from_user
    if user is None:
        return
    await set_opt_in(session_factory, user.id, False)
    await message.answer(STOP_TEXT)


def register(dp: Dispatcher, _bot: Bot) -> None:
    dp.include_router(router)