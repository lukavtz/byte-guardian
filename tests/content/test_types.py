from __future__ import annotations

import dataclasses
import re
from types import SimpleNamespace

import pytest

from app.content.repository import (
    ContentRepository,
    TextRow,
    ThreatRow,
    _load_from_db,
    _text_row,
)
from app.content.types import CardView, CategoryView, QuizView, SourceCitation, ThreatView
from app.db.content import Threat, ThreatText


def _category() -> CategoryView:
    return CategoryView(slug="fraud", title="Fraud", emoji="🎣", sort=2, threat_count=5)


def _threat() -> ThreatView:
    return ThreatView(
        slug="fire-home",
        category_slug="fraud",
        category_title="Fraud",
        title="Fire at home",
        summary="Smoke first.",
        severity=3,
        locale="tt",
        fallback=True,
    )


def _card() -> CardView:
    return CardView(
        slug="fire-home",
        category_slug="fraud",
        category_title="Fraud",
        severity=3,
        locale="ru",
        fallback=False,
        title="Fire",
        summary="Smoke first.",
        quick="Call 101",
        mistakes=("Do not open windows",),
        citations=(SourceCitation(org="MChS", url="https://mchs.gov.ru/fire"),),
        updated="2026-11-20",
        signs=("smoke",),
        before=("plan",),
        during=("call",),
        after=("check",),
        forbidden=("panic",),
        contacts=("101",),
        source="mchs",
        icon="fire",
    )


def _quiz() -> QuizView:
    return QuizView(
        threat_slug="fire-home",
        question_id="q1",
        prompt="What first?",
        options=("a", "b", "c", "d"),
        answer=2,
        explanation="Because.",
    )


def test_category_positional_order() -> None:
    view = CategoryView("fraud", "Fraud", "🎣", 2, 5)
    assert view == _category()


def test_threat_positional_order() -> None:
    view = ThreatView("fire-home", "fraud", "Fraud", "Fire at home", "Smoke first.", 3, "tt", True)
    assert view == _threat()


def test_card_positional_order() -> None:
    view = CardView(
        "fire-home",
        "fraud",
        "Fraud",
        3,
        "ru",
        False,
        "Fire",
        "Smoke first.",
        ("smoke",),
        ("plan",),
        ("call",),
        ("check",),
        ("panic",),
        ("101",),
        "mchs",
        "fire",
        "Call 101",
        ("Do not open windows",),
        (SourceCitation("MChS", "https://mchs.gov.ru/fire"),),
        "2026-11-20",
    )
    assert view == _card()
    assert view.signs == ("smoke",)
    assert view.contacts == ("101",)


def test_quiz_positional_order() -> None:
    view = QuizView("fire-home", "q1", "What first?", ("a", "b", "c", "d"), 2, "Because.")
    assert view == _quiz()


@pytest.mark.parametrize("view", [_category(), _threat(), _card(), _quiz()])
def test_views_are_frozen_slots_dataclasses(view: object) -> None:
    assert dataclasses.is_dataclass(view)
    assert type(view).__slots__  # type: ignore[attr-defined]
    assert not hasattr(view, "__dict__")
    field = dataclasses.fields(view)[0].name
    with pytest.raises(dataclasses.FrozenInstanceError):
        setattr(view, field, "other")


def test_text_row_maps_enrichment_from_db_model() -> None:
    row = SimpleNamespace(
        threat_slug="fire-home",
        locale="ru",
        title="Fire",
        summary="Smoke first.",
        quick="Call 101",
        mistakes=["Do not open windows"],
        citations=[{"org": "MChS", "url": "https://mchs.gov.ru/fire"}],
        signs=["smoke"],
        before=["plan"],
        during=["call"],
        after=["check"],
        forbidden=["panic"],
        contacts=["101"],
        source="mchs",
    )

    mapped = _text_row(row)  # type: ignore[arg-type]

    assert mapped.quick == "Call 101"
    assert mapped.mistakes == ("Do not open windows",)
    assert mapped.citations == (
        SourceCitation(org="MChS", url="https://mchs.gov.ru/fire"),
    )


def test_enrichment_db_columns_exist() -> None:
    assert Threat.__table__.c.updated.nullable is True
    assert ThreatText.__table__.c.quick.nullable is True
    assert ThreatText.__table__.c.mistakes.nullable is True
    assert ThreatText.__table__.c.citations.nullable is True


class _Result:
    def __init__(self, rows: list[object]) -> None:
        self._rows = rows

    def all(self) -> list[object]:
        return self._rows


class _LoadSession:
    def __init__(self, rows: dict[type, list[object]]) -> None:
        self.rows = rows

    async def __aenter__(self) -> _LoadSession:
        return self

    async def __aexit__(self, *exc: object) -> bool:
        return False

    async def scalars(self, statement: object) -> _Result:
        from app.db.content import Category, QuizQuestion

        sql = str(statement)
        for model in (Category, Threat, ThreatText, QuizQuestion):
            if re.search(rf"FROM {model.__tablename__}(?:\s|$)", sql):
                return _Result(self.rows.get(model, []))
        return _Result([])


async def test_load_from_db_maps_updated_and_text_enrichment() -> None:
    threat = SimpleNamespace(
        slug="fire-home",
        category_slug="fire",
        severity=3,
        sort=0,
        enabled=True,
        updated="2026-11-20",
    )
    text = SimpleNamespace(
        threat_slug="fire-home",
        locale="ru",
        title="Fire",
        summary="Smoke first.",
        quick="Call 101",
        mistakes=["Do not open windows"],
        citations=[{"org": "MChS", "url": "https://mchs.gov.ru/fire"}],
        signs=["smoke"],
        before=["plan"],
        during=["call"],
        after=["check"],
        forbidden=["panic"],
        contacts=["101"],
        source="mchs",
    )
    rows = {
        Threat: [threat],
        ThreatText: [text],
    }

    class _Factory:
        def __call__(self) -> _LoadSession:
            return _LoadSession(rows)

    categories, threats, texts, quizzes = await _load_from_db(_Factory())  # type: ignore[arg-type]

    assert threats == [
        ThreatRow(
            slug="fire-home",
            category_slug="fire",
            severity=3,
            sort=0,
            enabled=True,
            updated="2026-11-20",
        )
    ]
    assert texts[0].quick == "Call 101"
    assert texts[0].citations == (
        SourceCitation(org="MChS", url="https://mchs.gov.ru/fire"),
    )


async def test_card_exposes_enrichment_fields() -> None:
    text = TextRow(
        threat_slug="fire-home",
        locale="ru",
        title="Fire",
        summary="Smoke first.",
        quick="Call 101",
        mistakes=("Do not open windows",),
        citations=(SourceCitation(org="MChS", url="https://mchs.gov.ru/fire"),),
        signs=("smoke",),
        before=("plan",),
        during=("call",),
        after=("check",),
        forbidden=("panic",),
        contacts=("101",),
        source="mchs",
    )

    async def load() -> tuple:
        from app.content.repository import CategoryRow

        return (
            [CategoryRow(slug="fire", sort=1, emoji="🔥", titles={"ru": "Fire"})],
            [
                ThreatRow(
                    slug="fire-home",
                    category_slug="fire",
                    severity=3,
                    sort=0,
                    updated="2026-11-20",
                )
            ],
            [text],
            [],
        )

    repo = ContentRepository(loader=load, aliases_path=None)
    await repo.load()

    card = repo.card("fire-home", "ru")
    assert card is not None
    assert card.quick == "Call 101"
    assert card.mistakes == ("Do not open windows",)
    assert card.citations == (
        SourceCitation(org="MChS", url="https://mchs.gov.ru/fire"),
    )
    assert card.updated == "2026-11-20"