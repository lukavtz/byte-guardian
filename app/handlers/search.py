"""Any non-command text in a private chat is a search query.

The results screen pages seven at a time. Telegram caps callback data at 64
bytes, so the query itself is stored in `settings` under an 8-hex sha1 prefix
and only that prefix travels in «Показать ещё»'s callback.
"""

from __future__ import annotations

import hashlib
import re
import time

from aiogram import Bot, Dispatcher, F, Router
from aiogram.enums import ChatType
from aiogram.types import CallbackQuery, Message, User
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.content.repository import ContentRepository
from app.services.callbacks import parse_cb
from app.services.i18n import resolve_locale
from app.services.settings import settings_get, settings_set
from app.services.user_locale import get_locale
from app.ui.delivery import show
from app.ui.render import search_screen

router = Router(name="search")

SessionFactory = async_sessionmaker[AsyncSession]

# One hour, like a broadcast draft: a link tapped tomorrow is a stale screen.
QUERY_PREFIX = "q:"
QUERY_TTL_SECONDS = 3600
# The corpus is bounded (eight sections of seeded cards), so a wide first pass
# gives the honest «Найдено: N» without a second ranking call.
SEARCH_LIMIT = 100
DIGEST = re.compile(r"^[0-9a-f]{8}$")


def digest_of(query: str) -> str:
    """Stable 8-hex key for one query; case and spacing do not matter."""
    return hashlib.sha1(" ".join(query.split()).casefold().encode("utf-8")).hexdigest()[:8]


async def remember_query(
    session_factory: SessionFactory, digest: str, query: str
) -> None:
    await settings_set(
        session_factory,
        QUERY_PREFIX + digest,
        {"query": query, "created_at": time.time()},
    )


async def recall_query(
    session_factory: SessionFactory, digest: str
) -> str | None:
    """The stored query, or ``None`` once its hour is up or the row is junk."""
    value = await settings_get(session_factory, QUERY_PREFIX + digest)
    if not isinstance(value, dict):
        return None
    created = value.get("created_at")
    query = value.get("query")
    if not isinstance(created, (int, float)) or isinstance(created, bool):
        return None
    if time.time() - float(created) >= QUERY_TTL_SECONDS:
        return None
    return query if isinstance(query, str) and query.strip() else None


async def _locale(session_factory: SessionFactory | None, user: User | None) -> str:
    code = user.language_code if user is not None else None
    tg_id = user.id if user is not None else 0
    if session_factory is None:
        return resolve_locale(code)
    return await get_locale(session_factory, tg_id, code)


@router.message(F.chat.type == ChatType.PRIVATE, F.text, ~F.text.startswith("/"))
async def on_text(
    message: Message,
    repo: ContentRepository,
    bot: Bot,
    session_factory: SessionFactory | None = None,
) -> None:
    query = (message.text or "").strip()
    if not query or query.startswith("/"):
        return
    locale = await _locale(session_factory, message.from_user)
    results = repo.find(query, locale, limit=SEARCH_LIMIT).items
    digest = digest_of(query)
    if session_factory is not None:
        await remember_query(session_factory, digest, query)
    screen = search_screen(
        query,
        results,
        locale,
        None if results else repo.categories(),
        total=len(results),
        digest=digest,
    )
    await show(bot, message.chat.id, screen)


@router.callback_query(F.data.startswith("b1:search:"))
async def on_page(
    query: CallbackQuery,
    bot: Bot,
    repo: ContentRepository,
    session_factory: SessionFactory | None = None,
) -> None:
    parsed = parse_cb(query.data)
    message = query.message
    if (
        parsed is None
        or parsed.action != "search"
        or not isinstance(message, Message)
        or message.chat.type != ChatType.PRIVATE
    ):
        await query.answer()
        return
    locale = await _locale(session_factory, query.from_user)
    text = (
        await recall_query(session_factory, parsed.arg)
        if session_factory is not None
        else None
    )
    await query.answer()
    if text is None:
        # The hour ran out: ask again instead of paging into nothing.
        screen = search_screen("", [], locale, repo.categories())
    else:
        results = repo.find(text, locale, limit=SEARCH_LIMIT).items
        screen = search_screen(
            text,
            results,
            locale,
            page=parsed.page,
            total=len(results),
            digest=parsed.arg,
        )
    await show(bot, message.chat.id, screen, message.message_id)


def register(dp: Dispatcher, _bot: Bot) -> None:
    dp.include_router(router)