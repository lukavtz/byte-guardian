"""Two answer sinks: private-chat drafts and group status edits.

Both are best-effort chrome around the model stream. A refused Bot API call
disables the sink and leaves the caller's final-message path untouched, so an
older client or a 429 never costs the reader an answer.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable
from typing import Protocol

from aiogram import Bot
from aiogram.exceptions import TelegramRetryAfter
from aiogram.types import InlineKeyboardMarkup, InputRichMessage

from app.services.llm import draft_html, strip_html
from app.ui.delivery import show
from app.ui.render import RichScreen

logger = logging.getLogger(__name__)

DRAFT_INTERVAL = 1.0
STATUS_EDIT_GAP = 10.0
MAX_FAILURES = 3


class AnswerSink(Protocol):
    async def push(self, text: str) -> None: ...
    async def finish(self, text: str, markup: InlineKeyboardMarkup | None) -> None: ...
    async def close(self) -> None: ...


class DraftSink:
    """Private-chat rich draft; ephemeral, so `finish` always sends the answer."""

    def __init__(
        self,
        bot: Bot,
        chat_id: int,
        draft_id: int,
        *,
        interval: float = DRAFT_INTERVAL,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._bot = bot
        self._chat_id = chat_id
        self._draft_id = draft_id
        self._interval = interval
        self._clock = clock
        self._last = -interval
        self._last_text = ""
        self._last_raw = ""
        self._disabled_until = 0.0
        self._failures = 0
        self._in_flight = False
        self.disabled = False

    async def push(self, text: str) -> None:
        if self.disabled or self._in_flight:
            return
        now = self._clock()
        if now < self._disabled_until or now - self._last < self._interval:
            return
        html = draft_html(text, complete=False)
        if not html or text == self._last_raw:
            return
        self._last, self._last_raw, self._last_text, self._in_flight = now, text, html, True
        try:
            await self._bot.send_rich_message_draft(
                chat_id=self._chat_id,
                draft_id=self._draft_id,
                rich_message=InputRichMessage(html=html),
                can_stop=True,
            )
            self._failures = 0
        except TelegramRetryAfter as error:
            self._disabled_until = now + float(error.retry_after)
        except Exception:
            self._failures += 1
            if self._failures >= MAX_FAILURES:
                self.disabled = True
        finally:
            self._in_flight = False

    async def finish(self, text: str, markup: InlineKeyboardMarkup | None) -> None:
        await show(self._bot, self._chat_id, RichScreen(text, markup))
        try:
            await self._bot.send_message_draft(
                chat_id=self._chat_id, draft_id=self._draft_id, text=""
            )
        except Exception:
            logger.debug("draft clear failed", exc_info=True)

    async def close(self) -> None:
        return None


class StatusEditSink:
    """Group status: one message, edited sparingly, finalised with the answer."""

    def __init__(
        self,
        bot: Bot,
        chat_id: int,
        *,
        interval: float = DRAFT_INTERVAL,
        min_edit_gap: float = STATUS_EDIT_GAP,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._bot = bot
        self._chat_id = chat_id
        self._interval = interval
        self._gap = min_edit_gap
        self._clock = clock
        self._message_id: int | None = None
        self._last = -interval
        self._last_text = ""
        self._last_raw = ""
        self._last_edit = -min_edit_gap
        self._failures = 0
        self.disabled = False

    async def push(self, text: str) -> None:
        if self.disabled:
            return
        now = self._clock()
        if now - self._last < self._interval:
            return
        self._last = now
        if self._message_id is None:
            try:
                message = await self._bot.send_message(chat_id=self._chat_id, text=strip_html(text) or "…")
                self._message_id = getattr(message, "message_id", None)
                self._last_text = text
            except Exception:
                self._fail()
            return
        if text == self._last_text or now - self._last_edit < self._gap:
            return
        try:
            await self._bot.edit_message_text(
                chat_id=self._chat_id, message_id=self._message_id, text=strip_html(text)
            )
            self._last_text = text
            self._last_edit = now
            self._failures = 0
        except Exception:
            self._fail()

    async def finish(self, text: str, markup: InlineKeyboardMarkup | None) -> None:
        if self._message_id is None:
            await self._bot.send_message(chat_id=self._chat_id, text=text, reply_markup=markup)
            return
        try:
            await self._bot.edit_message_text(
                chat_id=self._chat_id,
                message_id=self._message_id,
                text=text,
                reply_markup=markup,
            )
        except Exception:
            await self._bot.send_message(chat_id=self._chat_id, text=text, reply_markup=markup)

    async def close(self) -> None:
        return None

    def _fail(self) -> None:
        self._failures += 1
        if self._failures >= MAX_FAILURES:
            self.disabled = True