from __future__ import annotations

import dataclasses
import re
from html import unescape
from pathlib import Path

import pytest

from app.content.types import CardView, CategoryView, QuizView, ThreatView
from app.services.callbacks import parse_cb
from app.ui import emoji as emoji_mod
from app.ui.emoji import DEFAULT_IDS, plain_emoji, premium, strip_premium
from app.ui.keyboards import PAGE_SIZE, keyboard_from_rich, strip_rich_buttons
from app.ui.render import (
    ART,
    CARD_CAP,
    RichScreen,
    render_card,
    render_category,
    render_chat,
    render_menu,
    render_question,
    render_result,
    search_screen,
)

LOCALE = "ru"
BUTTON = re.compile(r"<tg-button(?=[\s>])([^>]*)>(.*?)</tg-button>", re.DOTALL)
ROW = re.compile(r"<tg-button-row\b[^>]*>(.*?)</tg-button-row>", re.DOTALL)
ATTRIBUTE = re.compile(r'([\w-]+)="([^"]*)"')


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


def _card(fallback: bool = False, category_slug: str = "fire") -> CardView:
    return CardView(
        slug="fire-home",
        category_slug=category_slug,
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
        explanation="Сначала вызов пожарной охраны.",
    )


def _buttons(html: str) -> list[dict[str, str]]:
    """Every `<tg-button>` as `{"attrs": raw, **attributes, "label": text}`."""
    found = []
    for attributes, label in BUTTON.findall(html):
        text = unescape(re.sub(r"<[^>]+>", "", label)).strip()
        found.append({"attrs": attributes, **dict(ATTRIBUTE.findall(attributes)), "label": text})
    return found


def _data(html: str) -> list[str]:
    return [button["data"] for button in _buttons(html) if "data" in button]


def _parsed(html: str) -> list:
    return [parse_cb(data) for data in _data(html)]


def _rows(html: str) -> list[list[dict[str, str]]]:
    return [_buttons(row) for row in ROW.findall(html)]


def _primary(html: str) -> list[dict[str, str]]:
    return [button for button in _buttons(html) if button.get("style") == "primary"]


def _utf16_len(text: str) -> int:
    return len(text.encode("utf-16-le")) // 2


def _screens() -> list[RichScreen]:
    return [
        render_menu([_category()], LOCALE),
        render_chat(LOCALE),
        render_category(_category(), [_threat()], LOCALE, 1, 1),
        render_card(_card(), LOCALE, 1, True),
        render_card(_card(), LOCALE, 1, True, can_game=True),
        render_question(_quiz(), "fire-home", 1, 3, "a3f1", LOCALE),
        render_result(2, 3, "fire-home", LOCALE),
        search_screen("пожар", [_threat()], LOCALE),
    ]


# --- emoji ---------------------------------------------------------------


def test_strip_premium_reduces_tags_to_their_character() -> None:
    html = 'a <tg-emoji emoji-id="1">🔥</tg-emoji> b'
    assert strip_premium(html) == "a 🔥 b"
    assert strip_premium('<tg-emoji emoji-id="1">🔥</tg-emoji><tg-emoji emoji-id="2">⚡</tg-emoji>') == "🔥⚡"
    assert strip_premium("plain") == "plain"


def test_emoji_falls_back_to_the_character(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(emoji_mod, "CUSTOM", {})
    assert premium("fire", "🔥") == "🔥"


def test_emoji_uses_a_pinned_id(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(emoji_mod, "CUSTOM", {"fire": "5368324170671202286"})
    assert premium("fire", "🔥") == '<tg-emoji emoji-id="5368324170671202286">🔥</tg-emoji>'
    assert strip_premium(premium("fire", "🔥")) == "🔥"
    assert plain_emoji("fire", "🔥") == "🔥"


def test_vetted_defaults_are_installed() -> None:
    assert DEFAULT_IDS["search"] == "5258274739041883702"
    assert DEFAULT_IDS["test"] == "5219899949281453881"
    assert DEFAULT_IDS["admin"] == "6021401276904905698"


def test_every_screen_carries_premium_tags_when_ids_configured(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(emoji_mod, "CUSTOM", dict(DEFAULT_IDS))
    for screen in _screens():
        assert "<tg-emoji emoji-id=" in screen.html
        for button in _buttons(screen.html):
            assert button["label"] and "<tg-emoji" not in button["label"]


def test_pinned_premium_emoji_stays_inside_the_button(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(emoji_mod, "CUSTOM", dict(DEFAULT_IDS))
    for screen in _screens():
        assert "<tg-emoji" in screen.html
        for button in _buttons(screen.html):
            assert button["attrs"].count("data=") + button["attrs"].count("query=") == 1


def test_content_icon_is_escaped_in_heading() -> None:
    category = dataclasses.replace(_category(), emoji="<b>icon</b>")
    html = render_category(category, [_threat()], LOCALE, 1, 1).html
    assert "<b>icon</b>" not in html
    assert "&lt;b&gt;icon&lt;/b&gt;" in html

    card = dataclasses.replace(_card(), icon="<i>")
    assert "&lt;i&gt;" in render_card(card, LOCALE, 1, False).html


# --- shape every screen shares ------------------------------------------


def test_no_screen_carries_a_reply_markup() -> None:
    for screen in _screens():
        assert screen.keyboard is None


def test_every_screen_draws_its_buttons_inside_the_message() -> None:
    for screen in _screens():
        assert "<tg-button-row>" in screen.html
        assert _buttons(screen.html)
        assert all(data.startswith("b1:") for data in _data(screen.html))


def test_screens_have_at_most_one_primary_button() -> None:
    for screen in _screens():
        assert len(_primary(screen.html)) <= 1


def test_screens_have_one_artwork_image_at_most() -> None:
    for screen in _screens():
        assert screen.html.count("<img ") <= 1


def test_screens_fit_the_rich_message_limit() -> None:
    for screen in _screens():
        assert _utf16_len(screen.html) <= 32000


def test_every_screen_has_one_h1() -> None:
    for screen in _screens():
        assert len(re.findall(r"<h1>", screen.html)) == 1


def test_card_shows_every_field_without_ellipsis() -> None:
    card = dataclasses.replace(
        _card(),
        summary="Короткое описание. " * 12,
        signs=tuple(f"Признак {i}" for i in range(6)),
        before=tuple(f"До {i}" for i in range(6)),
        during=tuple(f"Во время {i}" for i in range(6)),
        after=tuple(f"После {i}" for i in range(6)),
        forbidden=tuple(f"Нельзя {i}" for i in range(6)),
        contacts=("101", "112", "8 800 000 00 00"),
    )
    html = render_card(card, LOCALE, 1, True).html
    assert "…" not in html
    for item in (
        card.summary,
        *card.signs,
        *card.before,
        *card.during,
        *card.after,
        *card.forbidden,
        *card.contacts,
        card.source,
    ):
        assert item in html


def test_card_layout_separates_the_summary_signs_and_footer() -> None:
    html = render_card(_card(), LOCALE, 1, True).html
    assert html.count("<h1>") == 1
    assert "<p>●●● Высокая · Пожар</p>" in html
    assert html.count("<br/>") >= 1
    assert "</p><br/>" in html
    assert "Признаки<br/>• Дым из-под двери" in html
    assert len(re.findall(r"<summary><h2>.*?</h2></summary>", html)) == 4
    footer = re.search(r"<footer>(.*?)</footer>", html)
    assert footer is not None
    assert strip_premium(footer.group(1)).split(" · ")[1:] == ["ТИСБИ", "101", "112", "МЧС России"]


def test_oversized_card_drops_whole_items_only() -> None:
    long = "я" * 160
    fat = dataclasses.replace(
        _card(),
        summary="я" * 300,
        signs=tuple(long for _ in range(60)),
        before=tuple(long for _ in range(60)),
        during=tuple(long for _ in range(60)),
        after=tuple(long for _ in range(60)),
        forbidden=tuple(long for _ in range(60)),
    )
    html = render_card(fat, LOCALE, 1, True).html
    assert _utf16_len(html) <= CARD_CAP
    assert "…" in html
    bullets = re.findall(r"• ([^<]*)", html)
    assert bullets and len(bullets) < 300
    assert all(item.removesuffix(" …") == long for item in bullets)


def test_card_footer_carries_the_brand_line() -> None:
    html = render_card(_card(), LOCALE, page=1, has_quiz=False).html
    footer = re.search(r"<footer>(.*?)</footer>", html)
    assert footer is not None
    assert strip_premium(footer.group(1)) == "🦆 Байт · ТИСБИ · 101 · 112 · МЧС России"


def test_screens_stay_on_the_brand_palette() -> None:
    for screen in _screens():
        lowered = screen.html.lower()
        assert "teal" not in lowered
        assert "purple" not in lowered
        assert "cyan" not in lowered


def test_action_buttons_are_verbs_without_trailing_dots() -> None:
    labels = {
        re.sub(r"^[^\w]+", "", button["label"])
        for screen in _screens()
        for button in _buttons(screen.html)
    }
    assert {"Тест", "Играть", "Назад", "Поделиться", "Меню"} <= labels
    assert not any(label.endswith(".") for label in labels)

    answered = render_question(_quiz(), "fire-home", 1, 3, "a3f1", LOCALE, chosen=0)
    answered_labels = {re.sub(r"^[^\w]+", "", b["label"]) for b in _buttons(answered.html)}
    result_labels = {
        re.sub(r"^[^\w]+", "", b["label"])
        for b in _buttons(render_result(2, 3, "fire-home", LOCALE).html)
    }
    assert "Дальше" in answered_labels
    assert "Ещё раз" in result_labels


# --- artwork -------------------------------------------------------------


@pytest.mark.parametrize("slug", ["fire", "terror", "tech", "nature", "medical", "chem", "rad", "cyber"])
def test_category_artwork_matches_the_section(slug: str) -> None:
    screen = render_category(_category(slug=slug), [_threat()], LOCALE, 1, 1)
    assert f'<img src="{ART}/{slug}.png"/>' in screen.html


def test_menu_is_text_only() -> None:
    html = render_menu([_category()], LOCALE).html
    assert "<img" not in html
    assert "menu.png" not in html


def test_chat_uses_its_own_artwork() -> None:
    assert f'<img src="{ART}/chat.png"/>' in render_chat(LOCALE).html


def test_no_source_references_the_removed_banner() -> None:
    root = Path(__file__).resolve().parents[1]
    banned = "banner" + ".jpg"
    files = [root / "README.md", *root.joinpath("app").rglob("*.py"), *root.joinpath("tests").rglob("*.py")]
    files += list(root.joinpath("docs").rglob("*.md"))
    offenders = [path.relative_to(root).as_posix() for path in files if banned in path.read_text(encoding="utf-8")]
    assert not offenders


def test_unknown_section_falls_back_to_the_hero() -> None:
    assert f'<img src="{ART}/hero.png"/>' in search_screen("x", [], LOCALE).html
    unknown = render_category(_category(slug="space"), [_threat()], LOCALE, 1, 1)
    assert f'<img src="{ART}/hero.png"/>' in unknown.html


def test_card_and_quiz_use_the_category_artwork() -> None:
    card = render_card(_card(category_slug="chem"), LOCALE, 1, True)
    assert f'<img src="{ART}/chem.png"/>' in card.html
    question = render_question(_quiz(), "rad-smoke", 1, 3, "a3f1", LOCALE)
    assert f'<img src="{ART}/rad.png"/>' in question.html
    result = render_result(3, 3, "medical-aid", LOCALE)
    assert f'<img src="{ART}/medical.png"/>' in result.html


# --- menu ----------------------------------------------------------------


def test_menu_has_one_button_per_category_and_the_chat_row() -> None:
    slugs = ["fire", "terror", "tech", "nature", "medical", "chem", "rad", "cyber"]
    categories = [_category(slug=slug, title=slug.title()) for slug in slugs]
    screen = render_menu(categories, LOCALE)

    rows = _rows(screen.html)
    assert [len(row) for row in rows] == [1] * 8 + [1, 2]
    categories_only = _parsed(screen.html)
    assert [cb.arg for cb in categories_only[:8]] == slugs
    assert all(cb.action == "cat" for cb in categories_only[:8])
    assert [cb.action for cb in categories_only[8:]] == ["chat", "search_hint", "lang_menu"]


def test_menu_shows_the_shared_header_and_brand() -> None:
    html = render_menu([_category()], LOCALE).html
    headings = re.findall(r"<h1>(.*?)</h1>", html)
    assert len(headings) == 1 and headings[0].endswith("Байт")
    assert "Помощник по безопасности жизнедеятельности ТИСБИ" in html
    assert html.index("<h1>") < html.index("<hr/>")


def test_menu_chat_is_the_only_primary() -> None:
    screen = render_menu([_category()], LOCALE)
    primary = _primary(screen.html)
    assert len(primary) == 1
    assert parse_cb(primary[0]["data"]).action == "chat"
    search = [button for button in _buttons(screen.html) if button.get("data") == "b1:search_hint"]
    assert search and "style" not in search[0]


def test_admin_row_is_hidden_from_everyone_else() -> None:
    screen = render_menu([_category()], LOCALE)
    assert "b1:admin" not in _data(screen.html)
    assert not any(button["label"] == "Админ-панель" for button in _buttons(screen.html))


def test_admin_row_is_appended_last_for_an_admin() -> None:
    screen = render_menu([_category()], LOCALE, is_admin=True)
    rows = _rows(screen.html)
    assert len(rows[-1]) == 1
    button = rows[-1][0]
    assert button["data"] == "b1:admin"
    assert button["label"].endswith("Админ-панель")
    assert "style" not in button


def test_exactly_one_primary_where_a_screen_has_one() -> None:
    menu = render_menu([_category()], LOCALE)
    card = render_card(_card(), LOCALE, page=1, has_quiz=True)
    answered = render_question(_quiz(), "fire-home", 1, 3, "a3f1", LOCALE, chosen=0)
    result = render_result(1, 3, "fire-home", LOCALE)
    for screen in (menu, card, answered, result):
        assert len(_primary(screen.html)) == 1
    for screen in (
        render_category(_category(), [_threat()], LOCALE, 1, 1),
        render_question(_quiz(), "fire-home", 1, 3, "a3f1", LOCALE),
        search_screen("пожар", [_threat()], LOCALE),
    ):
        assert not _primary(screen.html)


def test_card_emphasis_marks_test_primary_and_game_success() -> None:
    html = render_card(_card(), LOCALE, page=1, has_quiz=True, can_game=True).html
    styles = [
        (button["label"].split(" ", 1)[-1], button["style"]) for button in _emphasized(html)
    ]
    assert styles == [("Тест", "primary"), ("Играть", "success")]


def test_card_without_widgets_keeps_one_primary_test_button() -> None:
    default = render_card(_card(), LOCALE, page=1, has_quiz=True)
    off = render_card(_card(), LOCALE, page=1, has_quiz=True, can_game=False)

    for screen in (default, off):
        assert "b1:game:fire-home" not in screen.html
        primary = _primary(screen.html)
        assert len(primary) == 1
        assert "Тест" in primary[0]["label"]


def test_inline_keyboard_fallback_keeps_plain_text_and_the_id() -> None:
    screen = render_card(_card(), LOCALE, page=1, has_quiz=True)
    markup = keyboard_from_rich(screen.html)
    assert markup is not None
    for row in markup.inline_keyboard:
        for item in row:
            assert "<tg-emoji" not in item.text
            assert item.icon_custom_emoji_id is not None


def test_menu_never_exceeds_eight_categories() -> None:
    categories = [_category(slug=f"c{i}", title=f"Раздел {i}") for i in range(9)]
    screen = render_menu(categories, LOCALE)
    assert "b1:cat:c8:1" not in _data(screen.html)
    assert len(_data(screen.html)) == 11


def test_chat_screen_carries_the_heading_lead_and_two_ways_out() -> None:
    screen = render_chat(LOCALE)
    assert "<h1>" in screen.html and "Чат с Байтом" in screen.html
    assert "Напиши вопрос по БЖД — отвечу по карточкам. Выйти — кнопка ниже." in screen.html
    rows = _rows(screen.html)
    assert len(rows) == 1
    assert [button["data"] for button in rows[0]] == ["b1:chat_exit", "b1:menu"]
    assert all("style" not in button for button in rows[0])
    assert [button["label"].split(" ", 1)[-1] for button in rows[0]] == ["Выйти", "Меню"]


# --- category ------------------------------------------------------------


def test_category_lists_threats_with_severity_dots_and_pages() -> None:
    threats = [_threat(slug=f"t{i}") for i in range(PAGE_SIZE)]
    screen = render_category(_category(), threats, LOCALE, page=2, pages=3)
    parsed = _parsed(screen.html)

    assert "Пожар" in screen.html
    assert any(cb is not None and cb.action == "cat" and cb.page == 1 for cb in parsed)
    assert any(cb is not None and cb.action == "cat" and cb.page == 3 for cb in parsed)
    assert any(cb is not None and cb.action == "card" and cb.page == 2 for cb in parsed)
    assert any(cb is not None and cb.action == "menu" for cb in parsed)
    assert "●●●" in screen.html
    assert [len(row) for row in _rows(screen.html)][:PAGE_SIZE] == [1] * PAGE_SIZE


def test_category_never_exceeds_eight_buttons() -> None:
    threats = [_threat(slug=f"t{i}") for i in range(20)]
    screen = render_category(_category(), threats, LOCALE, page=2, pages=4)
    assert len(_buttons(screen.html)) <= 8


# --- card ----------------------------------------------------------------


def test_card_uses_rich_markup() -> None:
    html = render_card(_card(), LOCALE, page=1, has_quiz=True).html

    for tag in ("<h1>", "<blockquote>", "<details>", "<footer>", "<hr/>"):
        assert tag in html
    assert "●●●" in html
    assert "Высокая" in html
    assert "RU" not in html


def test_card_has_a_details_block_per_step() -> None:
    html = render_card(_card(), LOCALE, page=1, has_quiz=True).html
    summaries = [strip_premium(text) for text in re.findall(r"<summary><h2>(.*?)</h2></summary>", html)]
    assert [re.sub(r"^[^\w]+", "", text) for text in summaries] == ["До", "Во время", "После", "Нельзя"]
    opened = re.findall(r"<details( open)?><summary><h2>(.*?)</h2>", html)
    labels = [strip_premium(label).split(" ", 1)[-1] for flag, label in opened if flag]
    assert labels == ["Во время"]


def test_card_marks_ru_fallback() -> None:
    assert "RU" in render_card(_card(fallback=True), LOCALE, page=1, has_quiz=False).html


def test_card_buttons_are_quiz_back_and_share() -> None:
    screen = render_card(_card(), LOCALE, page=1, has_quiz=True)
    buttons = _buttons(screen.html)
    primary = _primary(screen.html)
    assert len(primary) == 1
    assert parse_cb(primary[0]["data"]).action == "quiz_next"

    back = [button for button in buttons if button.get("data", "").startswith("b1:cat:")]
    assert len(back) == 1
    share = [button for button in buttons if button.get("type") == "switch_inline_query_current_chat"]
    assert len(share) == 1
    assert share[0]["query"] == "Пожар в квартире"

    no_quiz = render_card(_card(), LOCALE, page=1, has_quiz=False)
    assert not _primary(no_quiz.html)


def test_card_back_keeps_the_category_page() -> None:
    screen = render_card(_card(), LOCALE, page=3, has_quiz=False)
    pages = [cb.page for cb in _parsed(screen.html) if cb is not None and cb.action == "cat"]
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


def test_question_offers_four_options_one_per_row() -> None:
    screen = render_question(_quiz(), "fire-home", index=1, total=3, seed="a3f1", locale=LOCALE)
    rows = _rows(screen.html)

    assert [len(row) for row in rows] == [1, 1, 1, 1]
    answers = [parse_cb(button["data"]) for button in rows[0] + rows[1] + rows[2] + rows[3]]
    assert [cb.extra for cb in answers] == [
        ("q1", "0", "a3f1"),
        ("q1", "1", "a3f1"),
        ("q1", "2", "a3f1"),
        ("q1", "3", "a3f1"),
    ]
    assert "Вопрос 1 из 3" in screen.html
    assert "Вопрос 1 из 3" in strip_premium(screen.html)
    assert "<h3>" not in screen.html
    assert not _primary(screen.html)


def test_answered_question_marks_the_chosen_option() -> None:
    correct = render_question(_quiz(), "fire-home", 1, 3, "a3f1", LOCALE, chosen=0)
    assert [b.get("style") for b in _buttons(correct.html)][:4] == ["success", None, None, None]
    assert "Сначала вызов пожарной охраны." in correct.html
    assert parse_cb(_primary(correct.html)[0]["data"]).action == "quiz_next"

    wrong = render_question(_quiz(), "fire-home", 1, 3, "a3f1", LOCALE, chosen=2)
    assert [b.get("style") for b in _buttons(wrong.html)][:4] == ["success", None, "danger", None]


def _emphasized(html: str) -> list[dict[str, str]]:
    return [button for button in _buttons(html) if button.get("style") in {"primary", "success", "danger"}]


def test_result_shows_score_and_one_emphasized_retry() -> None:
    screen = render_result(correct=2, total=3, slug="fire-home", locale=LOCALE)
    assert "<h1>" in screen.html and "2 из 3" in screen.html
    buttons = _buttons(screen.html)
    assert len(buttons) == 2
    emphasized = _emphasized(screen.html)
    assert len(emphasized) == 1
    assert emphasized[0]["style"] == "success"
    parsed = parse_cb(emphasized[0]["data"])
    assert parsed is not None and parsed.action == "quiz_next" and parsed.arg == "fire-home"
    assert [cb.action for cb in _parsed(screen.html)][1] == "menu"


def test_failed_result_keeps_the_retry_primary() -> None:
    screen = render_result(correct=1, total=3, slug="fire-home", locale=LOCALE)
    assert [button["style"] for button in _emphasized(screen.html)] == ["primary"]


def test_navigation_screens_stay_neutral() -> None:
    for screen in (
        render_category(_category(), [_threat()], LOCALE, 1, 1),
        search_screen("пожар", [_threat()], LOCALE),
    ):
        assert not _emphasized(screen.html)


def test_quiz_styles_per_screen() -> None:
    card = render_card(_card(), LOCALE, page=1, has_quiz=True, can_game=True)
    assert [button["style"] for button in _emphasized(card.html)] == ["primary", "success"]

    question = render_question(_quiz(), "fire-home", 1, 3, "a3f1", LOCALE)
    assert not _emphasized(question.html)

    answered = render_question(_quiz(), "fire-home", 1, 3, "a3f1", LOCALE, chosen=1)
    styles = [button.get("style") for button in _buttons(answered.html)]
    assert styles[:4] == ["success", "danger", None, None]
    assert [button["style"] for button in _emphasized(answered.html)] == ["success", "danger", "primary"]


# --- search --------------------------------------------------------------


def test_search_lists_results_with_severity() -> None:
    screen = search_screen("пожар", [_threat(slug=f"t{i}") for i in range(3)], LOCALE)
    buttons = _buttons(screen.html)
    assert len(buttons) == 4
    cards = [cb for cb in _parsed(screen.html) if cb and cb.action == "card"]
    assert len(cards) == 3
    assert "●●●" in buttons[0]["label"]
    assert "<code>пожар</code>" in screen.html


def test_search_empty_state_explains_itself() -> None:
    screen = search_screen("", [], LOCALE)
    assert "Поиск" in screen.html
    assert [cb.action for cb in _parsed(screen.html)] == ["menu"]


def test_search_truncates_to_seven_results() -> None:
    screen = search_screen("x", [_threat(slug=f"t{i}") for i in range(50)], LOCALE)
    assert len(_buttons(screen.html)) == 8


def test_search_and_quiz_never_clip_normal_text() -> None:
    title = "Длинное, но настоящее название угрозы с пояснением"
    threat = dataclasses.replace(_threat(), title=title)
    assert "…" not in search_screen("пожар", [threat], LOCALE).html
    assert title in search_screen("пожар", [threat], LOCALE).html

    quiz = dataclasses.replace(
        _quiz(),
        prompt="Что делать первым делом при пожаре в квартире?",
        options=tuple(f"Вариант ответа номер {i}" for i in range(4)),
        explanation="Сначала вызовите пожарную охрану по номеру 101.",
    )
    html = render_question(quiz, "fire-home", 1, 3, "a3f1", LOCALE).html
    assert "…" not in html
    for text in (quiz.prompt, *quiz.options):
        assert text in html


# --- degradation helpers -------------------------------------------------


def test_keyboard_from_rich_rebuilds_the_same_controls() -> None:
    screen = render_card(_card(), LOCALE, page=2, has_quiz=True)
    markup = keyboard_from_rich(screen.html)
    assert markup is not None
    rows = [list(row) for row in markup.inline_keyboard]
    assert rows[0][0].callback_data is not None
    assert parse_cb(rows[0][0].callback_data).action == "quiz_next"
    assert rows[0][0].style == "primary"
    assert rows[1][1].switch_inline_query_current_chat == "Пожар в квартире"
    assert "<tg-button" not in strip_rich_buttons(screen.html)


# --- noise ---------------------------------------------------------------


def test_screens_are_clean_utf8_without_bom() -> None:
    for screen in _screens():
        assert not screen.html.startswith("\ufeff")
        assert "\ufffd" not in screen.html
        screen.html.encode("utf-8").decode("utf-8")