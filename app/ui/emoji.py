"""Custom emoji: a name becomes a `<tg-emoji>` tag, or the plain character.

The vetted ids come from the MIT-licensed catalogue
https://github.com/abbosahmad/telegram-premium-emojis (packs TgAndroidIcons,
vector_icons_by_fStikBot, tgmacicons, NewsEmoji). Every id was checked against
a live bot: `sendMessage` with a `custom_emoji` entity keeps the entity, so
Telegram accepts it for this bot.

`TELEGRAM_EMOJI_IDS` as `name:id,name:id` overrides any name here, including an
empty `name:`. Without an id the character is used, so every screen is complete
before the owner buys the pack. `strip_premium` is the degradation step:
Telegram refuses the tag, the screen keeps its text.
"""

from __future__ import annotations

import os
import re

TAG = re.compile(r"<tg-emoji\b[^>]*>(.*?)</tg-emoji>", re.DOTALL)
ATTRIBUTE = re.compile(r'emoji-id="(\d+)"')

# name -> custom emoji id, vendored from the catalogue above.
DEFAULT_IDS: dict[str, str] = {
    "find": "5258274739041883702",
    "catalog": "6017174676898321263",
    "site": "5879585266426973039",
    "support": "5886666250158870040",
    "hot": "6008118472066732010",
    "new": "5886306834410640699",
    "panel": "6021401276904905698",
    "back": "5967822972931542886",
    "package": "5924720918826848520",
    "download": "5886451926995833684",
    "error": "5220197908342648622",
    "hourglass": "5258113901106580375",
    "lock": "5832546462478635761",
    "channel": "5260268501515377807",
    "refresh": "5292226786229236118",
    "done": "5219899949281453881",
    "link": "5877465816030515018",
    "category": "5854776233950188167",
    "description": "5897850551156084824",
    "duck": "5400233063261151534",
    "arrow": "5875008416132370818",
    "eyes": "5260341314095947411",
    "help": "5260535596941582167",
    "stats": "5226513232549664618",
    "health": "6023704302793528745",
    "publish": "5260268501515377807",
    "links": "6021788373717358680",
    "trash": "5258130763148172425",
    "cancel": "5258226313285607065",
    "menu": "5257963315258204021",
    "top": "5258336354642697821",
    "digest": "5257965174979042426",
    "broadcast": "5258500422393415126",
    "bell": "6021536113108196448",
    "mute": "6021440013214948027",
    "people": "5915556996215476302",
    "preview": "5960714428394507968",
    "send": "5877540355187937244",
    # Bot aliases: the screen names map onto a vetted id. The fallback is the
    # character the reader expects in that slot, not the source pack's glyph.
    "fire": "6008118472066732010",  # hot
    "terror": "5832546462478635761",  # lock
    "tech": "6021401276904905698",  # panel
    "nature": "5292226786229236118",  # refresh
    "medical": "6023704302793528745",  # health
    "chem": "5897850551156084824",  # description
    "rad": "5220197908342648622",  # error
    "cyber": "5832546462478635761",  # lock
    "search": "5258274739041883702",  # find
    "game": "5258336354642697821",  # top
    "test": "5219899949281453881",  # done
    "lang": "5879585266426973039",  # site
    "admin": "6021401276904905698",  # panel
}

# What each name draws. The alternative text is what a client without custom
# emoji support shows, so it has to be the character the reader expects.
FALLBACKS: dict[str, str] = {
    "find": "🔍",
    "catalog": "📂",
    "site": "🌐",
    "support": "💬",
    "hot": "🔥",
    "new": "🆕",
    "panel": "🛠",
    "back": "🏠",
    "package": "📦",
    "download": "⬇️",
    "error": "❗",
    "hourglass": "⏳",
    "lock": "🔒",
    "channel": "📣",
    "refresh": "🔄",
    "done": "✅",
    "link": "🔗",
    "category": "🏷",
    "description": "📖",
    "duck": "🦆",
    "arrow": "🔽",
    "eyes": "👀",
    "help": "💬",
    "stats": "🔢",
    "health": "💓",
    "publish": "📣",
    "links": "📎",
    "trash": "🗑",
    "cancel": "❌",
    "menu": "🏘",
    "top": "⬇️",
    "digest": "📝",
    "broadcast": "🎤",
    "bell": "🔔",
    "mute": "🔕",
    "people": "👥",
    "preview": "👁",
    "send": "📤",
    "fire": "🔥",
    "terror": "🔒",
    "tech": "🛠",
    "nature": "🌪",
    "medical": "🩺",
    "chem": "☣",
    "rad": "☢",
    "cyber": "🔐",
    "search": "🔍",
    "game": "🎮",
    "test": "✅",
    "lang": "🌐",
    "admin": "🛠",
}


def _parse(raw: str) -> dict[str, str]:
    """`name:id,name:id` -> `{name: id}`; a bad id is ignored, not guessed."""
    ids: dict[str, str] = {}
    for token in raw.split(","):
        name, separator, custom_id = token.partition(":")
        name, custom_id = name.strip(), custom_id.strip()
        if separator and name and custom_id.isdigit():
            ids[name] = custom_id
    return ids


# Defaults first, then the environment: a restart applies a new .env.
CUSTOM: dict[str, str] = {**DEFAULT_IDS, **_parse(os.environ.get("TELEGRAM_EMOJI_IDS", ""))}


def custom_id(name: str) -> str | None:
    """The pinned id for `name`, for inline keyboard buttons."""
    return CUSTOM.get(name)


def premium(name: str, fallback: str | None = None) -> str:
    """The pinned `<tg-emoji>` for `name`, or the plain character."""
    character = fallback if fallback is not None else FALLBACKS.get(name, "")
    custom = CUSTOM.get(name)
    if not custom:
        return character
    return f'<tg-emoji emoji-id="{custom}">{character}</tg-emoji>'


def plain_emoji(name: str, fallback: str | None = None) -> str:
    """The character alone, for places Telegram never parses markup."""
    return strip_premium(premium(name, fallback))


def strip_premium(html: str) -> str:
    """Every `<tg-emoji>` reduced to the character it wraps."""
    return TAG.sub(r"\1", html)


def first_id(html: str) -> str | None:
    """The id of the first `<tg-emoji>` in `html`, for keyboard fallbacks."""
    match = ATTRIBUTE.search(html)
    return match.group(1) if match else None
