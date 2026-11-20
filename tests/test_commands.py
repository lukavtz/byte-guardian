"""Command smoke tests: /help and /settings answer without Telegram."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.handlers import commands


class FakeBot:
    def __init__(self) -> None:
        self.sent: list[dict] = []

    async def send_rich_message(self, **kwargs: object) -> SimpleNamespace:
        self.sent.append(kwargs)
        return SimpleNamespace(message_id=1)


class FakeSessions:
    def __call__(self):
        return self

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc: object) -> bool:
        return False

    def begin(self):
        return self

    async def execute(self, statement, params=None):
        return SimpleNamespace(first=lambda: None)


def _message() -> SimpleNamespace:
    return SimpleNamespace(
        chat=SimpleNamespace(id=1, type="private"),
        from_user=SimpleNamespace(id=1, language_code="ru"),
        message_id=1,
        answer=AsyncMock(),
    )


@pytest.mark.asyncio
async def test_help_lists_commands() -> None:
    message = _message()
    await commands.help_command(message, FakeSessions())
    text = message.answer.await_args.args[0]
    assert "/start" in text
    assert "/settings" in text


@pytest.mark.asyncio
async def test_settings_renders_screen() -> None:
    bot = FakeBot()
    await commands.settings_command(_message(), bot, FakeSessions())
    assert bot.sent and "Настройки" in bot.sent[0]["rich_message"].html
