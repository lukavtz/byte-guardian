"""The one place a `RichScreen` becomes an API call.

`show` sends a new message or replaces the existing one in place, so the screen
never flickers and the chat never grows a second copy of the same menu. Three
Telegram refusals are handled here, once, instead of at every call site:

* the edit changed nothing (`message is not modified`) — the desired state is
  already on screen, so it is a success;
* the message is too old or gone — the screen goes out as a new message;
* a `<tg-emoji>` was refused — the same screen is retried without premium
  emoji, for this screen only.

Anything else is re-raised: a blocked chat or a bad payload must not be hidden
from the caller.
"""

from __future__ import annotations

from aiogram import Bot
from aiogram.exceptions import TelegramBadRequest
from aiogram.types import InputRichMessage, Message

from app.ui.emoji import strip_premium
from app.ui.render import RichScreen

# One attempt per thing that can be given up: the message, its premium emoji,
# a stale target. The cap is the guarantee that a wedged chat cannot loop.
MAX_ATTEMPTS = 4

NOT_MODIFIED = "message is not modified"
STALE = ("message to edit not found", "message to edit too old", "query is too old")


async def _deliver(bot: Bot, chat_id: int, screen: RichScreen, message_id: int | None) -> Message | None:
    rich = InputRichMessage(html=screen.html)
    if message_id is None:
        return await bot.send_rich_message(chat_id=chat_id, rich_message=rich, reply_markup=screen.keyboard)
    edited = await bot.edit_message_text(
        chat_id=chat_id,
        message_id=message_id,
        rich_message=rich,
        reply_markup=screen.keyboard,
    )
    # `editMessageText` answers `True` for an inline message; ours is always a
    # chat message, so a bool means there is no id to report.
    return edited if not isinstance(edited, bool) else None


async def show(bot: Bot, chat_id: int, screen: RichScreen, message_id: int | None = None) -> Message | None:
    """Draw `screen` in a chat, editing `message_id` when it is given.

    Returns the message that now carries the screen, or None when nothing had
    to change.
    """
    html = screen.html
    for attempt in range(1, MAX_ATTEMPTS + 1):
        try:
            return await _deliver(bot, chat_id, RichScreen(html, screen.keyboard), message_id)
        except TelegramBadRequest as error:
            description = str(error)
            if NOT_MODIFIED in description:
                return None
            if attempt == MAX_ATTEMPTS:
                raise
            if any(fragment in description for fragment in STALE):
                message_id = None
                continue
            if "<tg-emoji" in html and "emoji" in description.lower():
                # Telegram refuses an id the bot may not use. A screen that
                # keeps its text and loses its icons beats no screen at all.
                html = strip_premium(html)
                continue
            raise
    raise AssertionError("unreachable")
