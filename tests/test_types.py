from __future__ import annotations

import dataclasses

import pytest

from app.content.types import CardView, CategoryView, QuizView, ThreatView


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
    with pytest.raises(dataclasses.FrozenInstanceError):
        view.slug = "other"