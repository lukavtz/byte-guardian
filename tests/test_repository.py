from __future__ import annotations

import json
from collections.abc import Sequence
from pathlib import Path

from app.content.repository import (
    CategoryRow,
    ContentRepository,
    QuizRow,
    Snapshot,
    TextRow,
    ThreatRow,
)


def _text(slug: str, title: str, summary: str) -> TextRow:
    return TextRow(
        threat_slug=slug,
        locale="ru",
        title=title,
        summary=summary,
        signs=("Дым",),
        before=("Датчик",),
        during=("Звоните 101",),
        after=("Не входите",),
        forbidden=("Не открывайте окна",),
        contacts=("101", "112"),
        source="МЧС России",
    )


def _snapshot() -> Snapshot:
    categories: Sequence[CategoryRow] = [
        CategoryRow(
            slug="fire",
            sort=1,
            emoji="🔥",
            titles={"ru": "Пожар", "en": "Fire"},
        )
    ]
    threats: Sequence[ThreatRow] = [
        ThreatRow(slug="fire-home", category_slug="fire", severity=3, sort=0),
        ThreatRow(slug="fire-pan", category_slug="fire", severity=1, sort=1),
    ]
    texts: Sequence[TextRow] = [
        _text(
            "fire-home",
            "Пожар в квартире",
            "При пожаре звоните 101 и выводите людей.",
        ),
        _text("fire-pan", "Пожар на кухне", "Масло на плите вспыхнуло."),
    ]
    quizzes: Sequence[QuizRow] = [
        QuizRow(
            threat_slug="fire-home",
            question_id="q1",
            locale="ru",
            prompt="Что делать первым?",
            options=("Вызвать 101", "Открыть окна"),
            answer=0,
            explanation="Сначала вызов.",
        )
    ]
    return categories, threats, texts, quizzes


def _repo(aliases: Path | None = None) -> ContentRepository:
    async def load() -> Snapshot:
        return _snapshot()

    return ContentRepository(loader=load, aliases_path=aliases)


async def test_load_and_categories() -> None:
    repo = _repo()
    await repo.load()

    categories = repo.categories()

    assert [view.slug for view in categories] == ["fire"]
    assert categories[0].title == "Пожар"
    assert categories[0].emoji == "🔥"
    assert categories[0].threat_count == 2


async def test_card_falls_back_to_ru() -> None:
    repo = _repo()
    await repo.load()

    card = repo.card("fire-home", "tt")

    assert card is not None
    assert card.fallback is True
    assert card.locale == "ru"
    assert card.title == "Пожар в квартире"
    assert card.category_title == "Пожар"
    assert card.icon == "🔥"
    assert card.contacts == ("101", "112")
    assert card.severity == 3


async def test_card_in_requested_locale_has_no_fallback() -> None:
    repo = _repo()
    await repo.load()

    card = repo.card("fire-home", "ru")

    assert card is not None
    assert card.fallback is False
    assert repo.card("nope", "ru") is None


async def test_search_russian_and_english(tmp_path: Path) -> None:
    aliases = tmp_path / "aliases.json"
    aliases.write_text(
        json.dumps({"en": {"fire": ["fire-home"]}}, ensure_ascii=False),
        encoding="utf-8",
    )
    repo = _repo(aliases)
    await repo.load()

    russian = repo.search("пожара", "ru")
    english = repo.search("fire", "en")

    assert russian[0].slug == "fire-home"
    assert [view.slug for view in russian] == ["fire-home", "fire-pan"]
    assert [view.slug for view in english] == ["fire-home"]
    assert english[0].fallback is True


async def test_search_empty_and_limit() -> None:
    repo = _repo()
    await repo.load()

    assert repo.search("", "ru") == []
    assert repo.search("   ", "ru") == []
    assert len(repo.search("пожар", "ru", limit=1)) == 1


async def test_quizzes_fall_back_and_has_quiz() -> None:
    repo = _repo()
    await repo.load()

    questions = repo.quizzes("fire-home", "tt")

    assert [question.question_id for question in questions] == ["q1"]
    assert questions[0].options == ("Вызвать 101", "Открыть окна")
    assert repo.has_quiz("fire-home", "tt") is True
    assert repo.has_quiz("fire-pan", "ru") is False
    assert repo.quizzes("nope", "ru") == []


async def test_threats_sorted_by_severity_with_fallback() -> None:
    repo = _repo()
    await repo.load()

    threats = repo.threats("fire", "en")

    assert [threat.slug for threat in threats] == ["fire-home", "fire-pan"]
    assert all(threat.fallback for threat in threats)
    assert threats[0].category_title == "Пожар"


async def test_disabled_category_hides_quizzes() -> None:
    categories = [CategoryRow(slug="fire", sort=1, emoji="🔥", titles={"ru": "Пожар"}, enabled=False)]
    snapshot = (categories, *_snapshot()[1:])

    async def load() -> Snapshot:
        return snapshot

    repo = ContentRepository(loader=load, aliases_path=None)
    await repo.load()

    assert repo.categories() == []
    assert repo.card("fire-home", "ru") is None
    assert repo.has_quiz("fire-home", "ru") is False