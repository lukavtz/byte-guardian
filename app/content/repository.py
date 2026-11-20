"""In-memory content index backed by Postgres, with locale fallback to ``ru``."""

from __future__ import annotations

import json
import re
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.content.types import CardView, CategoryView, QuizView, SourceCitation, ThreatView
from app.db.content import Category, QuizQuestion, Threat, ThreatText

DEFAULT_LOCALE = "ru"
ALIASES_PATH = Path("content/aliases.json")
_TOKEN_RE = re.compile(r"\w+")
STOP_WORDS: frozenset[str] = frozenset(
    {"что", "делать", "если", "при", "как", "мне", "нужно", "можно", "бот"}
)


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
    updated: str | None = None


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
    quick: str = ""
    mistakes: tuple[str, ...] = ()
    citations: tuple[SourceCitation, ...] = ()


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


@dataclass(frozen=True, slots=True)
class _Doc:
    """One searchable threat text, pre-tokenized at load time."""

    slug: str
    locale: str
    title: str
    summary: str
    title_cf: str
    title_tokens: frozenset[str]
    summary_tokens: frozenset[str]
    severity: int
    category_slug: str
    category_title: str


@dataclass(frozen=True, slots=True)
class _Alias:
    text: str
    tokens: frozenset[str]
    slugs: tuple[str, ...]


@dataclass(slots=True)
class SearchResult:
    """Search hits plus the stop-word-reduced query that produced them."""

    items: list[ThreatView]
    relaxed_query: str | None = None


def _matches(token: str, words: frozenset[str]) -> bool:
    return any(_token_match(token, word) for word in words)


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


def _citation(value: object) -> SourceCitation:
    if isinstance(value, SourceCitation):
        return value
    if isinstance(value, dict):
        return SourceCitation(org=str(value["org"]), url=str(value["url"]))
    raise TypeError(f"unsupported citation: {value!r}")


def _text_row(row: ThreatText) -> TextRow:
    return TextRow(
        threat_slug=row.threat_slug,
        locale=row.locale,
        title=row.title,
        summary=row.summary,
        quick=row.quick or "",
        mistakes=tuple(row.mistakes or ()),
        citations=tuple(_citation(value) for value in row.citations or ()),
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
                updated=str(row.updated) if row.updated is not None else None,
            )
            for row in threats
        ],
        [
            TextRow(
                threat_slug=row.threat_slug,
                locale=row.locale,
                title=row.title,
                summary=row.summary,
                quick=row.quick or "",
                mistakes=tuple(row.mistakes or ()),
                citations=tuple(_citation(value) for value in row.citations or ()),
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
        self._index: dict[str, tuple[_Doc, ...]] = {}
        self._ordered: dict[tuple[str, str], tuple[_Doc, ...]] = {}
        self._category_views: tuple[CategoryView, ...] = ()
        self._alias_entries: dict[str, tuple[_Alias, ...]] = {}

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
        self._warm_index()

    def _warm_index(self) -> None:
        """Precompute tokens, per-locale docs, category order and alias tables."""
        locales = {row.locale for row in self._texts.values()} | {DEFAULT_LOCALE}
        index: dict[str, tuple[_Doc, ...]] = {}
        ordered: dict[tuple[str, str], tuple[_Doc, ...]] = {}
        for locale in sorted(locales):
            docs: list[_Doc] = []
            for threat in self._threats.values():
                if not threat.enabled:
                    continue
                category = self._categories.get(threat.category_slug)
                if category is None or not category.enabled:
                    continue
                text, _ = self._resolve(threat.slug, locale)
                if text is None:
                    continue
                docs.append(
                    _Doc(
                        slug=threat.slug,
                        locale=text.locale,
                        title=text.title,
                        summary=text.summary,
                        title_cf=text.title.casefold(),
                        title_tokens=frozenset(_tokens(text.title)),
                        summary_tokens=frozenset(_tokens(text.summary)),
                        severity=threat.severity,
                        category_slug=category.slug,
                        category_title=self._category_title(
                            category, text.locale
                        ),
                    )
                )
            index[locale] = tuple(docs)
            grouped: dict[str, list[_Doc]] = {}
            for doc in docs:
                grouped.setdefault(doc.category_slug, []).append(doc)
            for category_slug, bucket in grouped.items():
                ordered[(category_slug, locale)] = tuple(
                    sorted(
                        bucket,
                        key=lambda doc: (
                            -doc.severity,
                            doc.title.casefold(),
                            doc.slug,
                        ),
                    )
                )
        self._index = index
        self._ordered = ordered
        counts: dict[str, int] = {}
        for threat in self._threats.values():
            if threat.enabled:
                counts[threat.category_slug] = (
                    counts.get(threat.category_slug, 0) + 1
                )
        self._category_views = tuple(
            sorted(
                (
                    CategoryView(
                        slug=row.slug,
                        title=self._category_title(row, DEFAULT_LOCALE),
                        emoji=row.emoji,
                        sort=row.sort,
                        threat_count=counts.get(row.slug, 0),
                    )
                    for row in self._categories.values()
                    if row.enabled
                ),
                key=lambda view: (view.sort, view.slug),
            )
        )
        self._alias_entries = {
            locale: tuple(
                _Alias(
                    text=alias,
                    tokens=frozenset(_tokens(alias)),
                    slugs=tuple(sorted(slugs)),
                )
                for alias, slugs in sorted(aliases.items())
                if alias
            )
            for locale, aliases in self._aliases.items()
        }

    def _docs_for(self, locale: str) -> tuple[_Doc, ...]:
        docs = self._index.get(locale)
        if docs is not None:
            return docs
        return self._index.get(DEFAULT_LOCALE, ())

    def _view(self, doc: _Doc, locale: str) -> ThreatView:
        return ThreatView(
            slug=doc.slug,
            category_slug=doc.category_slug,
            category_title=doc.category_title,
            title=doc.title,
            summary=doc.summary,
            severity=doc.severity,
            locale=doc.locale,
            fallback=doc.locale != locale,
        )

    def categories(self) -> list[CategoryView]:
        return list(self._category_views)

    def threats(self, category_slug: str, locale: str) -> list[ThreatView]:
        category = self._categories.get(category_slug)
        if category is None or not category.enabled:
            return []
        docs = self._ordered.get((category_slug, locale))
        if docs is None:
            docs = self._ordered.get((category_slug, DEFAULT_LOCALE), ())
        return [self._view(doc, locale) for doc in docs]

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
            quick=text.quick,
            mistakes=text.mistakes,
            citations=text.citations,
            updated=threat.updated,
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
        """Backwards-compatible list of hits; ``find`` also reports relaxation."""
        return self.find(query, locale, limit).items

    def find(self, query: str, locale: str, limit: int = 10) -> SearchResult:
        """Rank hits; retry without stop words when the strict pass is empty."""
        needle = query.strip().casefold()
        if not needle:
            return SearchResult([])
        tokens = tuple(dict.fromkeys(_tokens(needle)))
        docs = self._docs_for(locale)
        ranked = self._rank(docs, tokens, needle, locale)
        if ranked:
            return SearchResult(
                [self._view(doc, locale) for doc in ranked[:limit]]
            )
        relaxed_tokens = tuple(
            token for token in tokens if token not in STOP_WORDS
        )
        if not relaxed_tokens or relaxed_tokens == tokens:
            return SearchResult([])
        relaxed = " ".join(relaxed_tokens)
        ranked = self._rank(docs, relaxed_tokens, relaxed, locale)
        if not ranked:
            return SearchResult([])
        return SearchResult(
            [self._view(doc, locale) for doc in ranked[:limit]], relaxed
        )

    def _rank(
        self,
        docs: Sequence[_Doc],
        tokens: Sequence[str],
        needle: str,
        locale: str,
    ) -> list[_Doc]:
        alias_tokens = self._alias_tokens(needle, tokens, locale)
        scored: list[tuple[tuple[int, int, str, str], _Doc]] = []
        for doc in docs:
            title_hits = {
                token for token in tokens if _matches(token, doc.title_tokens)
            }
            summary_hits = {
                token
                for token in tokens
                if _matches(token, doc.summary_tokens)
            }
            alias_hits = alias_tokens.get(doc.slug, frozenset())
            if len(title_hits | summary_hits | alias_hits) < len(tokens):
                continue
            if doc.title_cf == needle:
                rank = 0
            elif title_hits:
                rank = 1
            elif alias_hits:
                rank = 2
            else:
                rank = 3
            scored.append(
                ((rank, -doc.severity, doc.title.casefold(), doc.slug), doc)
            )
        scored.sort(key=lambda item: item[0])
        return [doc for _, doc in scored]

    def _alias_tokens(
        self, needle: str, tokens: Sequence[str], locale: str
    ) -> dict[str, frozenset[str]]:
        locales = {locale, DEFAULT_LOCALE}
        hits: dict[str, set[str]] = {}
        for code, entries in self._alias_entries.items():
            if code not in locales:
                continue
            for entry in entries:
                if entry.text in needle:
                    matched = set(tokens)
                else:
                    matched = {
                        token
                        for token in tokens
                        if any(
                            _token_match(token, word)
                            for word in entry.tokens
                        )
                    }
                if not matched:
                    continue
                for slug in entry.slugs:
                    hits.setdefault(slug, set()).update(matched)
        return {slug: frozenset(values) for slug, values in hits.items()}

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
    "STOP_WORDS",
    "SearchResult",
    "TextRow",
    "ThreatRow",
]