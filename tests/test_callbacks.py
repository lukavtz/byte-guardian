from __future__ import annotations

import pytest

from app.services.callbacks import (
    PREFIX,
    Callback,
    cb_admin,
    cb_ask,
    cb_broadcast,
    cb_card,
    cb_cat,
    cb_chat,
    cb_chat_exit,
    cb_game,
    cb_lang,
    cb_menu,
    cb_quiz,
    cb_quiz_next,
    parse_cb,
)

SLUG = "fire-home"
LONG_SLUG = "a" * 32
SEED = "a3f1"


def test_prefix() -> None:
    assert PREFIX == "b1"


def test_round_trip_every_builder() -> None:
    cases = [
        (cb_menu(), Callback("menu")),
        (cb_admin(), Callback("admin")),
        (cb_cat(SLUG), Callback("cat", arg=SLUG, page=1)),
        (cb_cat(SLUG, 3), Callback("cat", arg=SLUG, page=3)),
        (cb_card(SLUG), Callback("card", arg=SLUG, page=1)),
        (cb_card(SLUG, 2), Callback("card", arg=SLUG, page=2)),
        (cb_game(SLUG), Callback("game", arg=SLUG)),
        (cb_quiz(SLUG, "q1", 2, SEED), Callback("quiz", arg=SLUG, extra=("q1", "2", SEED))),
        (cb_quiz_next(SLUG, "q1", SEED), Callback("quiz_next", arg=SLUG, extra=("q1", SEED))),
        (cb_lang("tt"), Callback("lang", arg="tt")),
        (cb_broadcast(True), Callback("bc", arg="yes")),
        (cb_broadcast(False), Callback("bc", arg="no")),
        (cb_ask(SLUG), Callback("ask", arg=SLUG)),
        (cb_chat(), Callback("chat")),
        (cb_chat_exit(), Callback("chat_exit")),
    ]
    for data, expected in cases:
        assert parse_cb(data) == expected, data


def test_quiz_parse_order() -> None:
    parsed = parse_cb("b1:quiz:fire-home:q1:2:a3f1")
    assert parsed is not None
    assert parsed.action == "quiz"
    assert parsed.arg == "fire-home"
    assert parsed.page == 1
    assert parsed.extra == ("q1", "2", "a3f1")

    nxt = parse_cb("b1:quiz_next:fire-home:q1:a3f1")
    assert nxt is not None
    assert nxt.action == "quiz_next"
    assert nxt.extra == ("q1", "a3f1")


def test_worst_case_slug_fits_64_bytes() -> None:
    data = [
        cb_menu(),
        cb_admin(),
        cb_cat(LONG_SLUG, 999),
        cb_card(LONG_SLUG, 999),
        cb_game(LONG_SLUG),
        cb_quiz(LONG_SLUG, "q1", 3, SEED),
        cb_quiz_next(LONG_SLUG, "q1", SEED),
        cb_lang("tt"),
        cb_broadcast(True),
        cb_broadcast(False),
        cb_ask(LONG_SLUG),
        cb_chat(),
        cb_chat_exit(),
    ]
    assert len(LONG_SLUG) == 32
    for payload in data:
        assert len(payload.encode("utf-8")) <= 64, payload
        assert parse_cb(payload) is not None, payload


@pytest.mark.parametrize(
    "data",
    [
        None,
        "",
        "b1",
        "b2:menu",
        "x1:menu",
        "b1:nope",
        "b1:menu:extra",
        "b1:admin:x",
        "b1:cat",
        "b1:cat::1",
        "b1:cat:fire-home:0",
        "b1:cat:fire-home:-1",
        "b1:cat:fire-home:abc",
        "b1:card:fire-home:1:2",
        "b1:lang",
        "b1:lang:",
        "b1:ask",
        "b2:game:fire-home",
        "b1:game",
        "b1:game:fire-home:1",
        "b1:bc:maybe",
        "b1:bc",
        "b1:bc:yes:no",
        "b1:chat:x",
        "b1:chat_exit:1",
        "b1:quiz:fire-home:q1:4:a3f1",
        "b1:quiz:fire-home:q1:1",
        "b1:quiz:fire-home:q1:1:zzzz",
        "b1:quiz::q1:1:a3f1",
        "b1:quiz_next:fire-home:q1",
        "b1:" + "a" * 62,
        "b1:card:" + "a" * 60,
    ],
)
def test_malformed_returns_none(data: str | None) -> None:
    assert parse_cb(data) is None


def test_game_parse() -> None:
    parsed = parse_cb("b1:game:fire-home")
    assert parsed == Callback("game", arg="fire-home")
    assert cb_game(SLUG) == "b1:game:fire-home"
    assert len(cb_game(LONG_SLUG).encode("utf-8")) <= 64


def test_page_defaults() -> None:
    assert cb_cat(SLUG) == "b1:cat:fire-home:1"
    assert cb_card(SLUG) == "b1:card:fire-home:1"
    parsed = parse_cb("b1:cat:fire-home")
    assert parsed is not None
    assert parsed.page == 1


@pytest.mark.parametrize(
    ("call", "message"),
    [
        (lambda: cb_cat(""), "slug"),
        (lambda: cb_cat(LONG_SLUG + "a"), "slug"),
        (lambda: cb_cat(SLUG, 0), "page"),
        (lambda: cb_card(SLUG, -2), "page"),
        (lambda: cb_quiz(SLUG, "q1", 4, SEED), "option"),
        (lambda: cb_quiz(SLUG, "q1", -1, SEED), "option"),
        (lambda: cb_quiz(SLUG, "q1", 1, "xyz"), "seed"),
        (lambda: cb_quiz(SLUG, "q1", 1, "a3f"), "seed"),
        (lambda: cb_quiz_next(SLUG, "q1", "ABCD"), "seed"),
        (lambda: cb_quiz(SLUG, "", 1, SEED), "qid"),
        (lambda: cb_lang(""), "lang"),
        (lambda: cb_ask(""), "slug"),
    ],
)
def test_builders_reject_bad_input(call: object, message: str) -> None:
    assert callable(call)
    with pytest.raises(ValueError, match=message):
        call()