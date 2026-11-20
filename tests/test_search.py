"""Text search and inline lookup: handlers only, no Telegram, no database."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.content.repository import CategoryRow, ContentRepository, Snapshot, TextRow, ThreatRow
from app.handlers import inline as inline_handler
from app.handlers import search as search_handler
from app.services.callbacks import parse_cb

SLUGS = ("fire", "terror", "tech", "nature", "medical", "chem", "rad", "cyber")
LOCALE = "ru"


def _text(slug: str, title: str, summary: str = "Короткая карточка.") -> TextRow:
    return TextRow(
        threat_slug=slug,
        locale=LOCALE,
        title=title,
        summary=summary,
        signs=("Признак",),
        before=("До",),
        during=("Во время",),
        after=("После",),
        forbidden=("Нельзя",),
        contacts=("112",),
        source="МЧС России",
    )


def _snapshot(titles: dict[str, str] | None = None) -> Snapshot:
    titles = titles or {}
    categories = [
        CategoryRow(slug=slug, sort=index, emoji="🔥", titles={LOCALE: slug.title()})
        for index, slug in enumerate(SLUGS)
    ]
    threats = [ThreatRow(slug="fire-home", category_slug="fire", severity=3, sort=0)]
    texts = [_text("fire-home", titles.get("fire-home", "Пожар в квартире"))]
    return categories, threats, texts, []


async def _repo(titles: dict[str, str] | None = None) -> ContentRepository:
    async def load() -> Snapshot:
        return _snapshot(titles)

    repo = ContentRepository(loader=load, aliases_path=None)
    await repo.load()
    return repo


def _message(text: str, locale: str = "ru") -> MagicMock:
    message = MagicMock()
    message.text = text
    message.chat = SimpleNamespace(id=42, type="private")
    message.from_user = SimpleNamespace(id=7, language_code=locale)
    return message


@pytest.fixture
def shown(monkeypatch: pytest.MonkeyPatch) -> list:
    calls: list = []

    async def fake_show(bot: object, chat_id: int, screen: object, message_id: int | None = None) -> None:
        calls.append(screen)

    monkeypatch.setattr(search_handler, "show", fake_show)
    return calls


def _actions(screen: object) -> list[tuple[str, str]]:
    keyboard = screen.keyboard
    assert keyboard is not None
    parsed = []
    for row in keyboard.inline_keyboard:
        for button in row:
            callback = parse_cb(button.callback_data)
            if callback is not None:
                parsed.append((callback.action, callback.arg))
    return parsed


async def test_private_text_searches_and_offers_the_card(shown: list) -> None:
    await search_handler.on_text(_message("пожара"), await _repo(), None)

    assert len(shown) == 1
    assert ("card", "fire-home") in _actions(shown[0])


async def test_command_text_is_never_a_query(shown: list) -> None:
    await search_handler.on_text(_message("/start"), await _repo(), None)

    assert shown == []


async def test_empty_result_offers_eight_category_hints(shown: list) -> None:
    await search_handler.on_text(_message("абракадабра"), await _repo(), None)

    actions = _actions(shown[0])
    assert len(actions) == 8
    assert all(action == "cat" for action, _ in actions)
    assert {arg for _, arg in actions} == set(SLUGS)


async def _answer(query_text: str, repo: ContentRepository, session_factory: object | None = None) -> dict:
    query = MagicMock()
    query.query = query_text
    query.from_user = SimpleNamespace(id=7, language_code=LOCALE)
    query.bot = SimpleNamespace(username="byte_bot")
    bot = MagicMock()
    bot.answer_inline_query = AsyncMock()
    await inline_handler.on_inline(query, repo, bot, session_factory)  # type: ignore[arg-type]
    bot.answer_inline_query.assert_awaited_once()
    return bot.answer_inline_query.await_args.kwargs


async def test_inline_result_has_deep_link_and_html_limits() -> None:
    kwargs = await _answer("пожар", await _repo())

    assert kwargs["cache_time"] == 30
    assert kwargs["is_personal"] is True
    results = kwargs["results"]
    assert len(results) == 1
    article = results[0]
    html = article.input_message_content.message_text
    assert "https://t.me/byte_bot?start=card_fire-home" in html
    assert "112" in html
    assert len(html.encode("utf-16-le")) // 2 <= 4096
    assert article.reply_markup is not None
    assert article.reply_markup.inline_keyboard[0][0].url == "https://t.me/byte_bot?start=card_fire-home"


async def test_inline_titles_are_plain_text_but_the_body_is_escaped() -> None:
    kwargs = await _answer("пожар", await _repo({"fire-home": "<script>Пожар</script>"}))

    article = kwargs["results"][0]
    assert article.title == "<script>Пожар</script>"
    assert len(article.description) <= 200
    assert "<script>" not in article.input_message_content.message_text
    assert "&lt;script&gt;" in article.input_message_content.message_text


async def test_search_uses_the_saved_locale(shown: list, monkeypatch: pytest.MonkeyPatch) -> None:
    async def saved_locale(session_factory: object, tg_id: int, code: str | None) -> str:
        assert tg_id == 7
        return "en"

    monkeypatch.setattr(search_handler, "get_locale", saved_locale)
    await search_handler.on_text(_message("пожар"), await _repo(), None, object())  # type: ignore[arg-type]

    assert "Search" in shown[0].html


async def test_inline_uses_the_saved_locale(monkeypatch: pytest.MonkeyPatch) -> None:
    async def saved_locale(session_factory: object, tg_id: int, code: str | None) -> str:
        return "en"

    monkeypatch.setattr(inline_handler, "get_locale", saved_locale)
    kwargs = await _answer("", await _repo(), object())

    assert kwargs["switch_pm_text"] == "Open categories"


async def test_inline_empty_query_always_answers_with_menu() -> None:
    kwargs = await _answer("", await _repo())

    assert kwargs["cache_time"] == 30
    assert kwargs["switch_pm_text"]
    assert len(kwargs["results"]) == 1
    article = kwargs["results"][0]
    assert "https://t.me/byte_bot" in article.input_message_content.message_text


async def test_inline_result_without_matches_switches_to_menu() -> None:
    kwargs = await _answer("абракадабра", await _repo())

    assert kwargs["switch_pm_text"]
    assert kwargs["results"]


async def test_inline_caps_results_and_keeps_html_small() -> None:
    categories = [CategoryRow(slug="fire", sort=1, emoji="🔥", titles={LOCALE: "Пожар"})]
    threats = [ThreatRow(slug=f"fire-{index}", category_slug="fire", severity=3, sort=index) for index in range(15)]
    texts = [_text(f"fire-{index}", "Пожар " + "я" * 5000, "я" * 5000) for index in range(15)]

    async def load() -> Snapshot:
        return categories, threats, texts, []

    repo = ContentRepository(loader=load, aliases_path=None)
    await repo.load()
    kwargs = await _answer("пожар", repo)

    assert len(kwargs["results"]) == 10
    for article in kwargs["results"]:
        assert len(article.input_message_content.message_text) <= 4096
        assert len(article.title) <= 120
