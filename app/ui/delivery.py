"""The one place a `RichScreen` becomes an API call.

`show` sends a new message or replaces the existing one in place, so the screen
never flickers and the chat never grows a second copy of the same menu. Four
Telegram refusals are handled here, once, instead of at every call site:

* the edit changed nothing (`message is not modified`) — the desired state is
  already on screen, so it is a success;
* the message is gone, too old, no longer editable, or its callback query
  expired — the screen goes out as a new message;
* a `<tg-emoji>` was refused — the same screen is retried without premium
  emoji, for this screen only;
* a `<tg-button>` was refused — the buttons are lifted out of the markup into
  an inline keyboard, so navigation survives on an older client.

Anything else is re-raised: a blocked chat or a bad payload must not be hidden
from the caller.
"""

from __future__ import annotations

from typing import TypedDict

from aiogram import Bot
from aiogram.exceptions import TelegramBadRequest
from aiogram.types import InlineKeyboardMarkup, InputRichMessage, Message

from app.ui.emoji import strip_premium
from app.ui.keyboards import keyboard_from_rich, strip_rich_buttons
from app.ui.render import RichScreen

# One attempt per thing that can be given up: the message, its premium emoji,
# its in-message buttons, a stale target. The cap is the guarantee that a
# wedged chat cannot loop.
MAX_ATTEMPTS = 4

NOT_MODIFIED = "message is not modified"
STALE = (
    "message to edit not found",
    "message to edit too old",
    "message can't be edited",
    "query is too old",
)


class _Markup(TypedDict, total=False):
    """`reply_markup`, present only for the screens without a rich form."""

    reply_markup: InlineKeyboardMarkup


async def _deliver(bot: Bot, chat_id: int, screen: RichScreen, message_id: int | None) -> Message | None:
    rich = InputRichMessage(html=screen.html)
    # A rich screen carries its buttons itself; a keyboard is passed only for
    # the screens that have no rich form.
    markup: _Markup = {"reply_markup": screen.keyboard} if screen.keyboard is not None else {}
    if message_id is None:
        return await bot.send_rich_message(chat_id=chat_id, rich_message=rich, **markup)
    edited = await bot.edit_message_text(chat_id=chat_id, message_id=message_id, rich_message=rich, **markup)
    # `editMessageText` answers `True` for an inline message; ours is always a
    # chat message, so a bool means there is no id to report.
    return edited if not isinstance(edited, bool) else None


def _buttons_refused(description: str, html: str) -> bool:
    return "<tg-button" in html and "button" in description.lower()


async def show(bot: Bot, chat_id: int, screen: RichScreen, message_id: int | None = None) -> Message | None:
    """Draw `screen` in a chat, editing `message_id` when it is given.

    Returns the message that now carries the screen, or None when nothing had
    to change.
    """
    html, keyboard = screen.html, screen.keyboard
    for attempt in range(1, MAX_ATTEMPTS + 1):
        try:
            return await _deliver(bot, chat_id, RichScreen(html, keyboard), message_id)
        except TelegramBadRequest as error:
            description = str(error)
            if NOT_MODIFIED in description:
                return None
            if attempt == MAX_ATTEMPTS:
                raise
            if any(fragment in description for fragment in STALE):
                message_id = None
                continue
            if _buttons_refused(description, html):
                # The buttons move one layer down; the text loses only its
                # rows, so the reader keeps every way forward.
                keyboard = keyboard_from_rich(html)
                html = strip_rich_buttons(html)
                continue
            if "<tg-emoji" in html and "emoji" in description.lower():
                # Telegram refuses an id the bot may not use. A screen that
                # keeps its text and loses its icons beats no screen at all.
                html = strip_premium(html)
                continue
            raise
    raise AssertionError("unreachable")