"""Custom emoji: a name becomes a `<tg-emoji>` tag, or the plain character.

Ids are pinned through `TELEGRAM_EMOJI_IDS` as `name:id,name:id`. Without an id
the character is used, so every screen is complete before the owner buys the
pack. `strip_premium` is the degradation step: Telegram refuses the tag, the
screen keeps its text.
"""

from __future__ import annotations

import os
import re

TAG = re.compile(r"<tg-emoji\b[^>]*>(.*?)</tg-emoji>", re.DOTALL)


def _parse(raw: str) -> dict[str, str]:
    """`name:id,name:id` -> `{name: id}`; malformed pairs are ignored."""
    ids: dict[str, str] = {}
    for token in raw.split(","):
        name, separator, custom_id = token.partition(":")
        name, custom_id = name.strip(), custom_id.strip()
        if separator and name and custom_id.isdigit():
            ids[name] = custom_id
    return ids


# name -> custom emoji id. Read once: a restart is the way to apply a new .env.
CUSTOM: dict[str, str] = _parse(os.environ.get("TELEGRAM_EMOJI_IDS", ""))


def emoji(name: str, fallback: str) -> str:
    """The pinned `<tg-emoji>` for `name`, or `fallback` when it has no id."""
    custom_id = CUSTOM.get(name)
    if not custom_id:
        return fallback
    return f'<tg-emoji emoji-id="{custom_id}">{fallback}</tg-emoji>'


def strip_premium(html: str) -> str:
    """Every `<tg-emoji>` reduced to the character it wraps."""
    return TAG.sub(r"\1", html)
