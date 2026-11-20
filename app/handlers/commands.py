from __future__ import annotations

import logging

from aiogram import Bot, Dispatcher, Router
from aiogram.filters import Command
from aiogram.types import BotCommand, Message

from app.services.i18n import DEFAULT_LOCALE, ENABLED_LOCALES, t

logger = logging.getLogger(__name__)

router = Router(name="commands")


def locale_of(message: Message) -> str | None:
    return message.from_user.language_code if message.from_user else None


@router.message(Command("start"))
async def start(message: Message) -> None:
    await message.answer(t(locale_of(message), "start.welcome", bot="Байт — БЖД ТИСБИ"))


@router.message(Command("help"))
async def help_command(message: Message) -> None:
    locale = locale_of(message)
    await message.answer(t(locale, "help.title") + "\n" + t(locale, "help.commands"))


async def set_commands(bot: Bot, locale: str, language_code: str | None = None) -> None:
    await bot.set_my_commands(
        [
            BotCommand(command="start", description=t(locale, "commands.start")),
            BotCommand(command="help", description=t(locale, "commands.help")),
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


def register(dp: Dispatcher, _bot: Bot) -> None:
    dp.include_router(router)
    dp.startup.register(on_startup)