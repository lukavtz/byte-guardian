from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from app.handlers import commands
from app.services.i18n import ENABLED_LOCALES, resolve_locale, t


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
    assert bot.set_my_commands.await_count == len(ENABLED_LOCALES)
    language_codes = {
        call.kwargs["language_code"] for call in bot.set_my_commands.await_args_list
    }
    assert language_codes == set(ENABLED_LOCALES)