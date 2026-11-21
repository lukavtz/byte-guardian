"""Stateless navigation screens.

Every screen is rebuilt from the callback data: the menu, a category page, a
card and the language switch. Nothing is kept in FSM state, so a tap that
arrives hours late still draws the screen it names.
"""

from __future__ import annotations

import logging
import math
import secrets

import asyncpg
from aiogram import Bot, F, Router
from aiogram.types import CallbackQuery
from sqlalchemy import text
from sqlalchemy.exc import ProgrammingError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.config import Settings
from app.content.repository import ContentRepository
from app.content.types import CardView, CategoryView
from app.services.callbacks import (
    cb_chat,
    cb_chat_exit,
    cb_lang_menu,
    cb_menu,
    cb_random,
    cb_search_hint,
    cb_sections,
    cb_settings,
    parse_cb,
)
from app.services.i18n import ENABLED_LOCALES, resolve_locale, t
from app.services.settings import set_chat_mode
from app.services.user_locale import get_locale, locale_state, set_locale
from app.ui.delivery import show
from app.ui.emoji import premium
from app.ui.keyboards import PAGE_SIZE, menu_button, rich_button, rich_row
from app.ui.render import (
    RichScreen,
    render_card,
    render_category,
    render_chat,
    render_language,
    render_menu,
    render_not_found,
    render_onboarding,
    render_sections,
)

logger = logging.getLogger(__name__)

router = Router(name="nav")

OPT_IN_ON = "set:bc:on"
OPT_IN_OFF = "set:bc:off"
LANGUAGE_NAMES = {"ru": "Русский", "en": "English", "zh": "中文", "tt": "Татарча"}

OPT_IN_SELECT = text("SELECT broadcasts_opt_in FROM users WHERE id = :user_id")


async def save_locale(
    session_factory: async_sessionmaker[AsyncSession], user_id: int, code: str
) -> None:
    await set_locale(session_factory, user_id, code)


async def user_locale(
    session_factory: async_sessionmaker[AsyncSession], user: object | None
) -> str:
    """The stored column first, then the Telegram language, then ``ru``."""
    user_id = int(getattr(user, "id", 0)) if user is not None else 0
    return await get_locale(
        session_factory, user_id, getattr(user, "language_code", None)
    )


async def start_locale(
    session_factory: async_sessionmaker[AsyncSession], user: object | None
) -> tuple[str, bool]:
    """The language ``/start`` opens in, and whether the user already chose one.

    Before the picker the Telegram language preselects the answer; the column
    is written only when a language button is tapped, so an ignored picker
    still shows up on the next ``/start``.
    """
    if user is None:
        return resolve_locale(None), False
    user_id = int(getattr(user, "id", 0))
    stored, chosen = await locale_state(session_factory, user_id)
    if chosen and stored:
        return stored, True
    return resolve_locale(getattr(user, "language_code", None)), False


async def broadcast_opted_in(
    session_factory: async_sessionmaker[AsyncSession], user_id: int
) -> bool:
    try:
        async with session_factory() as session:
            async with session.begin():
                row = (await session.execute(OPT_IN_SELECT, {"user_id": user_id})).first()
    except (ProgrammingError, asyncpg.UndefinedColumnError):
        # ponytail: schema gap fallback; drop once the opt-in migration is universal.
        logger.warning("users.broadcasts_opt_in missing; assuming opted in")
        return True
    return True if row is None else bool(row[0])


def is_admin_id(settings: Settings | None, user_id: int | None) -> bool:
    """True only for an id the owner listed in ADMIN_IDS."""
    return settings is not None and user_id is not None and user_id in settings.admin_id_set


def settings_screen(locale: str, opted_in: bool) -> RichScreen:
    """Toggles stay neutral; the menu button is the one way back.

    The state shown comes from the caller's read of the DB, never from the
    button that was tapped.
    """
    toggle = OPT_IN_OFF if opted_in else OPT_IN_ON
    label = t(locale, "settings.broadcast.disable" if opted_in else "settings.broadcast.enable")
    status = t(locale, "settings.broadcast.on" if opted_in else "settings.broadcast.off")
    rows = [
        rich_row(
            rich_button(
                t(locale, "button.language"),
                data=cb_lang_menu(),
                icon=("lang", "🌐"),
            )
        ),
        rich_row(
            rich_button(label, data=toggle, icon=("broadcast", "📣"))
        ),
        rich_row(menu_button(locale)),
    ]
    html = (
        f"<h1>{premium('gear', '⚙')} {t(locale, 'settings.title')}</h1>"
        f"<p>{premium('lang', '🌐')} {t(locale, 'settings.language')}: "
        f"<b>{LANGUAGE_NAMES.get(locale, LANGUAGE_NAMES['ru'])}</b></p>"
        f"<p>{premium('broadcast', '📣')} {t(locale, 'settings.broadcasts')}: <b>{status}</b></p>"
    )
    return RichScreen(html + "\n".join(rows))


HELP_COMMANDS = (("start", "menu", "🏘"), ("help", "help", "💬"), ("settings", "lang", "🌐"))


def help_screen(locale: str) -> RichScreen:
    """The command list as a rich message: h2 block, premium icons, a way back."""
    lines = "<br/>".join(
        f"{premium(icon, fallback)} <b>/{name}</b> — {t(locale, f'commands.{name}')}"
        for name, icon, fallback in HELP_COMMANDS
    )
    html = (
        f"<h1>{premium('help', '💬')} {t(locale, 'help.screen')}</h1>"
        f"<p>{t(locale, 'menu.lead')}</p><hr/>"
        f"<h2>{premium('menu', '🏘')} {t(locale, 'help.title')}</h2>"
        f"<p>{lines}</p>"
    )
    return RichScreen(html + rich_row(menu_button(locale)))


def language_screen(locale: str) -> RichScreen:
    return render_language(locale, LANGUAGE_NAMES)


def onboarding_screen(locale: str) -> RichScreen:
    """The first-start picker: same four names, its own screen and callback."""
    return render_onboarding(locale, LANGUAGE_NAMES)


def _is_private(query: CallbackQuery) -> bool:
    message = query.message
    return message is not None and getattr(message.chat, "type", None) == "private"


async def _redraw(query: CallbackQuery, bot: Bot, screen: RichScreen) -> None:
    message = query.message
    if message is None or not _is_private(query):
        return
    await show(bot, message.chat.id, screen, message.message_id)


def _category(repo: ContentRepository, slug: str) -> CategoryView | None:
    return next((item for item in repo.categories() if item.slug == slug), None)


async def menu_screen(repo: ContentRepository, locale: str, is_admin: bool = False) -> RichScreen:
    # The search/language row is part of the rich message, like every other
    # button: nothing is appended to a keyboard underneath it.
    return render_menu(repo.categories(), locale, is_admin)


def sections_screen(repo: ContentRepository, locale: str) -> RichScreen:
    """The eight sections, one tap behind «Разделы»."""
    return render_sections(repo.categories(), locale)


async def category_screen(
    repo: ContentRepository, slug: str, page: int, locale: str
) -> RichScreen | None:
    category = _category(repo, slug)
    if category is None:
        return None
    threats = repo.threats(slug, locale)
    pages = max(1, math.ceil(len(threats) / PAGE_SIZE))
    page = min(max(page, 1), pages)
    visible = threats[(page - 1) * PAGE_SIZE : page * PAGE_SIZE]
    return render_category(category, visible, locale, page, pages)


def card_screen(
    repo: ContentRepository,
    slug: str,
    page: int,
    locale: str,
    can_game: bool = False,
    username: str | None = None,
) -> RichScreen | None:
    card: CardView | None = repo.card(slug, locale)
    if card is None:
        return None
    return render_card(
        card, locale, max(page, 1), repo.has_quiz(slug, locale), can_game, username
    )


def random_slug(repo: ContentRepository, locale: str) -> str | None:
    """Uniform over every enabled card, across every enabled section."""
    slugs = [
        threat.slug
        for category in repo.categories()
        for threat in repo.threats(category.slug, locale)
    ]
    return secrets.choice(slugs) if slugs else None


async def bot_username(bot: Bot) -> str | None:
    """The bot's @name for a deep link; ``None`` when Telegram will not say."""
    try:
        return (await bot.me()).username
    except Exception:
        logger.warning("could not resolve bot username for the share link")
        return None


async def _fallback(
    query: CallbackQuery,
    bot: Bot,
    repo: ContentRepository,
    locale: str,
    settings: Settings | None = None,
) -> None:
    await _redraw(query, bot, render_not_found(locale))


@router.callback_query(F.data == cb_menu())
async def on_menu(
    query: CallbackQuery,
    bot: Bot,
    repo: ContentRepository,
    session_factory: async_sessionmaker[AsyncSession],
    settings: Settings | None = None,
) -> None:
    await query.answer()
    locale = await user_locale(session_factory, query.from_user)
    admin = is_admin_id(settings, query.from_user.id if query.from_user else None)
    await _redraw(query, bot, await menu_screen(repo, locale, admin))


@router.callback_query(F.data == cb_sections())
async def on_sections(
    query: CallbackQuery,
    bot: Bot,
    repo: ContentRepository,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    await query.answer()
    locale = await user_locale(session_factory, query.from_user)
    await _redraw(query, bot, sections_screen(repo, locale))


@router.callback_query(F.data == cb_settings())
async def on_settings(
    query: CallbackQuery,
    bot: Bot,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    await query.answer()
    user = query.from_user
    if user is None:
        return
    locale = await user_locale(session_factory, user)
    await _redraw(query, bot, settings_screen(locale, await broadcast_opted_in(session_factory, user.id)))


@router.callback_query(F.data.startswith("b1:cat:"))
async def on_category(
    query: CallbackQuery,
    bot: Bot,
    repo: ContentRepository,
    session_factory: async_sessionmaker[AsyncSession],
    settings: Settings | None = None,
) -> None:
    await query.answer()
    callback = parse_cb(query.data)
    if callback is None or callback.action != "cat":
        return
    locale = await user_locale(session_factory, query.from_user)
    screen = await category_screen(repo, callback.arg, callback.page, locale)
    if screen is None:
        await _fallback(query, bot, repo, locale, settings)
        return
    await _redraw(query, bot, screen)


@router.callback_query(F.data.startswith("b1:card:"))
async def on_card(
    query: CallbackQuery,
    bot: Bot,
    repo: ContentRepository,
    session_factory: async_sessionmaker[AsyncSession],
    settings: Settings | None = None,
) -> None:
    await query.answer()
    callback = parse_cb(query.data)
    if callback is None or callback.action != "card":
        return
    locale = await user_locale(session_factory, query.from_user)
    screen = card_screen(
        repo,
        callback.arg,
        callback.page,
        locale,
        bool(settings and settings.widgets_enabled and _is_private(query)),
        await bot_username(bot),
    )
    if screen is None:
        await _fallback(query, bot, repo, locale, settings)
        return
    await _redraw(query, bot, screen)


@router.callback_query(F.data == cb_random())
async def on_random(
    query: CallbackQuery,
    bot: Bot,
    repo: ContentRepository,
    session_factory: async_sessionmaker[AsyncSession],
    settings: Settings | None = None,
) -> None:
    await query.answer()
    locale = await user_locale(session_factory, query.from_user)
    slug = random_slug(repo, locale)
    screen = (
        card_screen(
            repo,
            slug,
            1,
            locale,
            bool(settings and settings.widgets_enabled and _is_private(query)),
            await bot_username(bot),
        )
        if slug
        else None
    )
    await _redraw(query, bot, screen or render_not_found(locale))


@router.callback_query(F.data.startswith("b1:onb:"))
async def on_onboarding_lang(
    query: CallbackQuery,
    bot: Bot,
    repo: ContentRepository,
    session_factory: async_sessionmaker[AsyncSession],
    settings: Settings | None = None,
) -> None:
    """A language tap on the first-start picker: save it and step into the menu."""
    await query.answer()
    callback = parse_cb(query.data)
    if callback is None or callback.action != "onb" or callback.arg not in ENABLED_LOCALES:
        return
    user = query.from_user
    if user is None:
        return
    await save_locale(session_factory, user.id, callback.arg)
    await _redraw(
        query,
        bot,
        await menu_screen(repo, callback.arg, is_admin_id(settings, user.id)),
    )


@router.callback_query(F.data.startswith("b1:lang:"))
async def on_lang(
    query: CallbackQuery,
    bot: Bot,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    await query.answer()
    callback = parse_cb(query.data)
    if callback is None or callback.action != "lang" or callback.arg not in ENABLED_LOCALES:
        return
    user = query.from_user
    if user is None:
        return
    await save_locale(session_factory, user.id, callback.arg)
    await _redraw(query, bot, language_screen(callback.arg))


@router.callback_query(F.data == cb_lang_menu())
async def on_lang_menu(
    query: CallbackQuery,
    bot: Bot,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    await query.answer()
    locale = await user_locale(session_factory, query.from_user)
    await _redraw(query, bot, language_screen(locale))


@router.callback_query(F.data == cb_chat())
async def on_chat(
    query: CallbackQuery,
    bot: Bot,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    await query.answer()
    user = query.from_user
    if user is None:
        return
    await set_chat_mode(session_factory, user.id, True)
    locale = await user_locale(session_factory, user)
    await _redraw(query, bot, render_chat(locale))


@router.callback_query(F.data == cb_chat_exit())
async def on_chat_exit(
    query: CallbackQuery,
    bot: Bot,
    repo: ContentRepository,
    session_factory: async_sessionmaker[AsyncSession],
    settings: Settings | None = None,
) -> None:
    await query.answer()
    user = query.from_user
    if user is None:
        return
    await set_chat_mode(session_factory, user.id, False)
    from app.handlers.llm import HISTORY  # local: avoids an import cycle at module load

    chat_id = query.message.chat.id if query.message is not None else 0
    HISTORY.clear(chat_id=chat_id, user_id=user.id)
    locale = await user_locale(session_factory, user)
    await _redraw(
        query, bot, await menu_screen(repo, locale, is_admin_id(settings, user.id))
    )


@router.callback_query(F.data == cb_search_hint())
async def on_search_hint(
    query: CallbackQuery,
    bot: Bot,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    await query.answer()
    locale = await user_locale(session_factory, query.from_user)
    screen = RichScreen(
        f"<p>{t(locale, 'menu.search_hint')}</p>" + rich_row(menu_button(locale))
    )
    await _redraw(query, bot, screen)


def register(dp: object, _bot: Bot) -> None:
    dp.include_router(router)  # type: ignore[attr-defined]