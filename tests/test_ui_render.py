from __future__ import annotations

import dataclasses

import pytest
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from app.content.types import CardView, CategoryView, QuizView, ThreatView
from app.services.callbacks import parse_cb
from app.ui import emoji as emoji_mod
from app.ui.emoji import emoji, plain_emoji, strip_premium
from app.ui.keyboards import PAGE_SIZE
from app.ui.render import (
    RichScreen,
    render_card,
    render_category,
    render_menu,
    render_question,
    render_result,
    search_screen,
)

LOCALE = "ru"


def _category(slug: str = "fire", title: str = "Пожар") -> CategoryView:
    return CategoryView(slug=slug, title=title, emoji="🔥", sort=1, threat_count=9)


def _threat(slug: str = "fire-home", severity: int = 3) -> ThreatView:
    return ThreatView(
        slug=slug,
        category_slug="fire",
        category_title="Пожар",
        title=f"Угроза {slug}",
        summary="Короткое описание.",
        severity=severity,
        locale="ru",
        fallback=False,
    )


def _card(fallback: bool = False) -> CardView:
    return CardView(
        slug="fire-home",
        category_slug="fire",
        category_title="Пожар",
        severity=3,
        locale="ru",
        fallback=fallback,
        title="Пожар в квартире",
        summary="Счёт идёт на минуты.",
        signs=("Дым из-под двери",),
        before=("Проверьте огнетушитель",),
        during=("Звоните 101",),
        after=("Выйдите на улицу",),
        forbidden=("Не открывайте окна",),
        contacts=("101", "112"),
        source="МЧС России",
        icon="🔥",
    )


def _quiz() -> QuizView:
    return QuizView(
        threat_slug="fire-home",
        question_id="q1",
        prompt="Что делать первым делом?",
        options=("Позвонить 101", "Открыть окна", "Спрятаться", "Ждать"),
        answer=0,
        explanation="Сначала вызов пожарных.",
    )


def _buttons(markup: InlineKeyboardMarkup) -> list[InlineKeyboardButton]:
    return [button for row in markup.inline_keyboard for button in row]


def _utf16_len(text: str) -> int:
    return len(text.encode("utf-16-le")) // 2


# --- emoji ---------------------------------------------------------------


def test_strip_premium_reduces_tags_to_their_character() -> None:
    html = 'a <tg-emoji emoji-id="1">🔥</tg-emoji> b'
    assert strip_premium(html) == "a 🔥 b"
    assert strip_premium('<tg-emoji emoji-id="1">🔥</tg-emoji><tg-emoji emoji-id="2">⚡</tg-emoji>') == "🔥⚡"
    assert strip_premium("plain") == "plain"


def test_emoji_falls_back_to_the_character(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(emoji_mod, "CUSTOM", {})
    assert emoji("fire", "🔥") == "🔥"


def test_emoji_uses_a_pinned_id(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(emoji_mod, "CUSTOM", {"fire": "5368324170671202286"})
    assert emoji("fire", "🔥") == '<tg-emoji emoji-id="5368324170671202286">🔥</tg-emoji>'
    assert strip_premium(emoji("fire", "🔥")) == "🔥"
    assert plain_emoji("fire", "🔥") == "🔥"


def test_pinned_premium_emoji_never_reaches_button_text(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(emoji_mod, "CUSTOM", {"fire": "1", "shield": "2", "quiz": "3", "done": "4", "search": "5"})
    screens = [
        render_menu([_category()], LOCALE),
        render_category(_category(), [_threat()], LOCALE, 1, 1),
        render_card(_card(), LOCALE, 1, True),
        render_question(_quiz(), "fire-home", 1, 3, "a3f1", LOCALE),
        render_result(2, 3, "fire-home", LOCALE),
        search_screen("пожар", [_threat()], LOCALE),
    ]
    for screen in screens:
        assert screen.keyboard is not None
        for button in _buttons(screen.keyboard):
            assert "<tg-emoji" not in button.text
    menu = screens[0]
    assert menu.keyboard is not None
    assert menu.keyboard.inline_keyboard[0][0].text.startswith("🔥")
    assert "<tg-emoji" in menu.html


def test_content_icon_is_escaped_in_heading() -> None:
    category = dataclasses.replace(_category(), emoji="<b>icon</b>")
    html = render_category(category, [_threat()], LOCALE, 1, 1).html
    assert "<b>icon</b>" not in html
    assert "&lt;b&gt;icon&lt;/b&gt;" in html

    card = dataclasses.replace(_card(), icon="<i>")
    assert "&lt;i&gt;" in render_card(card, LOCALE, 1, False).html


# --- menu ----------------------------------------------------------------


def test_menu_has_eight_category_buttons_two_per_row() -> None:
    slugs = ["fire", "terror", "tech", "nature", "medical", "chem", "rad", "cyber"]
    categories = [_category(slug=slug, title=slug.title()) for slug in slugs]
    screen = render_menu(categories, LOCALE)

    assert isinstance(screen, RichScreen)
    assert screen.keyboard is not None
    assert all(len(row) == 2 for row in screen.keyboard.inline_keyboard)
    assert len(screen.keyboard.inline_keyboard) == 4

    buttons = _buttons(screen.keyboard)
    assert len(buttons) == 8
    assert [parse_cb(button.callback_data).arg for button in buttons] == slugs
    assert all(parse_cb(button.callback_data).action == "cat" for button in buttons)


def test_menu_never_exceeds_eight_buttons() -> None:
    categories = [_category(slug=f"c{i}", title=f"Раздел {i}") for i in range(9)]
    screen = render_menu(categories, LOCALE)
    assert screen.keyboard is not None
    assert len(_buttons(screen.keyboard)) <= 8
    callbacks = [button.callback_data for button in _buttons(screen.keyboard)]
    assert "b1:cat:c8:1" not in callbacks


# --- category ------------------------------------------------------------


def test_category_lists_a_threat_and_paginates() -> None:
    threats = [_threat(slug=f"t{i}") for i in range(PAGE_SIZE)]
    screen = render_category(_category(), threats, LOCALE, page=2, pages=3)

    assert "Пожар" in screen.html
    assert screen.keyboard is not None
    buttons = _buttons(screen.keyboard)
    assert len(buttons) <= 8
    data = [button.callback_data for button in buttons if button.callback_data]
    parsed = [parse_cb(item) for item in data]
    assert any(cb is not None and cb.action == "cat" and cb.page == 1 for cb in parsed)
    assert any(cb is not None and cb.action == "cat" and cb.page == 3 for cb in parsed)
    assert any(cb is not None and cb.action == "card" and cb.page == 2 for cb in parsed)
    assert any(cb is not None and cb.action == "menu" for cb in parsed)


def test_category_never_exceeds_eight_buttons() -> None:
    threats = [_threat(slug=f"t{i}") for i in range(20)]
    screen = render_category(_category(), threats, LOCALE, page=2, pages=4)
    assert screen.keyboard is not None
    assert len(_buttons(screen.keyboard)) <= 8


# --- card ----------------------------------------------------------------


def test_card_uses_rich_markup() -> None:
    screen = render_card(_card(), LOCALE, page=1, has_quiz=True)
    html = screen.html

    for tag in ("<h1>", "<blockquote>", "<details>", "<footer>"):
        assert tag in html
    assert "●●●" in html
    assert "Высокая" in html
    assert "RU" not in html


def test_card_marks_ru_fallback() -> None:
    assert "RU" in render_card(_card(fallback=True), LOCALE, page=1, has_quiz=False).html


def test_card_keyboard_has_one_primary_and_a_quiz_button() -> None:
    screen = render_card(_card(), LOCALE, page=1, has_quiz=True)
    assert screen.keyboard is not None
    buttons = _buttons(screen.keyboard)
    assert len(buttons) <= 8
    primary = [button for button in buttons if button.style == "primary"]
    assert len(primary) == 1
    assert primary[0].callback_data is not None
    assert parse_cb(primary[0].callback_data).action == "quiz_next"

    no_quiz = render_card(_card(), LOCALE, page=1, has_quiz=False)
    assert no_quiz.keyboard is not None
    assert not [button for button in _buttons(no_quiz.keyboard) if button.style == "primary"]


def test_card_back_keeps_the_category_page() -> None:
    screen = render_card(_card(), LOCALE, page=3, has_quiz=False)
    assert screen.keyboard is not None
    back = [button for row in screen.keyboard.inline_keyboard for button in row if button.callback_data]
    pages = [parse_cb(button.callback_data).page for button in back if parse_cb(button.callback_data).action == "cat"]
    assert pages == [3]


def test_card_escapes_content() -> None:
    card = dataclasses.replace(_card(), title="<script>", signs=("a & b",))
    screen = render_card(card, LOCALE, page=1, has_quiz=False)
    assert "<script>" not in screen.html
    assert "&lt;script&gt;" in screen.html
    assert "a &amp; b" in screen.html


def test_card_is_within_the_rich_limit() -> None:
    fat = dataclasses.replace(
        _card(),
        summary="я" * 5000,
        signs=tuple("я" * 1000 for _ in range(50)),
        before=tuple("я" * 1000 for _ in range(50)),
        during=tuple("я" * 1000 for _ in range(50)),
        after=tuple("я" * 1000 for _ in range(50)),
        forbidden=tuple("я" * 1000 for _ in range(50)),
        contacts=tuple("я" * 1000 for _ in range(50)),
    )
    assert _utf16_len(render_card(fat, LOCALE, page=1, has_quiz=True).html) <= 32000


# --- quiz ----------------------------------------------------------------


def test_question_offers_four_options_with_seeded_callbacks() -> None:
    screen = render_question(_quiz(), "fire-home", index=1, total=3, seed="a3f1", locale=LOCALE)
    assert screen.keyboard is not None
    buttons = [button for button in _buttons(screen.keyboard) if button.callback_data]
    answers = [parse_cb(button.callback_data) for button in buttons]
    answers = [cb for cb in answers if cb is not None and cb.action == "quiz"]
    assert len(answers) == 4
    assert [cb.extra for cb in answers] == [
        ("q1", "0", "a3f1"),
        ("q1", "1", "a3f1"),
        ("q1", "2", "a3f1"),
        ("q1", "3", "a3f1"),
    ]
    assert "1/3" in screen.html


def test_result_shows_score_and_retry() -> None:
    screen = render_result(correct=2, total=3, slug="fire-home", locale=LOCALE)
    assert "2/3" in screen.html
    assert screen.keyboard is not None
    buttons = _buttons(screen.keyboard)
    assert len(buttons) <= 8
    primary = [button for button in buttons if button.style == "primary"]
    assert len(primary) == 1
    parsed = parse_cb(primary[0].callback_data)
    assert parsed is not None and parsed.action == "quiz_next" and parsed.arg == "fire-home"


# --- search --------------------------------------------------------------


def test_search_lists_results() -> None:
    screen = search_screen("пожар", [_threat(slug=f"t{i}") for i in range(3)], LOCALE)
    assert screen.keyboard is not None
    buttons = _buttons(screen.keyboard)
    assert len(buttons) <= 8
    cards = [parse_cb(button.callback_data) for button in buttons if button.callback_data]
    assert sum(1 for cb in cards if cb and cb.action == "card") == 3


def test_search_empty_state_explains_itself() -> None:
    screen = search_screen("", [], LOCALE)
    assert "Поиск" in screen.html
    assert screen.keyboard is not None
    assert len(_buttons(screen.keyboard)) <= 8


def test_search_truncates_to_the_keyboard_limit() -> None:
    screen = search_screen("x", [_threat(slug=f"t{i}") for i in range(50)], LOCALE)
    assert screen.keyboard is not None
    assert len(_buttons(screen.keyboard)) <= 8


# --- noise ---------------------------------------------------------------


@pytest.mark.parametrize(
    "screen",
    [
        render_menu([_category()], LOCALE),
        render_category(_category(), [_threat()], LOCALE, 1, 1),
        render_card(_card(), LOCALE, 1, True),
        render_question(_quiz(), "fire-home", 1, 3, "a3f1", LOCALE),
        render_result(3, 3, "fire-home", LOCALE),
        search_screen("пожар", [_threat()], LOCALE),
    ],
)
def test_screens_are_clean_utf8_without_bom(screen: RichScreen) -> None:
    assert not screen.html.startswith("\ufeff")
    assert "\ufffd" not in screen.html
    screen.html.encode("utf-8").decode("utf-8")
    assert _utf16_len(screen.html) <= 32000
    if screen.keyboard is not None:
        assert len(_buttons(screen.keyboard)) <= 8
        assert sum(1 for button in _buttons(screen.keyboard) if button.style == "primary") <= 1
