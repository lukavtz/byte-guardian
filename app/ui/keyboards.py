"""Inline keyboard primitives: one button, one markup, hard caps.

Telegram allows 100 buttons in theory; a phone shows about eight without
scrolling, so the caps here are the design's, not the API's.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from typing import Literal

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

Style = Literal["primary", "success", "danger"]

MAX_BUTTONS = 8
PAGE_SIZE = 5


def button(text: str, data: str | None = None, style: Style | None = None, **kwargs: object) -> InlineKeyboardButton:
    """One button; `data` is callback data, `kwargs` covers url/switch_*."""
    if data is not None:
        kwargs["callback_data"] = data
    return InlineKeyboardButton(text=text, style=style, **kwargs)  # type: ignore[arg-type]


def markup(rows: Iterable[Sequence[InlineKeyboardButton]]) -> InlineKeyboardMarkup:
    """Rows trimmed to `MAX_BUTTONS`; a row that would overflow is dropped whole."""
    kept: list[list[InlineKeyboardButton]] = []
    count = 0
    for row in rows:
        if count + len(row) > MAX_BUTTONS:
            break
        kept.append(list(row))
        count += len(row)
    return InlineKeyboardMarkup(inline_keyboard=kept)


def paired(items: Sequence[InlineKeyboardButton]) -> list[list[InlineKeyboardButton]]:
    """Two buttons per row, the way the category grid reads."""
    return [list(items[index : index + 2]) for index in range(0, len(items), 2)]
