"""Button primitives: rich in-message rows, plus the inline keyboard fallback.

Rich messages draw their own buttons (`<tg-button-row>`), so a screen carries
its controls inside the text and needs no keyboard glued underneath. The
`InlineKeyboard*` half stays for the screens that have no rich form and for the
one degraded retry in `app.ui.delivery`, when Telegram refuses `tg-button`.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Sequence
from html import escape, unescape
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


# --- rich in-message buttons ---------------------------------------------

BUTTON_ROW = re.compile(r"<tg-button-row\b[^>]*>(.*?)</tg-button-row>", re.DOTALL)
BUTTON = re.compile(r"<tg-button(?=[\s>])([^>]*)>(.*?)</tg-button>", re.DOTALL)
ATTRIBUTE = re.compile(r'([\w-]+)="([^"]*)"')
TAGS = re.compile(r"<[^>]+>")


def rich_button(
    label: str,
    *,
    data: str | None = None,
    style: Style | Literal["link"] | None = None,
    query: str | None = None,
) -> str:
    """One `<tg-button>`: `data` is callback data, `query` an inline search."""
    if data is not None:
        attributes = f'type="callback_data" data="{escape(data, quote=True)}"'
    elif query is not None:
        attributes = f'type="switch_inline_query_current_chat" query="{escape(query, quote=True)}"'
    else:
        raise ValueError("a rich button needs callback data or an inline query")
    if style:
        attributes += f' style="{style}"'
    return f"<tg-button {attributes}>{escape(label, quote=False)}</tg-button>"


def rich_row(*buttons: str) -> str:
    """One row of buttons; the client stretches them over the message width."""
    return f"<tg-button-row>{''.join(buttons)}</tg-button-row>"


def strip_rich_buttons(html: str) -> str:
    """The markup without its button rows, for the keyboard fallback."""
    return BUTTON_ROW.sub("", html)


def keyboard_from_rich(html: str) -> InlineKeyboardMarkup | None:
    """Rebuild the buttons of `html` as an inline keyboard.

    Used only when Telegram refuses `tg-button`: the reader keeps every way
    forward, one layer lower. Labels lose their formatting, because an inline
    keyboard takes plain text.
    """
    rows: list[list[InlineKeyboardButton]] = []
    for row_html in BUTTON_ROW.findall(html):
        row = []
        for attributes, label in BUTTON.findall(row_html):
            inline = _inline_button(attributes, label)
            if inline is not None:
                row.append(inline)
        if row:
            rows.append(row)
    return InlineKeyboardMarkup(inline_keyboard=rows) if rows else None


def _inline_button(attributes: str, label: str) -> InlineKeyboardButton | None:
    values = dict(ATTRIBUTE.findall(attributes))
    text = unescape(TAGS.sub("", label)).strip()
    style = values.get("style")
    if values.get("type") == "callback_data":
        return InlineKeyboardButton(
            text=text,
            callback_data=unescape(values.get("data", "")),
            style=style,  # type: ignore[arg-type]
        )
    if values.get("type") == "switch_inline_query_current_chat":
        return InlineKeyboardButton(text=text, switch_inline_query_current_chat=unescape(values.get("query", "")))
    return None
