"""Custom emoji: a name becomes a `<tg-emoji>` tag, or the plain character.

Every default id comes from one pack, `PACK` (`TgAndroidIcons`, 515 stickers),
so a screen never mixes drawing styles. The pack is read live with
`getStickerSet`; `PACK_IDS` is the vendored char -> id map, and `DEFAULT_IDS`
is the semantic name layer on top of it.

A few semantic names have no exact glyph in the pack. They borrow the closest
one from the same pack; the comment on each says what was borrowed. No id from
another pack is ever installed.

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

PACK = "TgAndroidIcons"

# char -> custom emoji id, vendored from `getStickerSet?name=TgAndroidIcons`.
PACK_IDS: dict[str, str] = {
    "🔎": "5874960879434338403",
    "📂": "6017174676898321263",
    "🌐": "5879585266426973039",
    "💬": "5886666250158870040",
    "🔥": "6008118472066732010",
    "🆕": "5886306834410640699",
    "🛠": "5988023995125993550",
    "🏠": "5967822972931542886",
    "📦": "5924720918826848520",
    "⬇️": "5886451926995833684",
    "❗️": "5879813604068298387",
    "⏲": "5877613700344450910",
    "🔒": "5832546462478635761",
    "📢": "5771695636411847302",
    "🔄": "5839200986022812209",
    "✅": "5776375003280838798",
    "🔗": "5877465816030515018",
    "📎": "5877495434124988415",
    "🏷": "5854776233950188167",
    "📖": "5897850551156084824",
    "🐻": "5967800016331346187",
    "👁": "5960714428394507968",
    "📊": "5877485980901971030",
    "❤️": "5992453811510186287",
    "🗑": "5879896690210639947",
    "❌": "5778527486270770928",
    "🏡": "6010362983320916413",
    "🔽": "5875008416132370818",
    "📝": "5886330010054168711",
    "🔔": "5909201569898827582",
    "🔕": "5909123362839335003",
    "👥": "5915556996215476302",
    "👤": "5771887475421090729",
    "📤": "5877540355187937244",
    "🛡": "5926783847453692661",
    "🎲": "5960608239623082921",
    "💊": "5933768993285345899",
    "🧪": "5897607722295103204",
    "⚠️": "5881702736843511327",
    "⚡️": "5843553939672274145",
    "🕓": "5776213190387961618",
    "⚙": "5877260593903177342",
}

# Semantic name -> pack character. The right-hand character is also the
# fallback the client without custom emoji support shows.
ICONS: dict[str, str] = {
    "find": "🔎",
    "search": "🔎",
    "catalog": "📂",
    "site": "🌐",
    "lang": "🌐",
    "globe": "🌐",
    "support": "💬",
    "help": "💬",
    "chat": "💬",
    "hot": "🔥",
    "fire": "🔥",
    "new": "🆕",
    "panel": "🛠",
    "tech": "🛠",
    "admin": "🛠",
    "tools": "🛠",
    "back": "🏠",
    "home": "🏠",
    "package": "📦",
    "download": "⬇️",
    "arrow": "⬇️",
    "error": "❗️",
    "hourglass": "⏲",
    "lock": "🔒",
    "cyber": "🔒",
    "channel": "📢",
    "broadcast": "📢",
    "publish": "📢",
    "megaphone": "📢",
    "refresh": "🔄",
    "done": "✅",
    "test": "✅",
    "check": "✅",
    "link": "🔗",
    "links": "📎",
    "category": "🏷",
    "description": "📖",
    "duck": "🐻",  # no waterfowl in the pack; closest mascot animal
    "eyes": "👁",
    "preview": "👁",
    "stats": "📊",
    "health": "❤️",
    "trash": "🗑",
    "cancel": "❌",
    "menu": "🏡",
    "top": "🔽",
    "digest": "📝",
    "bell": "🔔",
    "mute": "🔕",
    "people": "👥",
    "user": "👤",
    "send": "📤",
    "shield": "🛡",
    "terror": "🛡",
    "game": "🎲",
    "medical": "💊",  # no stethoscope; pill is the closest health glyph
    "first-aid": "💊",
    "chem": "🧪",  # no biohazard; test tube is the closest lab glyph
    "hazard": "⚠️",
    "rad": "⚠️",  # no radiation trefoil; hazard triangle carries the warning
    "radiation": "⚠️",
    "warning": "⚠️",
    "nature": "⚡️",  # no tornado; storm bolt is the closest weather glyph
    "storm": "⚡️",
    "clock": "🕓",
}

# name -> custom emoji id, resolved through the one pack.
DEFAULT_IDS: dict[str, str] = {name: PACK_IDS[char] for name, char in ICONS.items()}

# What each name draws. The alternative text is what a client without custom
# emoji support shows, so it has to be the character the reader expects.
FALLBACKS: dict[str, str] = dict(ICONS)


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
