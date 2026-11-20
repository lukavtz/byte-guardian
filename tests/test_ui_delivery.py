from __future__ import annotations

from typing import Any

import pytest
from aiogram.exceptions import TelegramBadRequest
from aiogram.methods import EditMessageText, SendRichMessage
from aiogram.types import InputRichMessage

from app.ui.delivery import MAX_ATTEMPTS, show
from app.ui.render import RichScreen

CHAT_ID = 42
MESSAGE_ID = 7
BUTTONS = (
    '<h1>Меню</h1><tg-button-row><tg-button type="callback_data" data="b1:menu" style="primary">Меню'
    "</tg-button></tg-button-row>"
)


class FakeBot:
    """Records the API calls and answers like Telegram would."""

    def __init__(self, *errors: TelegramBadRequest | None) -> None:
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self.errors = list(errors)
        self.sent = object()

    async def _answer(self, name: str, kwargs: dict[str, Any]) -> object:
        self.calls.append((name, kwargs))
        if self.errors:
            error = self.errors.pop(0)
            if error is not None:
                raise error
        return self.sent

    async def send_rich_message(self, **kwargs: Any) -> object:
        return await self._answer("send_rich_message", kwargs)

    async def edit_message_text(self, **kwargs: Any) -> object:
        return await self._answer("edit_message_text", kwargs)

    async def send_message(self, **kwargs: Any) -> object:
        return await self._answer("send_message", kwargs)


def _bad(method_name: str, description: str) -> TelegramBadRequest:
    if method_name == "edit_message_text":
        method = EditMessageText(chat_id=CHAT_ID, message_id=MESSAGE_ID, rich_message=InputRichMessage(html="x"))
    else:
        method = SendRichMessage(chat_id=CHAT_ID, rich_message=InputRichMessage(html="x"))
    return TelegramBadRequest(method=method, message=description)


def _screen(html: str = BUTTONS) -> RichScreen:
    return RichScreen(html=html)


def _html(call: tuple[str, dict[str, Any]]) -> str:
    rich: InputRichMessage = call[1]["rich_message"]
    assert isinstance(rich, InputRichMessage)
    assert rich.html is not None
    return rich.html


@pytest.mark.asyncio
async def test_new_screen_is_sent_as_a_rich_message() -> None:
    bot = FakeBot()
    screen = _screen()
    message = await show(bot, CHAT_ID, screen)

    assert message is bot.sent
    assert [name for name, _ in bot.calls] == ["send_rich_message"]
    assert bot.calls[0][1]["chat_id"] == CHAT_ID
    assert "reply_markup" not in bot.calls[0][1]
    assert _html(bot.calls[0]) == screen.html


@pytest.mark.asyncio
async def test_existing_message_is_edited_in_place() -> None:
    bot = FakeBot()
    screen = _screen()
    message = await show(bot, CHAT_ID, screen, MESSAGE_ID)

    assert message is bot.sent
    assert [name for name, _ in bot.calls] == ["edit_message_text"]
    assert bot.calls[0][1]["message_id"] == MESSAGE_ID
    assert "reply_markup" not in bot.calls[0][1]
    assert _html(bot.calls[0]) == screen.html


@pytest.mark.asyncio
async def test_unchanged_edit_is_success() -> None:
    bot = FakeBot(_bad("edit_message_text", "Bad Request: message is not modified"))
    assert await show(bot, CHAT_ID, _screen(), MESSAGE_ID) is None
    assert len(bot.calls) == 1


@pytest.mark.asyncio
async def test_refused_premium_emoji_is_retried_without_it() -> None:
    bot = FakeBot(_bad("send_rich_message", "Bad Request: can't use custom emoji"))
    screen = _screen('<h1><tg-emoji emoji-id="1">🛡</tg-emoji> Меню</h1>')
    await show(bot, CHAT_ID, screen)

    assert len(bot.calls) == 2
    assert "<tg-emoji" not in _html(bot.calls[1])
    assert "reply_markup" not in bot.calls[1][1]


@pytest.mark.asyncio
async def test_refused_buttons_become_an_inline_keyboard() -> None:
    bot = FakeBot(_bad("send_rich_message", "Bad Request: RICH_MESSAGE_BUTTON_INVALID"))
    screen = _screen()
    await show(bot, CHAT_ID, screen)

    assert len(bot.calls) == 2
    assert "<tg-button" not in _html(bot.calls[1])
    assert "Меню" in _html(bot.calls[1])
    markup = bot.calls[1][1]["reply_markup"]
    assert markup is not None
    assert markup.inline_keyboard[0][0].callback_data == "b1:menu"
    assert markup.inline_keyboard[0][0].style == "primary"


@pytest.mark.asyncio
async def test_button_fallback_keeps_the_edit_in_place() -> None:
    bot = FakeBot(_bad("edit_message_text", "Bad Request: RICH_MESSAGE_BUTTON_INVALID"))
    await show(bot, CHAT_ID, _screen(), MESSAGE_ID)

    assert [name for name, _ in bot.calls] == ["edit_message_text", "edit_message_text"]
    assert bot.calls[1][1]["message_id"] == MESSAGE_ID
    assert bot.calls[1][1]["reply_markup"] is not None


@pytest.mark.asyncio
async def test_stale_edit_becomes_a_fresh_message() -> None:
    bot = FakeBot(_bad("edit_message_text", "Bad Request: message to edit not found"))
    screen = _screen()
    await show(bot, CHAT_ID, screen, MESSAGE_ID)

    assert [name for name, _ in bot.calls] == ["edit_message_text", "send_rich_message"]
    assert "message_id" not in bot.calls[1][1]
    assert "reply_markup" not in bot.calls[1][1]


@pytest.mark.asyncio
async def test_other_bad_requests_are_raised() -> None:
    bot = FakeBot(_bad("send_rich_message", "Bad Request: chat not found"))
    with pytest.raises(TelegramBadRequest):
        await show(bot, CHAT_ID, _screen())
    assert len(bot.calls) == 1


@pytest.mark.asyncio
async def test_ladder_stops_after_max_attempts() -> None:
    bot = FakeBot(*(_bad("edit_message_text", "Bad Request: message to edit not found") for _ in range(MAX_ATTEMPTS)))
    with pytest.raises(TelegramBadRequest):
        await show(bot, CHAT_ID, _screen(), MESSAGE_ID)
    assert len(bot.calls) == MAX_ATTEMPTS


@pytest.mark.asyncio
async def test_degraded_screen_keeps_the_message() -> None:
    bot = FakeBot(_bad("edit_message_text", "Bad Request: can't use custom emoji"))
    screen = _screen('<h1><tg-emoji emoji-id="1">🛡</tg-emoji> Меню</h1>')
    await show(bot, CHAT_ID, screen, MESSAGE_ID)

    assert bot.calls[1][1]["message_id"] == MESSAGE_ID
    assert "reply_markup" not in bot.calls[1][1]


@pytest.mark.asyncio
async def test_text_screen_is_sent_without_a_keyboard() -> None:
    bot = FakeBot()
    await show(bot, CHAT_ID, RichScreen(html="<h1>x</h1>"))
    assert "reply_markup" not in bot.calls[0][1]