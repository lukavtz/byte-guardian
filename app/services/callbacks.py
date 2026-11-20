"""Callback-data codec.

Wire format: ``b1:<action>:<args>``, at most 64 UTF-8 bytes (Telegram limit).
Screens are stateless: everything needed to redraw arrives in the callback.

    b1:menu
    b1:cat:<slug>:<page>
    b1:card:<slug>:<page>
    b1:game:<slug>
    b1:quiz:<slug>:<qid>:<option>:<seed>
    b1:quiz_next:<slug>:<qid>:<seed>
    b1:lang:<code>
    b1:lang_menu
    b1:search_hint
    b1:admin
    b1:bc:<yes|no>
    b1:ask:<slug>
"""

from __future__ import annotations

from dataclasses import dataclass

PREFIX = "b1"
MAX_LEN = 64
MAX_SLUG = 32
ACTIONS = frozenset(
    {
        "menu",
        "cat",
        "card",
        "game",
        "quiz",
        "quiz_next",
        "lang",
        "lang_menu",
        "search_hint",
        "admin",
        "bc",
        "ask",
    }
)
_BARE = frozenset({"menu", "admin", "lang_menu", "search_hint"})
_PAGES = frozenset({"cat", "card"})
_HEX = frozenset("0123456789abcdef")
_BOOL_ARGS = frozenset({"yes", "no"})


@dataclass(frozen=True, slots=True)
class Callback:
    action: str
    arg: str = ""
    page: int = 1
    extra: tuple[str, ...] = ()


def _pack(*parts: str) -> str:
    data = ":".join((PREFIX, *parts))
    if len(data.encode("utf-8")) > MAX_LEN:
        raise ValueError(f"callback data too long: {data!r}")
    return data


def _slug(slug: str) -> str:
    if not slug or ":" in slug or len(slug.encode("utf-8")) > MAX_SLUG:
        raise ValueError(f"bad slug: {slug!r}")
    return slug


def _qid(qid: str) -> str:
    if not qid or ":" in qid:
        raise ValueError(f"bad qid: {qid!r}")
    return qid


def _page(page: int) -> str:
    if isinstance(page, bool) or page < 1:
        raise ValueError(f"bad page: {page!r}")
    return str(page)


def _option(option: int) -> str:
    if isinstance(option, bool) or not 0 <= option <= 3:
        raise ValueError(f"bad option: {option!r}")
    return str(option)


def _seed(seed: str) -> str:
    if len(seed) != 4 or not set(seed) <= _HEX:
        raise ValueError(f"bad seed: {seed!r}")
    return seed


def _lang(code: str) -> str:
    if not code or ":" in code:
        raise ValueError(f"bad lang: {code!r}")
    return code


def cb_menu() -> str:
    return _pack("menu")


def cb_cat(slug: str, page: int = 1) -> str:
    return _pack("cat", _slug(slug), _page(page))


def cb_card(slug: str, page: int = 1) -> str:
    return _pack("card", _slug(slug), _page(page))


def cb_game(slug: str) -> str:
    return _pack("game", _slug(slug))


def cb_quiz(slug: str, qid: str, option: int, seed: str) -> str:
    return _pack("quiz", _slug(slug), _qid(qid), _option(option), _seed(seed))


def cb_quiz_next(slug: str, qid: str, seed: str) -> str:
    return _pack("quiz_next", _slug(slug), _qid(qid), _seed(seed))


def cb_lang(code: str) -> str:
    return _pack("lang", _lang(code))


def cb_lang_menu() -> str:
    return _pack("lang_menu")


def cb_search_hint() -> str:
    return _pack("search_hint")


def cb_admin() -> str:
    return _pack("admin")


def cb_broadcast(confirm: bool) -> str:
    return _pack("bc", "yes" if confirm else "no")


def cb_ask(slug: str) -> str:
    return _pack("ask", _slug(slug))


def _page_arg(raw: str) -> int | None:
    if not raw.isdigit():
        return None
    page = int(raw)
    return page if page >= 1 else None


def _is_seed(raw: str) -> bool:
    return len(raw) == 4 and set(raw) <= _HEX


def parse_cb(data: str | None) -> Callback | None:
    """Decode callback data; any malformed input yields ``None``."""
    if not isinstance(data, str) or not data or len(data.encode("utf-8")) > MAX_LEN:
        return None
    parts = data.split(":")
    if len(parts) < 2 or parts[0] != PREFIX or parts[1] not in ACTIONS:
        return None
    action, args = parts[1], parts[2:]

    if action in _BARE:
        return Callback(action) if not args else None

    if action in _PAGES:
        if len(args) not in (1, 2) or not args[0]:
            return None
        page = 1 if len(args) == 1 else _page_arg(args[1])
        if page is None:
            return None
        return Callback(action, arg=args[0], page=page)

    if action in ("lang", "ask", "game"):
        return Callback(action, arg=args[0]) if len(args) == 1 and args[0] else None

    if action == "bc":
        return Callback(action, arg=args[0]) if len(args) == 1 and args[0] in _BOOL_ARGS else None

    if action == "quiz":
        if len(args) != 4:
            return None
        slug, qid, option, seed = args
        if not slug or not qid or option not in {"0", "1", "2", "3"} or not _is_seed(seed):
            return None
        return Callback(action, arg=slug, extra=(qid, option, seed))

    if action != "quiz_next" or len(args) != 3:
        return None
    slug, qid, seed = args
    if not slug or not qid or not _is_seed(seed):
        return None
    return Callback(action, arg=slug, extra=(qid, seed))