from __future__ import annotations

import asyncio
import logging
import re
from collections.abc import Mapping
from datetime import UTC, datetime
from html import escape
from typing import Any

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
    latest_broadcast,
    set_opt_in,
)
from app.services.callbacks import (
    cb_admin,
    cb_broadcast,
    cb_broadcast_status,
    parse_cb,
)
from app.services.i18n import resolve_locale, t
from app.services.settings import draft_discard, draft_save, draft_take
from app.ui.delivery import show
from app.ui.emoji import premium
from app.ui.keyboards import rich_button, rich_row
from app.ui.render import RichScreen

UUID_PATTERN = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$", re.I)
MAX_BROADCAST_CHARS = 4000
PROGRESS_INTERVAL = 3.0
BAR_CELLS = 10
LIVE_STATUSES = frozenset({"pending", "running"})

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


def progress_percent(done: int, total: int) -> int:
    return max(0, min(100, done * 100 // total)) if total > 0 else 0


def progress_bar(percent: int, cells: int = BAR_CELLS) -> str:
    """Ten cells, rounded: 0% is empty, 100% is full."""
    filled = max(0, min(cells, round(percent * cells / 100)))
    return "▇" * filled + "░" * (cells - filled)


def format_duration(seconds: float) -> str:
    whole = max(0, int(seconds))
    hours, rest = divmod(whole, 3600)
    minutes, secs = divmod(rest, 60)
    if hours:
        return f"{hours} ч {minutes:02d} м"
    if minutes:
        return f"{minutes} м {secs:02d} с"
    return f"{secs} с"


def elapsed_seconds(row: Mapping[str, Any]) -> float:
    start = row.get("created_at")
    if not isinstance(start, datetime):
        return 0.0
    end = row.get("finished_at")
    if not isinstance(end, datetime):
        end = datetime.now(UTC)
    return max(0.0, (end - start).total_seconds())


def _status(locale: str, name: str) -> str:
    label = t(locale, "broadcasts.state." + name)
    return name if label.startswith("broadcasts.") else label


def progress_screen(
    broadcast_id: str, row: Mapping[str, Any], locale: str = "ru"
) -> RichScreen:
    """One broadcast: counters, a ten-cell bar, elapsed time, two ways out."""
    sent = int(row.get("sent") or 0)
    failed = int(row.get("failed") or 0)
    done = sent + failed
    total = max(int(row.get("total") or 0), done)
    percent = progress_percent(done, total)
    html = "\n".join(
        (
            f"<h1>{premium('broadcast', '📣')} Ход рассылки</h1>",
            f"<p>Статус: <b>{escape(_status(locale, str(row.get('status') or '')), quote=False)}</b></p>",
            f"<p>Всего: <b>{total}</b> · {premium('send', '📤')} Отправлено: <b>{sent}</b>"
            f" · {premium('error', '❗')} Ошибок: <b>{failed}</b>"
            f" · {premium('hourglass', '⏳')} Осталось: <b>{max(0, total - done)}</b></p>",
            f"<pre>{progress_bar(percent)} {percent}%</pre>",
            f"<p>{premium('health', '💓')} Прошло: <b>{format_duration(elapsed_seconds(row))}</b></p>",
            "<hr/>",
            rich_row(
                rich_button(
                    "Обновить",
                    data=cb_broadcast_status(broadcast_id),
                    style="primary",
                    icon=("refresh", "🔄"),
                ),
                rich_button("Назад", data=cb_admin(), icon=("back", "🏠")),
            ),
        )
    )
    return RichScreen(html)


# One live card per chat message. A second open or a finished job replaces or
# drops its predecessor, so a closed broadcast never keeps editing.
_monitors: dict[tuple[int, int], asyncio.Task[None]] = {}


def _live(row: Mapping[str, Any]) -> bool:
    return str(row.get("status") or "") in LIVE_STATUSES


def _stop_monitor(key: tuple[int, int]) -> None:
    task = _monitors.pop(key, None)
    if task is not None:
        task.cancel()


def _start_monitor(
    bot: Bot,
    chat_id: int,
    message_id: int,
    broadcast_id: str,
    session_factory: async_sessionmaker[AsyncSession],
    locale: str,
) -> None:
    key = (chat_id, message_id)
    _stop_monitor(key)
    task = asyncio.create_task(
        _monitor(bot, chat_id, message_id, broadcast_id, session_factory, locale),
        name="broadcast-progress:" + broadcast_id,
    )
    _monitors[key] = task

    def _forget(finished: asyncio.Task[None]) -> None:
        if _monitors.get(key) is finished:
            _monitors.pop(key, None)

    task.add_done_callback(_forget)


async def _monitor(
    bot: Bot,
    chat_id: int,
    message_id: int,
    broadcast_id: str,
    session_factory: async_sessionmaker[AsyncSession],
    locale: str,
) -> None:
    """Redraw the card every few seconds until the broadcast stops running."""
    while True:
        await asyncio.sleep(PROGRESS_INTERVAL)
        row = await broadcast_status(session_factory, broadcast_id)
        if row is None:
            return
        try:
            await show(bot, chat_id, progress_screen(broadcast_id, row, locale), message_id)
        except Exception:  # noqa: BLE001
            logger.exception("broadcast progress redraw failed for %s", broadcast_id)
            return
        if not _live(row):
            return


@router.callback_query(F.data.startswith(cb_broadcast_status()))
async def broadcast_progress(
    query: CallbackQuery,
    bot: Bot,
    session_factory: async_sessionmaker[AsyncSession],
    settings: Settings,
) -> None:
    """The card and its «Обновить»: same message, admin re-checked every time."""
    user = query.from_user
    locale = resolve_locale(user.language_code if user else None)
    if user is None or not is_admin(user.id, settings.admin_id_set):
        await query.answer(t(locale, "broadcasts.admins_only"), show_alert=True)
        return

    callback = parse_cb(query.data)
    if callback is None or callback.action != "bc_status":
        await query.answer()
        return

    row = (
        await broadcast_status(session_factory, callback.arg)
        if callback.arg
        else await latest_broadcast(session_factory)
    )
    if row is None:
        await query.answer(t(locale, "broadcasts.not_found"), show_alert=True)
        return

    broadcast_id = callback.arg or str(row.get("id") or "")
    if not broadcast_id:
        await query.answer(t(locale, "broadcasts.not_found"), show_alert=True)
        return

    message = query.message
    chat_id = getattr(getattr(message, "chat", None), "id", None)
    message_id = getattr(message, "message_id", None)
    if chat_id is None or message_id is None:
        await query.answer()
        return

    await query.answer()
    await show(bot, chat_id, progress_screen(broadcast_id, row, locale), message_id)
    if _live(row):
        _start_monitor(bot, chat_id, message_id, broadcast_id, session_factory, locale)
    else:
        _stop_monitor((chat_id, message_id))


@router.message(Command("broadcast_status"))
async def broadcast_status_command(
    message: Message,
    command: CommandObject,
    bot: Bot,
    session_factory: async_sessionmaker[AsyncSession],
    settings: Settings,
) -> None:
    user = message.from_user
    locale = resolve_locale(user.language_code if user else None)
    if user is None or not is_admin(user.id, settings.admin_id_set):
        await message.answer(t(locale, "admin.denied"))
        return
    broadcast_id = (command.args or "").strip()
    if broadcast_id and not UUID_PATTERN.match(broadcast_id):
        await message.answer(t(locale, "broadcasts.status_usage"))
        return
    row = (
        await broadcast_status(session_factory, broadcast_id)
        if broadcast_id
        else await latest_broadcast(session_factory)
    )
    if row is None:
        await message.answer(t(locale, "broadcasts.not_found"))
        return
    broadcast_id = broadcast_id or str(row.get("id") or "")
    card = await show(bot, message.chat.id, progress_screen(broadcast_id, row, locale))
    if card is not None and broadcast_id and _live(row):
        _start_monitor(
            bot, message.chat.id, card.message_id, broadcast_id, session_factory, locale
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