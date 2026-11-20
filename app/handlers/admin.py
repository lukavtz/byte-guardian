from __future__ import annotations

import re
from typing import Any

from aiogram import Bot, Dispatcher, F, Router
from aiogram.filters import Command
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
)
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.config import Settings
from app.middlewares.allowlist import is_admin
from app.services.callbacks import cb_admin

router = Router(name="admin")

ADMIN_ONLY_TEXT = "This command is restricted to administrators."
TOGGLE_PREFIX = "adm:t:"
SLUG_PATTERN = re.compile(r"^[a-z0-9][a-z0-9-]{0,31}$")

STATS_SQL = text(
    """
    SELECT
      (SELECT count(*) FROM users) AS users,
      (SELECT count(*) FROM threat) AS threats,
      (SELECT count(*) FROM quiz_question) AS quizzes,
      (SELECT count(*) FROM quiz_answer) AS answers
    """
)
CATEGORIES_SQL = text("SELECT slug, enabled FROM category ORDER BY sort, slug")
TOGGLE_SQL = text(
    "UPDATE category SET enabled = NOT enabled WHERE slug = :slug RETURNING enabled"
)


async def admin_stats(session_factory: async_sessionmaker[AsyncSession]) -> dict[str, int]:
    async with session_factory() as session:
        async with session.begin():
            row = (await session.execute(STATS_SQL)).mappings().first()
    if row is None:
        return {"users": 0, "threats": 0, "quizzes": 0, "answers": 0}
    return {key: int(row[key]) for key in ("users", "threats", "quizzes", "answers")}


async def category_rows(
    session_factory: async_sessionmaker[AsyncSession],
) -> list[tuple[str, bool]]:
    async with session_factory() as session:
        async with session.begin():
            result = await session.execute(CATEGORIES_SQL)
            return [(str(row[0]), bool(row[1])) for row in result]


async def toggle_category(
    session_factory: async_sessionmaker[AsyncSession], slug: str
) -> bool | None:
    """Flip only ``enabled`` for one category; ``None`` when the slug is unknown."""
    async with session_factory() as session:
        async with session.begin():
            row = (
                await session.execute(TOGGLE_SQL, {"slug": slug})
            ).first()
    return bool(row[0]) if row is not None else None


def _panel_text(stats: dict[str, int], categories: list[tuple[str, bool]]) -> str:
    lines = [
        "<b>Admin panel</b>",
        f"Users: {stats['users']}",
        f"Threats: {stats['threats']}",
        f"Quizzes: {stats['quizzes']}",
        f"Answers: {stats['answers']}",
        "",
        "<b>Categories</b>",
    ]
    if categories:
        lines.extend(("✅ " if enabled else "⬜ ") + slug for slug, enabled in categories)
    else:
        lines.append("No categories yet.")
    lines.extend(["", "Broadcast: /broadcast &lt;html&gt;"])
    return "\n".join(lines)


def _panel_keyboard(
    categories: list[tuple[str, bool]],
) -> InlineKeyboardMarkup | None:
    buttons = [
        [
            InlineKeyboardButton(
                text=("✅ " if enabled else "⬜ ") + slug,
                callback_data=TOGGLE_PREFIX + slug,
            )
        ]
        for slug, enabled in categories
        if SLUG_PATTERN.fullmatch(slug)
    ]
    return InlineKeyboardMarkup(inline_keyboard=buttons) if buttons else None


async def _panel(
    session_factory: async_sessionmaker[AsyncSession],
) -> tuple[str, InlineKeyboardMarkup | None]:
    stats = await admin_stats(session_factory)
    categories = await category_rows(session_factory)
    return _panel_text(stats, categories), _panel_keyboard(categories)


@router.message(Command("admin"))
async def admin_panel(
    message: Message,
    session_factory: async_sessionmaker[AsyncSession],
    settings: Settings,
) -> None:
    user_id = message.from_user.id if message.from_user else None
    if not is_admin(user_id, settings.admin_id_set):
        await message.answer(ADMIN_ONLY_TEXT)
        return
    text_value, keyboard = await _panel(session_factory)
    await message.answer(text_value, reply_markup=keyboard)


@router.callback_query(F.data == cb_admin())
async def admin_refresh(
    query: CallbackQuery,
    session_factory: async_sessionmaker[AsyncSession],
    settings: Settings,
) -> None:
    user_id = query.from_user.id if query.from_user else None
    if not is_admin(user_id, settings.admin_id_set):
        await query.answer("Admins only.", show_alert=True)
        return
    await query.answer()
    await _refresh_panel(query, session_factory)


@router.callback_query(F.data.startswith(TOGGLE_PREFIX))
async def admin_toggle(
    query: CallbackQuery,
    session_factory: async_sessionmaker[AsyncSession],
    settings: Settings,
) -> None:
    user_id = query.from_user.id if query.from_user else None
    if not is_admin(user_id, settings.admin_id_set):
        await query.answer("Admins only.", show_alert=True)
        return
    slug = (query.data or "")[len(TOGGLE_PREFIX) :]
    if not SLUG_PATTERN.fullmatch(slug):
        await query.answer("Unknown category.", show_alert=True)
        return
    enabled = await toggle_category(session_factory, slug)
    if enabled is None:
        await query.answer("Unknown category.", show_alert=True)
        return
    await query.answer("Enabled" if enabled else "Disabled")
    await _refresh_panel(query, session_factory)


async def _refresh_panel(
    query: CallbackQuery,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    panel_text, keyboard = await _panel(session_factory)
    message: Any = query.message
    edit = getattr(message, "edit_text", None)
    if edit is None:
        return
    await edit(panel_text, reply_markup=keyboard)


def register(dp: Dispatcher, _bot: Bot) -> None:
    dp.include_router(router)