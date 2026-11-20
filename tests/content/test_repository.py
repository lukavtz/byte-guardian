from __future__ import annotations

import json
import statistics
import time
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

def _rich_snapshot() -> Snapshot:
    categories: Sequence[CategoryRow] = [
        CategoryRow(slug="fire", sort=1, emoji="🔥", titles={"ru": "Пожар"}),
        CategoryRow(slug="cyber", sort=2, emoji="💻", titles={"ru": "Кибер"}),
    ]
    threats: Sequence[ThreatRow] = [
        ThreatRow(slug="fire-exact", category_slug="fire", severity=1, sort=0),
        ThreatRow(slug="fire-home", category_slug="fire", severity=3, sort=1),
        ThreatRow(slug="smoke", category_slug="fire", severity=5, sort=2),
        ThreatRow(slug="phish", category_slug="cyber", severity=2, sort=3),
    ]
    texts: Sequence[TextRow] = [
        _text("fire-exact", "Пожар", "Горит всё."),
        _text("fire-home", "Пожар в квартире", "При пожаре звоните 101."),
        _text("smoke", "Дым в подъезде", "Пожар может быть скрыт."),
        _text("phish", "Фишинг", "Ссылка ведёт на поддельный сайт."),
    ]
    return categories, threats, texts, []


async def _rich_repo(tmp_path: Path) -> ContentRepository:
    aliases = tmp_path / "aliases.json"
    aliases.write_text(
        json.dumps(
            {"ru": {"мошенники": ["phish"], "мошенник": ["phish"]}},
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    async def load() -> Snapshot:
        return _rich_snapshot()

    repo = ContentRepository(loader=load, aliases_path=aliases)
    await repo.load()
    return repo


async def test_search_one_word_exact_and_inflected(tmp_path: Path) -> None:
    repo = await _rich_repo(tmp_path)

    exact = repo.search("Пожар", "ru")
    inflected = repo.search("пожаром", "ru")

    assert exact[0].slug == "fire-exact"
    assert [view.slug for view in inflected] == ["fire-home", "fire-exact", "smoke"]


async def test_search_two_word_query(tmp_path: Path) -> None:
    repo = await _rich_repo(tmp_path)

    hits = repo.search("пожар квартире", "ru")

    assert [view.slug for view in hits] == ["fire-home"]


async def test_search_stop_words_are_relaxed(tmp_path: Path) -> None:
    repo = await _rich_repo(tmp_path)

    strict = repo.find("пожар", "ru")
    relaxed = repo.find("что делать если при пожаре", "ru")

    assert strict.relaxed_query is None
    assert relaxed.relaxed_query == "пожаре"
    assert relaxed.items[0].slug == "fire-home"
    assert repo.find("что мне нужно", "ru").items == []
    assert repo.find("что мне нужно", "ru").relaxed_query is None


async def test_search_alias_only_hit(tmp_path: Path) -> None:
    repo = await _rich_repo(tmp_path)

    hits = repo.search("мошенники", "ru")

    assert [view.slug for view in hits] == ["phish"]


async def test_search_ranking_order(tmp_path: Path) -> None:
    repo = await _rich_repo(tmp_path)

    hits = repo.search("пожар", "ru")

    assert [view.slug for view in hits] == ["fire-exact", "fire-home", "smoke"]


async def test_search_empty_queries(tmp_path: Path) -> None:
    repo = await _rich_repo(tmp_path)

    assert repo.search("", "ru") == []
    assert repo.search("   ", "ru") == []
    assert repo.find("", "ru").items == []


async def test_search_median_latency(tmp_path: Path) -> None:
    categories: Sequence[CategoryRow] = [
        CategoryRow(slug="fire", sort=1, emoji="🔥", titles={"ru": "Пожар"})
    ]
    count = 60
    threats: Sequence[ThreatRow] = [
        ThreatRow(slug=f"fire-{index}", category_slug="fire", severity=index % 5, sort=index)
        for index in range(count)
    ]
    texts: Sequence[TextRow] = [
        _text(
            f"fire-{index}",
            f"Пожар номер {index}",
            f"Пожар в здании {index}. Звоните 101 и выходите на улицу.",
        )
        for index in range(count)
    ]
    snapshot: Snapshot = (categories, threats, texts, [])

    async def load() -> Snapshot:
        return snapshot

    repo = ContentRepository(loader=load, aliases_path=None)
    await repo.load()
    queries = ("пожар", "пожара", "пожар в квартире", "что делать при пожаре", "дым")

    samples: list[float] = []
    for index in range(1000):
        query = queries[index % len(queries)]
        started = time.perf_counter()
        repo.search(query, "ru")
        samples.append((time.perf_counter() - started) * 1000)

    median = statistics.median(samples)
    print(f"median search latency: {median:.4f} ms over {len(samples)} queries")
    assert median < 10
