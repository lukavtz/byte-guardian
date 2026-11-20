from __future__ import annotations

import dataclasses
import re
from html import unescape
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import pytest

from app.content.types import CardView, CategoryView, QuizView, ThreatView
from app.services.callbacks import parse_cb
from app.ui import emoji as emoji_mod
from app.ui.emoji import (
    DEFAULT_IDS,
    FALLBACKS,
    ICONS,
    PACK,
    PACK_IDS,
    plain_emoji,
    premium,
    strip_premium,
)
from app.ui.keyboards import PAGE_SIZE, keyboard_from_rich, strip_rich_buttons
from app.ui.render import (
    ART,
    ART_VERSION,
    BANNER,
    CARD_CAP,
    EXT,
    RichScreen,
    _art_version,
    render_card,
    render_category,
    render_chat,
    render_menu,
    render_not_found,
    render_question,
    render_result,
    render_sections,
    search_screen,
    share_text,
    share_url,
)

LOCALE = "ru"
BUTTON = re.compile(r"<tg-button(?=[\s>])([^>]*)>(.*?)</tg-button>", re.DOTALL)
ROW = re.compile(r"<tg-button-row\b[^>]*>(.*?)</tg-button-row>", re.DOTALL)
ATTRIBUTE = re.compile(r'([\w-]+)="([^"]*)"')
BARE_EMOJI = re.compile(r"[\U0001F000-\U0001FAFF\u2600-\u27BF\u2B00-\u2BFF]")


def _img_src(url: str) -> str:
    return f'<img src="{url}?v={ART_VERSION}"/>'


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
        render_result(2, 3, "fire-home", LOCALE, 2, 4),
        search_screen("пожар", [_threat()], LOCALE),
        render_not_found(LOCALE),
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
    assert PACK == "TgAndroidIcons"
    assert DEFAULT_IDS["search"] == PACK_IDS["🔎"]
    assert DEFAULT_IDS["test"] == PACK_IDS["✅"]
    assert DEFAULT_IDS["admin"] == PACK_IDS["🛠"]


def test_every_default_id_comes_from_the_one_pack() -> None:
    pack_ids = set(PACK_IDS.values())
    assert set(DEFAULT_IDS.values()) <= pack_ids
    assert set(FALLBACKS.values()) == set(ICONS.values()) <= set(PACK_IDS)
    assert {"fire", "terror", "tech", "nature", "medical", "chem", "rad", "cyber"} <= set(
        DEFAULT_IDS
    )


def test_no_screen_shows_a_bare_emoji_or_a_foreign_id(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(emoji_mod, "CUSTOM", dict(DEFAULT_IDS))
    pack_ids = set(PACK_IDS.values())
    for screen in _screens():
        assert set(re.findall(r'emoji-id="(\d+)"', screen.html)) <= pack_ids
        assert not BARE_EMOJI.findall(re.sub(r"<tg-emoji\b[^>]*>.*?</tg-emoji>", "", screen.html, flags=re.DOTALL))


def test_unknown_name_keeps_its_plain_character(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(emoji_mod, "CUSTOM", dict(DEFAULT_IDS))
    assert premium("no-such-name", "❓") == "❓"
    assert premium("no-such-name") == ""


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
            assert (
                button["attrs"].count("data=")
                + button["attrs"].count("query=")
                + button["attrs"].count("url=")
                == 1
            )


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
    assert "Первое действие" in html
    assert "Признаки</b><br/>• Дым из-под двери" in html
    assert len(re.findall(r"<summary><b>.*?</b></summary>", html)) == 4
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
    card_labels = {
        button["label"] for button in _buttons(render_card(_card(), LOCALE, 1, False).html)
    }
    assert any(label.endswith("‹ Назад") for label in card_labels)

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
    assert _img_src(f"{ART}/{slug}.jpg") in screen.html


# --- pager ---------------------------------------------------------------


def _pager_buttons(html: str) -> list[tuple[str, str | None]]:
    return [
        (button["label"], button.get("style"))
        for button in _buttons(html)
        if button["label"] in {"‹", "›"} or re.fullmatch(r"\d+/\d+", button["label"])
    ]


def test_pager_on_the_first_page_offers_forward_only() -> None:
    screen = render_category(_category(), [_threat()], LOCALE, 1, 3)

    assert _pager_buttons(screen.html) == [("1/3", None), ("›", "success")]


def test_pager_in_the_middle_walks_both_ways() -> None:
    screen = render_category(_category(), [_threat()], LOCALE, 2, 3)

    assert _pager_buttons(screen.html) == [
        ("‹", "danger"),
        ("2/3", None),
        ("›", "success"),
    ]


def test_pager_on_the_last_page_goes_back_only() -> None:
    screen = render_category(_category(), [_threat()], LOCALE, 3, 3)

    assert _pager_buttons(screen.html) == [("‹", "danger"), ("3/3", None)]


def test_single_page_has_no_pager() -> None:
    screen = render_category(_category(), [_threat()], LOCALE, 1, 1)

    assert _pager_buttons(screen.html) == []


def test_menu_opens_with_its_banner() -> None:
    html = render_menu([_category()], LOCALE).html
    assert _img_src(BANNER) in html
    assert _img_src(f"{ART}/menu.{EXT}") not in html
    assert html.index("<img") < html.index("<h1>")


def test_chat_uses_its_own_artwork() -> None:
    assert _img_src(f"{ART}/chat.jpg") in render_chat(LOCALE).html


def test_menu_banner_is_the_readme_banner() -> None:
    root = Path(__file__).resolve().parents[2]
    assert BANNER.endswith("/assets/brand/banner.jpg")
    assert "assets/brand/banner.jpg" in (root / "README.md").read_text(encoding="utf-8")


def test_unknown_section_falls_back_to_the_hero() -> None:
    assert _img_src(f"{ART}/hero.jpg") in search_screen("x", [], LOCALE).html
    unknown = render_category(_category(slug="space"), [_threat()], LOCALE, 1, 1)
    assert _img_src(f"{ART}/hero.jpg") in unknown.html


def test_card_and_quiz_use_the_category_artwork() -> None:
    card = render_card(_card(category_slug="chem"), LOCALE, 1, True)
    assert "<img " not in card.html
    question = render_question(_quiz(), "rad-smoke", 1, 3, "a3f1", LOCALE)
    assert _img_src(f"{ART}/rad.jpg") in question.html
    result = render_result(3, 3, "medical-aid", LOCALE)
    assert _img_src(f"{ART}/medical.jpg") in result.html


def test_art_urls_carry_a_cache_busting_version() -> None:
    """Telegram caches images by URL; a version marker makes new art a new URL."""
    assert ART_VERSION
    screens = [
        render_category(_category(slug="fire"), [_threat()], LOCALE, 1, 1),
        render_chat(LOCALE),
        render_menu([_category()], LOCALE),
        search_screen("x", [], LOCALE),
    ]
    sources = re.findall(r'<img src="([^"]+)"', "\n".join(s.html for s in screens))
    assert sources, "no artwork URLs rendered"
    for src in sources:
        parsed = urlparse(src)
        assert parse_qs(parsed.query)["v"] == [ART_VERSION], src


def test_art_version_tracks_asset_bytes(tmp_path: Path) -> None:
    """Changing deployed art bytes changes the cache-busting marker."""
    art = tmp_path / "assets" / "art"
    art.mkdir(parents=True)
    (art / "chat.jpg").write_bytes(b"original")
    original = _art_version(tmp_path)
    assert _art_version(tmp_path) == original
    (art / "chat.jpg").write_bytes(b"changed")
    assert _art_version(tmp_path) != original


# --- menu ----------------------------------------------------------------


def test_menu_is_a_doorway_with_at_most_seven_buttons() -> None:
    slugs = ["fire", "terror", "tech", "nature", "medical", "chem", "rad", "cyber"]
    categories = [_category(slug=slug, title=slug.title()) for slug in slugs]
    screen = render_menu(categories, LOCALE)

    rows = _rows(screen.html)
    assert [len(row) for row in rows] == [1, 1, 2, 1]
    parsed = _parsed(screen.html)
    assert [cb.action for cb in parsed] == [
        "chat",
        "sections",
        "search_hint",
        "random",
        "settings",
    ]
    assert len(parsed) <= 7
    # The eight sections are one tap deeper, not in the menu.
    assert not [cb for cb in parsed if cb.action == "cat"]


def test_sections_screen_lists_eight_sections_then_a_menu_button() -> None:
    slugs = ["fire", "terror", "tech", "nature", "medical", "chem", "rad", "cyber"]
    categories = [_category(slug=slug, title=slug.title()) for slug in slugs]
    screen = render_sections(categories, LOCALE)

    parsed = [cb for cb in _parsed(screen.html) if cb.action == "cat"]
    assert [cb.arg for cb in parsed] == slugs
    assert "‹ Назад" not in screen.html
    menu = [button for button in _buttons(screen.html) if button.get("data") == "b1:menu"]
    assert len(menu) == 1
    assert menu[0]["label"] == "🏠 Меню" and menu[0]["style"] == "success"
    assert _rows(screen.html)[-1][-1]["data"] == "b1:menu"


def test_every_screen_but_the_menu_ends_with_one_success_menu_button() -> None:
    screens = [
        render_sections([_category()], LOCALE),
        render_chat(LOCALE),
        render_category(_category(), [_threat()], LOCALE, 1, 1),
        render_card(_card(), LOCALE, page=1, has_quiz=True),
        render_card(_card(), LOCALE, page=1, has_quiz=False),
        render_question(_quiz(), "fire-home", 1, 3, "a3f1", LOCALE),
        render_question(_quiz(), "fire-home", 1, 3, "a3f1", LOCALE, chosen=0),
        render_result(2, 3, "fire-home", LOCALE),
        search_screen("пожар", [_threat()], LOCALE),
        search_screen("", [], LOCALE),
        render_not_found(LOCALE),
    ]
    for screen in screens:
        menu = [button for button in _buttons(screen.html) if button.get("data") == "b1:menu"]
        assert len(menu) == 1
        assert menu[0]["label"] == "🏠 Меню" and menu[0]["style"] == "success"
        assert _rows(screen.html)[-1][-1]["data"] == "b1:menu"


def test_menu_itself_has_no_menu_button() -> None:
    screen = render_menu([_category()], LOCALE, is_admin=True)
    assert "🏠 Меню" not in [button["label"] for button in _buttons(screen.html)]
    assert "b1:menu" not in _data(screen.html)


def test_menu_button_is_success_even_beside_a_primary_cta() -> None:
    for screen in (
        render_card(_card(), LOCALE, page=1, has_quiz=True),
        render_question(_quiz(), "fire-home", 1, 3, "a3f1", LOCALE, chosen=0),
    ):
        assert len(_primary(screen.html)) == 1
    for screen in (
        render_card(_card(), LOCALE, page=1, has_quiz=True),
        render_question(_quiz(), "fire-home", 1, 3, "a3f1", LOCALE, chosen=0),
        render_result(2, 3, "fire-home", LOCALE),
    ):
        menu = [b for b in _buttons(screen.html) if b["label"] == "🏠 Меню"]
        assert len(menu) == 1 and menu[0]["style"] == "success"


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
    assert styles == [
        ("Тест", "primary"),
        ("Играть", "success"),
        ("‹ Назад", "danger"),
        ("Меню", "success"),
    ]


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


def test_menu_never_exceeds_seven_buttons() -> None:
    categories = [_category(slug=f"c{i}", title=f"Раздел {i}") for i in range(9)]
    assert len(_data(render_menu(categories, LOCALE).html)) == 5
    assert len(_data(render_menu(categories, LOCALE, is_admin=True).html)) == 6


def test_sections_screen_never_exceeds_eight_categories() -> None:
    categories = [_category(slug=f"c{i}", title=f"Раздел {i}") for i in range(9)]
    screen = render_sections(categories, LOCALE)
    assert "b1:cat:c8:1" not in _data(screen.html)


def test_chat_screen_carries_the_heading_lead_and_two_ways_out() -> None:
    screen = render_chat(LOCALE)
    assert "<h1>" in screen.html and "Чат с Байтом" in screen.html
    assert "Напишите вопрос по БЖД — отвечу по карточкам. Выйти — кнопка ниже." in screen.html
    rows = _rows(screen.html)
    assert [len(row) for row in rows] == [1, 1]
    assert [button["data"] for button in rows[0]] == ["b1:chat_exit"]
    assert [button["data"] for button in rows[1]] == ["b1:menu"]
    assert "style" not in rows[0][0]
    assert rows[1][0]["style"] == "success"
    assert rows[1][0]["label"].endswith("Меню")


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


def test_category_never_exceeds_nine_buttons() -> None:
    threats = [_threat(slug=f"t{i}") for i in range(20)]
    screen = render_category(_category(), threats, LOCALE, page=2, pages=4)
    # Five cards, the three-cell pager and the menu.
    assert len(_buttons(screen.html)) <= 9


# --- card ----------------------------------------------------------------


def test_card_uses_rich_markup() -> None:
    html = render_card(_card(), LOCALE, page=1, has_quiz=True).html

    for tag in ("<h1>", "<details>", "<footer>"):
        assert tag in html
    assert "<hr/>" not in html
    assert "●●●" in html
    assert "Высокая" in html
    assert "RU" not in html


def test_card_has_a_details_block_per_step() -> None:
    html = render_card(_card(), LOCALE, page=1, has_quiz=True).html
    raw = re.findall(r"<summary>(.*?)</summary>", html)
    summaries = [strip_premium(re.sub(r"<[^>]+>", "", text)).strip() for text in raw]
    assert [label.split(" ", 1)[-1] for label in summaries] == ["До", "Во время", "После", "Нельзя"]
    opened = re.findall(r"<details( open)?>", html)
    assert opened == ["", " open", "", ""]


def test_card_details_have_visible_labels_without_nested_headings() -> None:
    html = render_card(_card(), LOCALE, page=1, has_quiz=True).html
    raw = re.findall(r"<summary>(.*?)</summary>", html)
    summaries = [re.sub(r"^\W+\s*", "", strip_premium(re.sub(r"<[^>]+>", "", item))).strip() for item in raw]
    assert len(summaries) == 4
    assert all("<h2>" not in item for item in raw)
    assert summaries == ["До", "Во время", "После", "Нельзя"]


def test_card_keeps_the_first_action_and_contacts_visible() -> None:
    card = dataclasses.replace(_card(), quick="Звоните 101")
    html = render_card(card, LOCALE, page=1, has_quiz=True).html
    assert "Первое действие" in html
    assert "Звоните 101" in html
    assert "101" in html and "112" in html
    assert html.index("Звоните 101") < html.index("<details")


def test_card_warns_when_items_are_dropped() -> None:
    long = "я" * 160
    fat = dataclasses.replace(
        _card(),
        signs=tuple(long for _ in range(80)),
        before=tuple(long for _ in range(80)),
        during=tuple(long for _ in range(80)),
    )
    html = render_card(fat, LOCALE, page=1, has_quiz=True).html
    assert "Показано не всё" in html
    assert "…" in html


def test_card_puts_text_before_art() -> None:
    html = render_card(_card(), LOCALE, page=1, has_quiz=True).html
    assert "<img " not in html
    assert "<hr/>" not in html


def test_card_keeps_emergency_numbers_above_the_fold() -> None:
    long = "я" * 160
    fat = dataclasses.replace(
        _card(),
        signs=tuple(long for _ in range(80)),
        before=tuple(long for _ in range(80)),
        during=tuple(long for _ in range(80)),
    )
    above = render_card(fat, LOCALE, page=1, has_quiz=True).html.split("<details", 1)[0]
    assert "101" in above and "112" in above


def test_card_marks_ru_fallback() -> None:
    assert "RU" in render_card(_card(fallback=True), LOCALE, page=1, has_quiz=False).html


def test_card_footer_names_sources_without_links() -> None:
    card = dataclasses.replace(
        _card(),
        contacts=("101", "https://mchs.gov.ru/contacts"),
        source="МЧС России (https://mchs.gov.ru)",
    )
    html = render_card(card, LOCALE, page=1, has_quiz=False).html
    footer = html[html.rindex("<footer>") : html.rindex("</footer>")]

    assert "http" not in footer
    assert "mchs.gov.ru" not in footer
    assert "МЧС России" in footer and "101" in footer


def test_card_buttons_are_quiz_back_and_share() -> None:
    screen = render_card(_card(), LOCALE, page=1, has_quiz=True)
    buttons = _buttons(screen.html)
    primary = _primary(screen.html)
    assert len(primary) == 1
    assert parse_cb(primary[0]["data"]).action == "quiz_next"

    back = [button for button in buttons if button.get("data", "").startswith("b1:cat:")]
    assert len(back) == 1
    share = [button for button in buttons if button.get("type") == "url"]
    assert len(share) == 1
    assert share[0]["url"].startswith("https://t.me/share/url?")
    assert "switch_inline_query_current_chat" not in share[0]["attrs"]

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

    assert [len(row) for row in rows] == [1, 1, 1, 1, 1]
    answers = [parse_cb(button["data"]) for row in rows[:4] for button in row]
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
    assert [button["style"] for button in emphasized] == ["success", "success"]
    assert emphasized[0]["data"].startswith("b1:quiz_next:")
    parsed = parse_cb(emphasized[0]["data"])
    assert parsed is not None and parsed.action == "quiz_next" and parsed.arg == "fire-home"
    assert [cb.action for cb in _parsed(screen.html)][1] == "menu"


def test_failed_result_keeps_the_retry_primary() -> None:
    screen = render_result(correct=1, total=3, slug="fire-home", locale=LOCALE)
    assert [button["style"] for button in _emphasized(screen.html)] == ["primary", "success"]


def test_navigation_screens_keep_only_the_menu_emphasized() -> None:
    for screen in (
        render_category(_category(), [_threat()], LOCALE, 1, 1),
        search_screen("пожар", [_threat()], LOCALE),
    ):
        emphasized = _emphasized(screen.html)
        assert [(button["data"], button["style"]) for button in emphasized] == [
            ("b1:menu", "success")
        ]


def test_quiz_styles_per_screen() -> None:
    card = render_card(_card(), LOCALE, page=1, has_quiz=True, can_game=True)
    assert [button["style"] for button in _emphasized(card.html)] == [
        "primary",
        "success",
        "danger",
        "success",
    ]

    question = render_question(_quiz(), "fire-home", 1, 3, "a3f1", LOCALE)
    assert [(button["data"], button["style"]) for button in _emphasized(question.html)] == [
        ("b1:menu", "success")
    ]

    answered = render_question(_quiz(), "fire-home", 1, 3, "a3f1", LOCALE, chosen=1)
    styles = [button.get("style") for button in _buttons(answered.html)]
    assert styles[:4] == ["success", "danger", None, None]
    assert [button["style"] for button in _emphasized(answered.html)] == [
        "success",
        "danger",
        "primary",
        "success",
    ]


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
    assert [cb.action for cb in _parsed(screen.html)] == ["random", "menu"]


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
    assert rows[1][0].url.startswith("https://t.me/share/url?")
    assert rows[2][1].callback_data == "b1:menu" and rows[2][1].style == "success"
    assert "<tg-button" not in strip_rich_buttons(screen.html)


# --- noise ---------------------------------------------------------------


def test_screens_are_clean_utf8_without_bom() -> None:
    for screen in _screens():
        assert not screen.html.startswith("\ufeff")
        assert "\ufffd" not in screen.html
        screen.html.encode("utf-8").decode("utf-8")

# --- share ---------------------------------------------------------------


def test_share_url_encodes_the_deep_link_and_a_human_message() -> None:
    url = share_url("byte_bot", _card())
    query = parse_qs(urlparse(url).query)

    assert url.startswith("https://t.me/share/url?")
    assert query["url"] == ["https://t.me/byte_bot?start=card_fire-home"]
    assert query["text"] == ["Пожар в квартире\nЗвоните 101\nБайт · ТИСБИ"]
    assert "%20" in url and "+" not in url
    assert "—" not in url and "…" not in query["text"][0]


def test_share_text_trims_a_long_action_without_cutting_a_word() -> None:
    card = dataclasses.replace(_card(), during=(), summary="слово " * 60)
    lines = share_text(card).splitlines()

    assert lines[0] == "Пожар в квартире"
    assert lines[-1] == "Байт · ТИСБИ"
    assert lines[1].endswith("…") and len(lines[1]) <= 181
    assert lines[1][:-1].rstrip().endswith("слово")


def test_share_without_a_username_still_shares_the_text() -> None:
    query = parse_qs(urlparse(share_url(None, _card())).query)

    assert "url" not in query
    assert query["text"][0].startswith("Пожар в квартире")


# --- stale screens -------------------------------------------------------


def test_not_found_names_the_state_and_offers_menu_and_search() -> None:
    screen = render_not_found(LOCALE)

    assert "Карточка не найдена" in screen.html
    assert [cb.action for cb in _parsed(screen.html)] == ["search_hint", "menu"]


# --- paged search --------------------------------------------------------


def _many(count: int) -> list[ThreatView]:
    return [_threat(slug=f"t{i}") for i in range(count)]


def test_search_page_one_offers_forward_only() -> None:
    screen = search_screen(
        "пожар", _many(9), LOCALE, page=1, total=9, digest="0123abcd"
    )
    parsed = _parsed(screen.html)

    assert "Найдено: 9" in screen.html
    assert len([cb for cb in parsed if cb.action == "card"]) == 7
    labels = [button["label"] for button in _buttons(screen.html)]
    assert "‹" not in labels and "›" in labels and "1/2" in labels
    pages = [(cb.arg, cb.page) for cb in parsed if cb.action == "search"]
    assert pages == [("0123abcd", 1), ("0123abcd", 2)]


def test_search_last_page_goes_back_only_and_keeps_the_total() -> None:
    screen = search_screen(
        "пожар", _many(9), LOCALE, page=2, total=9, digest="0123abcd"
    )

    assert "Найдено: 9" in screen.html
    assert len([cb for cb in _parsed(screen.html) if cb.action == "card"]) == 2
    pages = [(cb.arg, cb.page) for cb in _parsed(screen.html) if cb.action == "search"]
    assert pages == [("0123abcd", 1), ("0123abcd", 2)]
    labels = [button["label"] for button in _buttons(screen.html)]
    assert "‹" in labels and "›" not in labels and "2/2" in labels


def test_search_without_a_digest_never_offers_paging() -> None:
    screen = search_screen("пожар", _many(9), LOCALE, page=1, total=9)

    assert not [cb for cb in _parsed(screen.html) if cb.action == "search"]


# --- quiz result ---------------------------------------------------------


def test_result_shows_the_session_bar_and_the_streak() -> None:
    screen = render_result(2, 3, "fire-home", LOCALE, 2, 5)

    assert "▰" * 7 + "▱" * 3 in screen.html
    assert "Серия: 2 · Рекорд: 5" in screen.html
    assert "▰" * 10 in render_result(3, 3, "fire-home", LOCALE, 3, 3).html
