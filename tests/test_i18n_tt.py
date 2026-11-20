from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest
from aiogram import Dispatcher

from app.handlers import commands
from app.services.i18n import DEFAULT_LOCALE, ENABLED_LOCALES, resolve_locale, t


def test_resolve_tt_with_region() -> None:
    assert resolve_locale("tt-TR") == "tt"
    assert resolve_locale("tt") == "tt"


def test_tt_catalog_has_translation() -> None:
    text = t("tt", "start.welcome", bot="Байт")
    assert text != "start.welcome"
    assert "Рәхим" in text


def test_unknown_locale_falls_back_to_ru() -> None:
    assert resolve_locale("de") == "ru"


def test_tt_is_enabled() -> None:
    assert "tt" in ENABLED_LOCALES


@pytest.mark.asyncio
async def test_startup_registers_commands_for_every_locale() -> None:
    bot = MagicMock()
    bot.set_my_commands = AsyncMock()
    await commands.on_startup(bot)
    calls = bot.set_my_commands.await_args_list
    language_codes = {call.kwargs["language_code"] for call in calls}
    assert language_codes == {*ENABLED_LOCALES, None}
    default = [call for call in calls if call.kwargs["language_code"] is None]
    assert len(default) == 1
    assert [command.description for command in default[0].args[0]] == [
        t(DEFAULT_LOCALE, "commands.start"),
        t(DEFAULT_LOCALE, "commands.help"),
    ]


@pytest.mark.asyncio
async def test_startup_survives_telegram_errors() -> None:
    bot = MagicMock()
    bot.set_my_commands = AsyncMock(
        side_effect=[RuntimeError("boom"), *([None] * len(ENABLED_LOCALES))]
    )
    await commands.on_startup(bot)
    assert bot.set_my_commands.await_count == len(ENABLED_LOCALES) + 1


def test_register_wires_startup_handler() -> None:
    dp = Dispatcher()
    commands.register(dp, MagicMock())
    callbacks = [handler.callback for handler in dp.startup.handlers]
    assert commands.on_startup in callbacks