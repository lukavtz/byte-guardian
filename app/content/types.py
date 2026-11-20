"""Immutable views exchanged between the content repository, UI, and handlers."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class CategoryView:
    slug: str
    title: str
    emoji: str
    sort: int
    threat_count: int


@dataclass(frozen=True, slots=True)
class ThreatView:
    slug: str
    category_slug: str
    category_title: str
    title: str
    summary: str
    severity: int
    locale: str
    fallback: bool


@dataclass(frozen=True, slots=True)
class CardView:
    slug: str
    category_slug: str
    category_title: str
    severity: int
    locale: str
    fallback: bool
    title: str
    summary: str
    signs: tuple[str, ...]
    before: tuple[str, ...]
    during: tuple[str, ...]
    after: tuple[str, ...]
    forbidden: tuple[str, ...]
    contacts: tuple[str, ...]
    source: str
    icon: str


@dataclass(frozen=True, slots=True)
class QuizView:
    threat_slug: str
    question_id: str
    prompt: str
    options: tuple[str, ...]
    answer: int
    explanation: str