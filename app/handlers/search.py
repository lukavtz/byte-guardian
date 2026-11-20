"""Any non-command text in a private chat is a search query."""

from __future__ import annotations

from aiogram import Bot, Dispatcher, F, Router
from aiogram.enums import ChatType
from aiogram.types import Message, User
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.content.repository import ContentRepository
from app.services.i18n import resolve_locale
from app.services.user_locale import get_locale
from app.ui.delivery import show
from app.ui.render import search_screen

router = Router(name="search")


SessionFactory = async_sessionmaker[AsyncSession]


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
    results = repo.search(query, locale)
    screen = search_screen(query, results, locale, None if results else repo.categories())
    await show(bot, message.chat.id, screen)


def register(dp: Dispatcher, _bot: Bot) -> None:
    dp.include_router(router)
