"""Commands: start (with deep link), help, settings."""

from __future__ import annotations

import logging

from aiogram import Bot, F, Router
from aiogram.filters import Command, CommandObject
from aiogram.types import BotCommand, CallbackQuery, Message
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.config import Settings
from app.content.repository import ContentRepository
from app.handlers.nav import (
    OPT_IN_OFF,
    OPT_IN_ON,
    broadcast_opted_in,
    card_screen,
    is_admin_id,
    menu_screen,
    pin_initial_locale,
    settings_screen,
    user_locale,
)
from app.services.broadcast import set_opt_in
from app.services.i18n import DEFAULT_LOCALE, ENABLED_LOCALES, t
from app.ui.delivery import show

logger = logging.getLogger(__name__)

router = Router(name="commands")


def _card_from_payload(payload: str) -> str | None:
    payload = payload.strip()
    return payload[len("card_") :] if payload.startswith("card_") else None


async def _private_only(
    message: Message,
    bot: Bot,
    session_factory: async_sessionmaker[AsyncSession],
) -> bool:
    """Interactive screens are edited in place, so groups get a link instead."""
    if getattr(message.chat, "type", None) == "private":
        return True
    locale = await user_locale(session_factory, message.from_user)
    username = None
    try:
        me = await bot.me()
        username = me.username
    except Exception:
        logger.warning("could not resolve bot username for the group reply")
    if username:
        await message.answer(t(locale, "group.private_only", link=f"https://t.me/{username}"))
    else:
        await message.answer(t(locale, "group.private_only_plain"))
    return False


@router.message(Command("start"))
async def start(
    message: Message,
    command: CommandObject,
    bot: Bot,
    repo: ContentRepository,
    session_factory: async_sessionmaker[AsyncSession],
    settings: Settings | None = None,
) -> None:
    if not await _private_only(message, bot, session_factory):
        return
    locale = await pin_initial_locale(session_factory, message.from_user)
    slug = _card_from_payload(command.args or "")
    screen = (
        card_screen(repo, slug, 1, locale, bool(settings and settings.widgets_enabled))
        if slug
        else None
    )
    if screen is None:
        user_id = message.from_user.id if message.from_user else None
        screen = await menu_screen(repo, locale, is_admin_id(settings, user_id))
    await show(bot, message.chat.id, screen)


@router.message(Command("help"))
async def help_command(
    message: Message,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    locale = await user_locale(session_factory, message.from_user)
    await message.answer(
        t(locale, "help.title") + "\n" + t(locale, "help.commands") + "\n" + t(locale, "settings.command")
    )


@router.message(Command("settings"))
async def settings_command(
    message: Message,
    bot: Bot,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    if not await _private_only(message, bot, session_factory):
        return
    locale = await user_locale(session_factory, message.from_user)
    user = message.from_user
    opted_in = await broadcast_opted_in(session_factory, user.id) if user else True
    await show(bot, message.chat.id, settings_screen(locale, opted_in))


@router.callback_query(F.data.in_({OPT_IN_ON, OPT_IN_OFF}))
async def toggle_broadcasts(
    query: CallbackQuery,
    bot: Bot,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    await query.answer()
    user = query.from_user
    message = query.message
    if user is None or message is None or getattr(message.chat, "type", None) != "private":
        return
    opted_in = query.data == OPT_IN_ON
    await set_opt_in(session_factory, user.id, opted_in)
    locale = await user_locale(session_factory, user)
    await show(bot, message.chat.id, settings_screen(locale, opted_in), message.message_id)


async def set_commands(bot: Bot, locale: str, language_code: str | None = None) -> None:
    await bot.set_my_commands(
        [
            BotCommand(command="start", description=t(locale, "commands.start")),
            BotCommand(command="help", description=t(locale, "commands.help")),
            BotCommand(command="settings", description=t(locale, "commands.settings")),
        ],
        language_code=language_code,
    )


async def on_startup(bot: Bot) -> None:
    for locale in ENABLED_LOCALES:
        try:
            await set_commands(bot, locale, locale)
        except Exception:
            logger.exception("failed to set %s command menu", locale)
    try:
        await set_commands(bot, DEFAULT_LOCALE)
    except Exception:
        logger.exception("failed to set default command menu")


def register(dp: object, _bot: Bot) -> None:
    dp.include_router(router)  # type: ignore[attr-defined]
    dp.startup.register(on_startup)  # type: ignore[attr-defined]
