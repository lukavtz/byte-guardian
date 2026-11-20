"""In-memory content index backed by Postgres, with locale fallback to ``ru``."""

from __future__ import annotations

import json
import re
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.content.types import CardView, CategoryView, QuizView, ThreatView
from app.db.content import Category, QuizQuestion, Threat, ThreatText

DEFAULT_LOCALE = "ru"
ALIASES_PATH = Path("content/aliases.json")
_TOKEN_RE = re.compile(r"\w+")


@dataclass(frozen=True, slots=True)
class CategoryRow:
    slug: str
    sort: int
    emoji: str
    titles: Mapping[str, str]
    enabled: bool = True


@dataclass(frozen=True, slots=True)
class ThreatRow:
    slug: str
    category_slug: str
    severity: int
    sort: int
    enabled: bool = True


@dataclass(frozen=True, slots=True)
class TextRow:
    threat_slug: str
    locale: str
    title: str
    summary: str
    signs: tuple[str, ...]
    before: tuple[str, ...]
    during: tuple[str, ...]
    after: tuple[str, ...]
    forbidden: tuple[str, ...]
    contacts: tuple[str, ...]
    source: str


@dataclass(frozen=True, slots=True)
class QuizRow:
    threat_slug: str
    question_id: str
    locale: str
    prompt: str
    options: tuple[str, ...]
    answer: int
    explanation: str


Snapshot = tuple[
    Sequence[CategoryRow],
    Sequence[ThreatRow],
    Sequence[TextRow],
    Sequence[QuizRow],
]
Loader = Callable[[], Awaitable[Snapshot]]


def _tokens(value: str) -> list[str]:
    return _TOKEN_RE.findall(value.casefold())


def _token_match(left: str, right: str) -> bool:
    if left == right:
        return True
    # ponytail: prefix heuristic instead of a stemmer; swap if false positives hurt.
    return len(left) >= 4 and len(right) >= 4 and (
        left.startswith(right) or right.startswith(left)
    )


def _read_aliases(path: Path | None) -> dict[str, dict[str, frozenset[str]]]:
    if path is None:
        return {}
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    if not isinstance(raw, dict):
        return {}
    fallback: dict[str, dict[str, set[str]]] = {}
    for key, value in raw.items():
        if not isinstance(key, str):
            continue
        if isinstance(value, dict):
            for alias, slugs in value.items():
                if isinstance(alias, str) and isinstance(slugs, list):
                    bucket = fallback.setdefault(key, {}).setdefault(
                        alias.casefold(), set()
                    )
                    bucket.update(s for s in slugs if isinstance(s, str))
        elif isinstance(value, list):
            bucket = fallback.setdefault(DEFAULT_LOCALE, {}).setdefault(
                key.casefold(), set()
            )
            bucket.update(s for s in value if isinstance(s, str))
    return {
        locale: {alias: frozenset(slugs) for alias, slugs in aliases.items()}
        for locale, aliases in fallback.items()
    }


def _text_row(row: ThreatText) -> TextRow:
    return TextRow(
        threat_slug=row.threat_slug,
        locale=row.locale,
        title=row.title,
        summary=row.summary,
        signs=tuple(row.signs),
        before=tuple(row.before),
        during=tuple(row.during),
        after=tuple(row.after),
        forbidden=tuple(row.forbidden),
        contacts=tuple(row.contacts),
        source=row.source,
    )


async def _load_from_db(
    session_factory: async_sessionmaker[AsyncSession],
) -> Snapshot:
    async with session_factory() as session:
        categories = (await session.scalars(select(Category))).all()
        threats = (await session.scalars(select(Threat))).all()
        texts = (await session.scalars(select(ThreatText))).all()
        quizzes = (await session.scalars(select(QuizQuestion))).all()
    return (
        [
            CategoryRow(
                slug=row.slug,
                sort=row.sort,
                emoji=row.emoji,
                titles=dict(row.titles or {}),
                enabled=row.enabled,
            )
            for row in categories
        ],
        [
            ThreatRow(
                slug=row.slug,
                category_slug=row.category_slug,
                severity=row.severity,
                sort=row.sort,
                enabled=row.enabled,
            )
            for row in threats
        ],
        [
            TextRow(
                threat_slug=row.threat_slug,
                locale=row.locale,
                title=row.title,
                summary=row.summary,
                signs=tuple(row.signs),
                before=tuple(row.before),
                during=tuple(row.during),
                after=tuple(row.after),
                forbidden=tuple(row.forbidden),
                contacts=tuple(row.contacts),
                source=row.source,
            )
            for row in texts
        ],
        [
            QuizRow(
                threat_slug=row.threat_slug,
                question_id=row.question_id,
                locale=row.locale,
                prompt=row.prompt,
                options=tuple(row.options),
                answer=row.answer,
                explanation=row.explanation,
            )
            for row in quizzes
        ],
    )


class ContentRepository:
    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession] | None = None,
        *,
        loader: Loader | None = None,
        aliases_path: Path | None = ALIASES_PATH,
    ) -> None:
        if session_factory is None and loader is None:
            raise ValueError("session_factory or loader required")
        self._factory = session_factory
        self._loader = loader
        self._aliases_path = aliases_path
        self._categories: dict[str, CategoryRow] = {}
        self._threats: dict[str, ThreatRow] = {}
        self._texts: dict[tuple[str, str], TextRow] = {}
        self._quizzes: dict[tuple[str, str], tuple[QuizRow, ...]] = {}
        self._aliases: dict[str, dict[str, frozenset[str]]] = {}

    async def load(self) -> None:
        if self._loader is not None:
            snapshot = await self._loader()
        else:
            assert self._factory is not None
            snapshot = await _load_from_db(self._factory)
        categories, threats, texts, quizzes = snapshot
        self._categories = {row.slug: row for row in categories}
        self._threats = {row.slug: row for row in threats}
        self._texts = {(row.threat_slug, row.locale): row for row in texts}
        grouped: dict[tuple[str, str], list[QuizRow]] = {}
        for row in quizzes:
            grouped.setdefault((row.threat_slug, row.locale), []).append(row)
        self._quizzes = {key: tuple(value) for key, value in grouped.items()}
        self._aliases = _read_aliases(self._aliases_path)

    def categories(self) -> list[CategoryView]:
        views = [
            CategoryView(
                slug=row.slug,
                title=self._category_title(row, DEFAULT_LOCALE),
                emoji=row.emoji,
                sort=row.sort,
                threat_count=sum(
                    1
                    for threat in self._threats.values()
                    if threat.category_slug == row.slug and threat.enabled
                ),
            )
            for row in self._categories.values()
            if row.enabled
        ]
        return sorted(views, key=lambda view: (view.sort, view.slug))

    def threats(self, category_slug: str, locale: str) -> list[ThreatView]:
        category = self._categories.get(category_slug)
        if category is None or not category.enabled:
            return []
        views: list[ThreatView] = []
        for threat in self._threats.values():
            if threat.category_slug != category_slug or not threat.enabled:
                continue
            text, fallback = self._resolve(threat.slug, locale)
            if text is None:
                continue
            views.append(
                ThreatView(
                    slug=threat.slug,
                    category_slug=category.slug,
                    category_title=self._category_title(category, text.locale),
                    title=text.title,
                    summary=text.summary,
                    severity=threat.severity,
                    locale=text.locale,
                    fallback=fallback,
                )
            )
        return sorted(
            views, key=lambda view: (-view.severity, view.title.casefold())
        )

    def card(self, slug: str, locale: str) -> CardView | None:
        threat = self._threats.get(slug)
        if threat is None or not threat.enabled:
            return None
        category = self._categories.get(threat.category_slug)
        if category is None or not category.enabled:
            return None
        text, fallback = self._resolve(slug, locale)
        if text is None:
            return None
        return CardView(
            slug=threat.slug,
            category_slug=category.slug,
            category_title=self._category_title(category, text.locale),
            severity=threat.severity,
            locale=text.locale,
            fallback=fallback,
            title=text.title,
            summary=text.summary,
            signs=text.signs,
            before=text.before,
            during=text.during,
            after=text.after,
            forbidden=text.forbidden,
            contacts=text.contacts,
            source=text.source,
            icon=category.emoji,
        )

    def search(self, query: str, locale: str, limit: int = 10) -> list[ThreatView]:
        needle = query.strip().casefold()
        if not needle:
            return []
        tokens = _tokens(needle)
        alias_hits = self._alias_hits(needle, tokens, locale)
        views: list[ThreatView] = []
        for threat in self._threats.values():
            if not threat.enabled:
                continue
            category = self._categories.get(threat.category_slug)
            if category is None or not category.enabled:
                continue
            text, fallback = self._resolve(threat.slug, locale)
            if text is None:
                continue
            haystack = _tokens(f"{text.title} {text.summary}")
            if threat.slug in alias_hits or any(
                _token_match(token, word)
                for token in tokens
                for word in haystack
            ):
                views.append(
                    ThreatView(
                        slug=threat.slug,
                        category_slug=category.slug,
                        category_title=self._category_title(
                            category, text.locale
                        ),
                        title=text.title,
                        summary=text.summary,
                        severity=threat.severity,
                        locale=text.locale,
                        fallback=fallback,
                    )
                )
        views.sort(key=lambda view: (-view.severity, view.title.casefold()))
        return views[:limit]

    def quizzes(self, slug: str, locale: str) -> list[QuizView]:
        threat = self._threats.get(slug)
        if threat is None or not threat.enabled:
            return []
        category = self._categories.get(threat.category_slug)
        if category is None or not category.enabled:
            return []
        rows = self._quizzes.get((slug, locale), ())
        if not rows and locale != DEFAULT_LOCALE:
            rows = self._quizzes.get((slug, DEFAULT_LOCALE), ())
        return [
            QuizView(
                threat_slug=slug,
                question_id=row.question_id,
                prompt=row.prompt,
                options=row.options,
                answer=row.answer,
                explanation=row.explanation,
            )
            for row in rows
        ]

    def has_quiz(self, slug: str, locale: str) -> bool:
        return bool(self.quizzes(slug, locale))

    async def set_enabled(self, kind: str, slug: str, enabled: bool) -> None:
        if kind not in {"category", "threat"}:
            raise ValueError(f"unknown content kind: {kind}")
        if self._factory is None:
            raise RuntimeError("set_enabled requires a session factory")
        model = Category if kind == "category" else Threat
        async with self._factory() as session:
            await session.execute(
                update(model).where(model.slug == slug).values(enabled=enabled)
            )
            await session.commit()
        await self.load()

    def _resolve(self, slug: str, locale: str) -> tuple[TextRow | None, bool]:
        text = self._texts.get((slug, locale))
        if text is not None:
            return text, False
        if locale != DEFAULT_LOCALE:
            fallback = self._texts.get((slug, DEFAULT_LOCALE))
            if fallback is not None:
                return fallback, True
        return None, False

    def _alias_hits(
        self, needle: str, tokens: Sequence[str], locale: str
    ) -> set[str]:
        hits: set[str] = set()
        locales = {locale, DEFAULT_LOCALE}
        for code, aliases in self._aliases.items():
            if code not in locales:
                continue
            for alias, slugs in aliases.items():
                if not alias:
                    continue
                words = _tokens(alias)
                if alias in needle or any(
                    _token_match(token, word)
                    for token in tokens
                    for word in words
                ):
                    hits.update(slugs)
        return hits

    @staticmethod
    def _category_title(category: CategoryRow, locale: str) -> str:
        title = category.titles.get(locale) or category.titles.get(DEFAULT_LOCALE)
        if title:
            return title
        return next(iter(category.titles.values()), category.slug)


__all__ = [
    "CategoryRow",
    "ContentRepository",
    "QuizRow",
    "TextRow",
    "ThreatRow",
]