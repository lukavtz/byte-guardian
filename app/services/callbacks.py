"""Callback-data codec.

Wire format: ``b1:<action>:<args>``, at most 64 UTF-8 bytes (Telegram limit).
Screens are stateless: everything needed to redraw arrives in the callback.

    b1:menu
    b1:cat:<slug>:<page>
    b1:card:<slug>:<page>
    b1:random
    b1:search:<hash>:<page>
    b1:game:<slug>
    b1:quiz:<slug>:<qid>:<option>:<seed>
    b1:quiz_next:<slug>:<qid>:<seed>
    b1:lang:<code>
    b1:lang_menu
    b1:search_hint
    b1:admin
    b1:admin:content
    b1:admin:cat:<slug>
    b1:admin:threats:<category>
    b1:admin:threat:<slug>:<locale>
    b1:admin:edit:<slug>:<locale>:<field>
    b1:admin:add:<category>
    b1:admin:cancel:<slug>:<locale>
    b1:admin:bc
    b1:admin:hint
    b1:bc:<yes|no>
    b1:bc_status
    b1:bc_status:<uuid>
    b1:ask:<slug>
    b1:chat
    b1:chat_exit
"""

from __future__ import annotations

from dataclasses import dataclass

from app.services.content_admin import EDIT_FIELDS as _EDIT_FIELDS
from app.services.content_admin import LOCALES as _LOC

PREFIX = "b1"
MAX_LEN = 64
MAX_SLUG = 32
ACTIONS = frozenset(
    {
        "menu",
        "cat",
        "card",
        "random",
        "search",
        "game",
        "quiz",
        "quiz_next",
        "lang",
        "lang_menu",
        "search_hint",
        "admin",
        "bc",
        "bc_status",
        "ask",
        "chat",
        "chat_exit",
    }
)
_BARE = frozenset(
    {"menu", "random", "lang_menu", "search_hint", "chat", "chat_exit"}
)
_ADMIN_BARE = frozenset({"bc", "hint"})
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


def _digest(raw: str) -> str:
    """An 8-hex sha1 prefix: the query itself never rides in callback data."""
    if len(raw) != 8 or not set(raw) <= _HEX:
        raise ValueError(f"bad query digest: {raw!r}")
    return raw


def _lang(code: str) -> str:
    if not code or ":" in code:
        raise ValueError(f"bad lang: {code!r}")
    return code


def _uuid(raw: str) -> str:
    if len(raw) != 36 or raw.count("-") != 4 or not set(raw) <= _HEX | {"-"}:
        raise ValueError(f"bad uuid: {raw!r}")
    return raw


def cb_menu() -> str:
    return _pack("menu")


def cb_cat(slug: str, page: int = 1) -> str:
    return _pack("cat", _slug(slug), _page(page))


def cb_card(slug: str, page: int = 1) -> str:
    return _pack("card", _slug(slug), _page(page))


def cb_random() -> str:
    """Draw the card at tap time: the slug is unknown when the row is built."""
    return _pack("random")


def cb_search(digest: str, page: int = 1) -> str:
    return _pack("search", _digest(digest), _page(page))


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


def cb_admin_category(slug: str) -> str:
    return _pack("admin", "cat", _slug(slug))


def cb_admin_content() -> str:
    return _pack("admin", "content")


def cb_admin_threats(category: str) -> str:
    return _pack("admin", "threats", _slug(category))


def _locale(code: str) -> str:
    if code not in _LOC:
        raise ValueError(f"bad locale: {code!r}")
    return code


def cb_admin_threat(slug: str, locale: str = "ru") -> str:
    return _pack("admin", "threat", _slug(slug), _locale(locale))


def cb_admin_edit(slug: str, locale: str, field: str) -> str:
    if field not in _EDIT_FIELDS:
        raise ValueError(f"bad field: {field!r}")
    return _pack("admin", "edit", _slug(slug), _locale(locale), field)


def cb_admin_add(category: str) -> str:
    return _pack("admin", "add", _slug(category))


def cb_admin_cancel(slug: str, locale: str = "ru") -> str:
    return _pack("admin", "cancel", _slug(slug), _locale(locale))


def cb_admin_toggle(slug: str, locale: str = "ru") -> str:
    return _pack("admin", "toggle", _slug(slug), _locale(locale))


def cb_admin_category_screen(slug: str) -> str:
    return _pack("admin", "ced", _slug(slug))


def cb_admin_category_edit(slug: str, field: str) -> str:
    if field not in {"title", "emoji"}:
        raise ValueError(f"bad category field: {field!r}")
    return _pack("admin", "cedit", _slug(slug), field)


def cb_admin_severity(slug: str, locale: str, level: int) -> str:
    if level not in (1, 2, 3):
        raise ValueError(f"bad severity: {level!r}")
    return _pack("admin", "sev", _slug(slug), _locale(locale), str(level))


def cb_admin_move(slug: str, locale: str, direction: str) -> str:
    if direction not in {"up", "down"}:
        raise ValueError(f"bad direction: {direction!r}")
    return _pack("admin", "move", _slug(slug), _locale(locale), direction)


def cb_admin_quiz(slug: str, locale: str = "ru") -> str:
    return _pack("admin", "quiz", _slug(slug), _locale(locale))


def _index(index: int) -> str:
    if isinstance(index, bool) or not 1 <= index <= 99:
        raise ValueError(f"bad question index: {index!r}")
    return str(index)


def cb_admin_question(slug: str, locale: str, index: int) -> str:
    return _pack("admin", "q", _slug(slug), _locale(locale), _index(index))


def cb_admin_quiz_edit(
    slug: str, locale: str, index: int, field: str
) -> str:
    if field not in ("p", "o0", "o1", "o2", "o3", "e"):
        raise ValueError(f"bad quiz field: {field!r}")
    return _pack("admin", "qe", _slug(slug), _locale(locale), _index(index), field)


def cb_admin_quiz_answer(
    slug: str, locale: str, index: int, answer: int
) -> str:
    if answer not in (0, 1, 2, 3):
        raise ValueError(f"bad answer: {answer!r}")
    return _pack("admin", "qa", _slug(slug), _locale(locale), _index(index), str(answer))


def cb_admin_broadcast() -> str:
    return _pack("admin", "bc")


def cb_admin_hint() -> str:
    return _pack("admin", "hint")


def cb_broadcast(confirm: bool) -> str:
    return _pack("bc", "yes" if confirm else "no")


def cb_broadcast_status(broadcast_id: str = "") -> str:
    """Open the progress card: the given broadcast, or the newest one."""
    return _pack("bc_status", _uuid(broadcast_id)) if broadcast_id else _pack("bc_status")


def cb_ask(slug: str) -> str:
    return _pack("ask", _slug(slug))


def cb_chat() -> str:
    return _pack("chat")


def cb_chat_exit() -> str:
    return _pack("chat_exit")


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

    if action == "admin":
        if not args:
            return Callback(action)
        if len(args) == 1 and args[0] in _ADMIN_BARE:
            return Callback(action, arg=args[0])
        if len(args) == 1 and args[0] == "content":
            return Callback(action, arg="content")
        if len(args) == 2 and args[0] in {"cat", "threats", "add", "ced"} and args[1]:
            return Callback(action, arg=args[1], extra=(args[0],))
        if len(args) == 3 and args[1] and args[2] in _LOC:
            if args[0] in {"threat", "cancel", "toggle", "quiz"}:
                return Callback(action, arg=args[1], extra=(args[0], args[2]))
        if len(args) == 3 and args[0] == "cedit" and args[1]:
            if args[2] not in {"title", "emoji"}:
                return None
            return Callback(action, arg=args[1], extra=("cedit", args[2]))
        if len(args) == 4 and args[1] and args[2] in _LOC:
            name, slug, locale, value = args
            if name == "edit" and value in _EDIT_FIELDS:
                return Callback(action, arg=slug, extra=("edit", locale, value))
            if name == "sev" and value in {"1", "2", "3"}:
                return Callback(action, arg=slug, extra=("sev", locale, value))
            if name == "move" and value in {"up", "down"}:
                return Callback(action, arg=slug, extra=("move", locale, value))
            if name == "q" and value.isdigit() and 1 <= int(value) <= 99:
                return Callback(action, arg=slug, extra=("q", locale, value))
        if len(args) == 5 and args[1] and args[2] in _LOC and args[3].isdigit():
            name, slug, locale, index, value = args
            if not 1 <= int(index) <= 99:
                return None
            if name == "qe" and value in {"p", "o0", "o1", "o2", "o3", "e"}:
                return Callback(action, arg=slug, extra=("qe", locale, index, value))
            if name == "qa" and value in {"0", "1", "2", "3"}:
                return Callback(action, arg=slug, extra=("qa", locale, index, value))
        return None

    if action == "search":
        if len(args) not in (1, 2) or len(args[0]) != 8 or not set(args[0]) <= _HEX:
            return None
        page = 1 if len(args) == 1 else _page_arg(args[1])
        return Callback(action, arg=args[0], page=page) if page is not None else None

    if action == "bc_status":
        if not args:
            return Callback(action)
        if len(args) == 1:
            try:
                return Callback(action, arg=_uuid(args[0]))
            except ValueError:
                return None
        return None

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