"""Any non-command text in a private chat is a search query."""

from __future__ import annotations

from aiogram import Bot, Dispatcher, F, Router
from aiogram.enums import ChatType
from aiogram.types import Message

from app.content.repository import ContentRepository
from app.services.callbacks import cb_cat
from app.services.i18n import resolve_locale
from app.ui.delivery import show
from app.ui.keyboards import button, markup, paired
from app.ui.render import RichScreen, search_screen

router = Router(name="search")


@router.message(F.chat.type == ChatType.PRIVATE, F.text, ~F.text.startswith("/"))
async def on_text(message: Message, repo: ContentRepository, bot: Bot) -> None:
    query = (message.text or "").strip()
    if not query or query.startswith("/"):
        return
    locale = resolve_locale(message.from_user.language_code if message.from_user else None)
    results = repo.search(query, locale)
    screen = search_screen(query, results, locale)
    if not results:
        # No match: the eight sections are the useful next tap.
        hints = [button(f"{category.emoji} {category.title}", cb_cat(category.slug)) for category in repo.categories()]
        screen = RichScreen(screen.html, markup(paired(hints)))
    await show(bot, message.chat.id, screen)


def register(dp: Dispatcher, _bot: Bot) -> None:
    dp.include_router(router)
