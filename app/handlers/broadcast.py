from __future__ import annotations

import logging
import re

import asyncpg
from aiogram import Bot, Dispatcher, F, Router
from aiogram.filters import Command, CommandObject
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
)
from sqlalchemy.exc import ProgrammingError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.config import Settings
from app.jobs.broadcast import KIND as BROADCAST_KIND
from app.middlewares.allowlist import is_admin
from app.runtime.queue import Queue
from app.services.broadcast import (
    broadcast_status,
    create_broadcast,
    set_opt_in,
)
from app.services.callbacks import cb_broadcast
from app.services.i18n import resolve_locale, t
from app.services.settings import draft_discard, draft_save, draft_take

UUID_PATTERN = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$", re.I)
MAX_BROADCAST_CHARS = 4000

logger = logging.getLogger(__name__)

router = Router(name="broadcast")


def _confirm_keyboard(locale: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=t(locale, "broadcasts.send"), callback_data=cb_broadcast(True)
                ),
                InlineKeyboardButton(
                    text=t(locale, "broadcasts.cancel"), callback_data=cb_broadcast(False)
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
    locale = resolve_locale(user.language_code if user else None)
    if user is None or not is_admin(user.id, settings.admin_id_set):
        await message.answer(t(locale, "admin.denied"))
        return
    broadcast_text = (command.args or "").strip()
    if not broadcast_text:
        await message.answer(t(locale, "broadcasts.usage"))
        return
    if len(broadcast_text.encode("utf-16-le")) // 2 > MAX_BROADCAST_CHARS:
        await message.answer(t(locale, "broadcasts.too_long", limit=MAX_BROADCAST_CHARS))
        return
    await draft_save(session_factory, user.id, broadcast_text)
    await message.answer(
        t(locale, "broadcasts.preview_title") + "\n\n" + broadcast_text,
        reply_markup=_confirm_keyboard(locale),
    )


@router.callback_query(F.data.in_({cb_broadcast(True), cb_broadcast(False)}))
async def broadcast_confirm(
    query: CallbackQuery,
    session_factory: async_sessionmaker[AsyncSession],
    queue: Queue,
    settings: Settings,
) -> None:
    user = query.from_user
    locale = resolve_locale(user.language_code if user else None)
    if user is None or not is_admin(user.id, settings.admin_id_set):
        await query.answer(t(locale, "broadcasts.admins_only"), show_alert=True)
        return

    if query.data == cb_broadcast(False):
        await draft_discard(session_factory, user.id)
        await query.answer(t(locale, "broadcasts.cancelled"))
        await _edit(query, t(locale, "broadcasts.cancelled"))
        return

    broadcast_text = await draft_take(session_factory, user.id)
    if broadcast_text is None:
        await query.answer(t(locale, "broadcasts.draft_expired"), show_alert=True)
        await _edit(query, t(locale, "broadcasts.draft_expired"))
        return

    broadcast_id = await create_broadcast(session_factory, broadcast_text, user.id)
    await queue.enqueue(
        BROADCAST_KIND,
        {"broadcastId": broadcast_id, "text": broadcast_text, "createdBy": user.id},
        idempotency_key="broadcast:" + broadcast_id,
    )
    await draft_discard(session_factory, user.id)
    await query.answer(t(locale, "broadcasts.queued"))
    await _edit(query, t(locale, "broadcasts.queued_id", id=broadcast_id))


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
    locale = resolve_locale(user.language_code if user else None)
    if user is None or not is_admin(user.id, settings.admin_id_set):
        await message.answer(t(locale, "admin.denied"))
        return
    broadcast_id = (command.args or "").strip()
    if not UUID_PATTERN.match(broadcast_id):
        await message.answer(t(locale, "broadcasts.status_usage"))
        return
    row = await broadcast_status(session_factory, broadcast_id)
    if row is None:
        await message.answer(t(locale, "broadcasts.not_found"))
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
    locale = resolve_locale(user.language_code)
    try:
        await set_opt_in(session_factory, user.id, False)
    except (ProgrammingError, asyncpg.UndefinedColumnError) as error:
        if "broadcasts_opt_in" not in str(error):
            raise
        logger.exception("broadcasts_opt_in is missing; /stop cannot persist")
        await message.answer(t(locale, "broadcasts.stop_failed"))
        return
    await message.answer(t(locale, "broadcasts.stopped"))


def register(dp: Dispatcher, _bot: Bot) -> None:
    dp.include_router(router)