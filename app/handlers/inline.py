"""Inline lookup: every query is answered, even when nothing matches."""

from __future__ import annotations

from html import escape

from aiogram import Bot, Dispatcher, Router
from aiogram.types import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    InlineQuery,
    InlineQueryResultArticle,
    InputTextMessageContent,
)

from app.content.repository import ContentRepository
from app.content.types import ThreatView
from app.services.i18n import resolve_locale, t

router = Router(name="inline")

TITLE = 120
SUMMARY = 300
RESULTS = 10
CACHE_TIME = 30
HTML_LIMIT = 4096
FALLBACK_USERNAME = "bot"


def _esc(value: object, quote: bool = False) -> str:
    return escape(str(value), quote=quote)


def _clip(value: object, limit: int) -> str:
    text = " ".join(str(value).split())
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


def _deep_link(username: str, slug: str) -> str:
    return f"https://t.me/{username}?start=card_{slug}"


def _button(text: str, link: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text=_clip(text, 64), url=link)]])


def _card_article(view: ThreatView, locale: str, username: str) -> InlineQueryResultArticle:
    link = _deep_link(username, view.slug)
    html = (
        f"<b>{_esc(_clip(view.title, TITLE))}</b>\n"
        f"{_esc(_clip(view.summary, SUMMARY))}\n"
        f"<b>112</b>\n"
        f'<a href="{_esc(link, quote=True)}">{_esc(t(locale, "inline.open"))}</a>'
    )
    # Every part is clipped, so the payload stays far below HTML_LIMIT.
    assert len(html) <= HTML_LIMIT
    return InlineQueryResultArticle(
        id=view.slug,
        title=_esc(_clip(view.title, TITLE)),
        description=_esc(_clip(view.summary, SUMMARY)),
        input_message_content=InputTextMessageContent(message_text=html, parse_mode="HTML"),
        reply_markup=_button(t(locale, "inline.open"), link),
    )


def _menu_article(locale: str, username: str) -> InlineQueryResultArticle:
    label = t(locale, "inline.menu.title")
    link = f"https://t.me/{username}"
    html = f'<a href="{_esc(link, quote=True)}">{_esc(label)}</a>\n{_esc(t(locale, "inline.menu.description"))}'
    return InlineQueryResultArticle(
        id="menu",
        title=_esc(label),
        description=_esc(t(locale, "inline.menu.description")),
        input_message_content=InputTextMessageContent(message_text=html, parse_mode="HTML"),
        reply_markup=_button(label, link),
    )


async def _username(query: InlineQuery, bot: Bot) -> str:
    if query.bot is not None and query.bot.username:
        return query.bot.username
    me = await bot.me()
    return me.username or FALLBACK_USERNAME


@router.inline_query()
async def on_inline(query: InlineQuery, repo: ContentRepository, bot: Bot) -> None:
    locale = resolve_locale(query.from_user.language_code if query.from_user else None)
    username = await _username(query, bot)
    text = (query.query or "").strip()
    results: list[InlineQueryResultArticle] = (
        [_card_article(view, locale, username) for view in repo.search(text, locale, limit=RESULTS)] if text else []
    )
    switch = None
    if not results:
        results = [_menu_article(locale, username)]
        switch = t(locale, "inline.menu.title")
    await bot.answer_inline_query(
        query.id,
        results=results,
        cache_time=CACHE_TIME,
        is_personal=False,
        switch_pm_text=switch,
        switch_pm_parameter="menu" if switch else None,
    )


def register(dp: Dispatcher, _bot: Bot) -> None:
    dp.include_router(router)
