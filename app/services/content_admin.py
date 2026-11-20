"""Admin content editing: read the catalogue, validate, save, reload the index.

Every write bumps ``admin_version`` and stamps ``edited_at`` so the seed's
upsert skips the row: an edit made in the panel outlives ``content/*.json``.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.content.repository import ContentRepository

DEFAULT_LOCALE = "ru"
LOCALES = ("ru", "en", "zh", "tt")
SLUG_PATTERN = re.compile(r"^[a-z0-9-]{3,32}$")
MAX_SUMMARY = 300
MAX_ITEM = 160
MAX_TITLE = 200
MAX_SOURCE = 1000
MIN_ITEMS = 2
MAX_ITEMS = 5
# A card's phone list can hold one number; content lists start at two.
MIN_CONTACTS = 1

LIST_FIELDS = ("signs", "before", "during", "after", "forbidden", "contacts")
TEXT_FIELDS = ("title", "summary", "source")
EDIT_FIELDS = (*TEXT_FIELDS, *LIST_FIELDS)

CATEGORIES_SQL = text(
    """
    SELECT c.slug, c.emoji, c.enabled, c.titles,
           (SELECT count(*) FROM threat t WHERE t.category_slug = c.slug) AS total,
           (SELECT count(*) FROM threat t
             WHERE t.category_slug = c.slug AND t.enabled) AS active
      FROM category c
     ORDER BY c.sort, c.slug
    """
)
THREATS_SQL = text(
    """
    SELECT t.slug, t.severity, t.enabled,
           COALESCE(
             (SELECT tt.title FROM threat_text tt
               WHERE tt.threat_slug = t.slug AND tt.locale = 'ru'),
             t.slug
           ) AS title
      FROM threat t
     WHERE t.category_slug = :category
     ORDER BY t.severity DESC, t.sort, t.slug
    """
)
THREAT_SQL = text(
    """
    SELECT t.slug, t.category_slug, t.severity, t.enabled, c.emoji
      FROM threat t JOIN category c ON c.slug = t.category_slug
     WHERE t.slug = :slug
    """
)
TEXTS_SQL = text(
    "SELECT * FROM threat_text WHERE threat_slug = :slug ORDER BY locale"
)
CATEGORY_EXISTS_SQL = text("SELECT 1 FROM category WHERE slug = :slug")
THREAT_EXISTS_SQL = text("SELECT 1 FROM threat WHERE slug = :slug")
NEXT_SORT_SQL = text(
    "SELECT COALESCE(max(sort), -1) + 1 FROM threat WHERE category_slug = :category"
)
INSERT_THREAT_SQL = text(
    """
    INSERT INTO threat (slug, category_slug, severity, sort, edited_at)
    VALUES (:slug, :category, :severity, :sort, now())
    """
)
INSERT_TEXT_SQL = text(
    """
    INSERT INTO threat_text
      (threat_slug, locale, title, summary, signs, before, during, after,
       forbidden, contacts, source, admin_version, edited_at)
    VALUES
      (:slug, :locale, :title, :summary,
       '[]'::jsonb, '[]'::jsonb, '[]'::jsonb, '[]'::jsonb,
       '[]'::jsonb, '[]'::jsonb, '', 1, now())
    """
)
TEXT_UPSERT_SQL = text(
    """
    INSERT INTO threat_text
      (threat_slug, locale, title, summary, signs, before, during, after,
       forbidden, contacts, source, admin_version, edited_at)
    VALUES
      (:slug, :locale, :title, :summary,
       CAST(:signs AS jsonb), CAST(:before AS jsonb), CAST(:during AS jsonb),
       CAST(:after AS jsonb), CAST(:forbidden AS jsonb), CAST(:contacts AS jsonb),
       :source, 1, now())
    ON CONFLICT (threat_slug, locale) DO UPDATE SET
      title = excluded.title,
      summary = excluded.summary,
      signs = excluded.signs,
      before = excluded.before,
      during = excluded.during,
      after = excluded.after,
      forbidden = excluded.forbidden,
      contacts = excluded.contacts,
      source = excluded.source,
      admin_version = threat_text.admin_version + 1,
      edited_at = now()
    """
)
CATEGORY_SAVE_SQL = text(
    """
    UPDATE category
       SET titles = jsonb_set(titles, '{ru}', to_jsonb(CAST(:title AS text)), true),
           emoji = :emoji,
           enabled = :enabled,
           edited_at = now()
     WHERE slug = :slug
    RETURNING slug
    """
)
ENABLED_SQL = text(
    """
    UPDATE threat SET enabled = :enabled, edited_at = now()
     WHERE slug = :slug RETURNING enabled
    """
)


class ContentError(ValueError):
    """A patch the panel can explain to the admin, never a 500."""


@dataclass(frozen=True, slots=True)
class CategoryItem:
    slug: str
    title: str
    emoji: str
    enabled: bool
    total: int
    active: int


@dataclass(frozen=True, slots=True)
class ThreatItem:
    slug: str
    title: str
    severity: int
    enabled: bool


@dataclass(frozen=True, slots=True)
class ThreatDetail:
    slug: str
    category_slug: str
    severity: int
    enabled: bool
    locale: str
    locales: tuple[str, ...]
    exact: bool
    title: str
    summary: str
    signs: tuple[str, ...]
    before: tuple[str, ...]
    during: tuple[str, ...]
    after: tuple[str, ...]
    forbidden: tuple[str, ...]
    contacts: tuple[str, ...]
    source: str


def _title(titles: Any, slug: str) -> str:
    if isinstance(titles, dict):
        value = titles.get(DEFAULT_LOCALE) or next(iter(titles.values()), "")
        if isinstance(value, str) and value.strip():
            return value
    return slug


def _json(values: tuple[str, ...]) -> str:
    return json.dumps(list(values), ensure_ascii=False)


def _single(value: object, field: str, limit: int) -> str:
    if not isinstance(value, str):
        raise ContentError(f"{field}: ожидается текст")
    clean = value.strip()
    if not clean:
        raise ContentError(f"{field}: пустое значение")
    if len(clean) > limit:
        raise ContentError(f"{field}: не больше {limit} символов")
    return clean


def _lines(value: object, field: str, minimum: int = MIN_ITEMS) -> tuple[str, ...]:
    if not isinstance(value, (list, tuple)):
        raise ContentError(f"{field}: ожидается список")
    clean = tuple(str(item).strip() for item in value if str(item).strip())
    if not minimum <= len(clean) <= MAX_ITEMS:
        raise ContentError(
            f"{field}: нужно от {minimum} до {MAX_ITEMS} пунктов"
        )
    for item in clean:
        if len(item) > MAX_ITEM:
            raise ContentError(f"{field}: пункт длиннее {MAX_ITEM} символов")
    return clean


def validate_patch(patch: dict[str, Any]) -> dict[str, Any]:
    """Only known fields, each with the catalogue's own limits."""
    unknown = set(patch) - set(EDIT_FIELDS)
    if unknown:
        raise ContentError("Неизвестное поле: " + ", ".join(sorted(unknown)))
    clean: dict[str, Any] = {}
    for field, value in patch.items():
        if field == "summary":
            clean[field] = _single(value, "Кратко", MAX_SUMMARY)
        elif field == "title":
            clean[field] = _single(value, "Название", MAX_TITLE)
        elif field == "source":
            clean[field] = _single(value, "Источник", MAX_SOURCE)
        else:
            minimum = MIN_CONTACTS if field == "contacts" else MIN_ITEMS
            clean[field] = _lines(value, field, minimum)
    return clean


def parse_field_input(field: str, raw: str) -> str | tuple[str, ...]:
    """One chat message -> the value a field stores."""
    if field in LIST_FIELDS:
        separator = "," if field == "contacts" else "\n"
        parts = tuple(part.strip() for part in raw.split(separator))
        return tuple(part for part in parts if part)
    return raw.strip()


async def list_categories(
    session_factory: async_sessionmaker[AsyncSession],
) -> list[CategoryItem]:
    async with session_factory() as session:
        rows = (await session.execute(CATEGORIES_SQL)).mappings().all()
    return [
        CategoryItem(
            slug=str(row["slug"]),
            title=_title(row["titles"], str(row["slug"])),
            emoji=str(row["emoji"]),
            enabled=bool(row["enabled"]),
            total=int(row["total"]),
            active=int(row["active"]),
        )
        for row in rows
    ]


async def list_threats(
    session_factory: async_sessionmaker[AsyncSession], category: str
) -> list[ThreatItem]:
    async with session_factory() as session:
        rows = (
            await session.execute(THREATS_SQL, {"category": category})
        ).mappings().all()
    return [
        ThreatItem(
            slug=str(row["slug"]),
            title=str(row["title"]),
            severity=int(row["severity"]),
            enabled=bool(row["enabled"]),
        )
        for row in rows
    ]


def _detail(
    threat: Any,
    locale: str,
    texts: dict[str, dict[str, Any]],
) -> ThreatDetail:
    locales = tuple(
        code for code in (*LOCALES, *sorted(texts)) if code in texts
    )
    locales = tuple(dict.fromkeys(locales))
    row = texts.get(locale) or texts.get(DEFAULT_LOCALE)
    if row is None:
        row = {
            "title": str(threat["slug"]),
            "summary": "",
            "signs": [],
            "before": [],
            "during": [],
            "after": [],
            "forbidden": [],
            "contacts": [],
            "source": "",
        }
    return ThreatDetail(
        slug=str(threat["slug"]),
        category_slug=str(threat["category_slug"]),
        severity=int(threat["severity"]),
        enabled=bool(threat["enabled"]),
        locale=locale,
        locales=locales or (DEFAULT_LOCALE,),
        exact=locale in texts,
        title=str(row["title"]),
        summary=str(row["summary"]),
        signs=tuple(row["signs"] or ()),
        before=tuple(row["before"] or ()),
        during=tuple(row["during"] or ()),
        after=tuple(row["after"] or ()),
        forbidden=tuple(row["forbidden"] or ()),
        contacts=tuple(row["contacts"] or ()),
        source=str(row["source"] or ""),
    )


async def get_threat(
    session_factory: async_sessionmaker[AsyncSession],
    slug: str,
    locale: str = DEFAULT_LOCALE,
) -> ThreatDetail | None:
    locale = locale if locale in LOCALES else DEFAULT_LOCALE
    async with session_factory() as session:
        threat = (await session.execute(THREAT_SQL, {"slug": slug})).mappings().first()
        if threat is None:
            return None
        rows = (await session.execute(TEXTS_SQL, {"slug": slug})).mappings().all()
    return _detail(threat, locale, {str(row["locale"]): dict(row) for row in rows})


async def _reload(repo: ContentRepository | None) -> None:
    if repo is not None:
        await repo.load()


async def save_threat(
    session_factory: async_sessionmaker[AsyncSession],
    repo: ContentRepository | None,
    slug: str,
    locale: str,
    patch: dict[str, Any],
) -> ThreatDetail:
    """Validate the whole row, write it, and rebuild the in-memory index."""
    if locale not in LOCALES:
        raise ContentError("Неизвестный язык.")
    detail = await get_threat(session_factory, slug, locale)
    if detail is None:
        raise ContentError("Карточка не найдена.")
    clean = validate_patch(patch)
    merged = {
        "title": detail.title,
        "summary": detail.summary,
        "signs": detail.signs,
        "before": detail.before,
        "during": detail.during,
        "after": detail.after,
        "forbidden": detail.forbidden,
        "contacts": detail.contacts,
        "source": detail.source,
        **clean,
    }
    # The patched field was validated above; untouched fields keep whatever
    # the row already holds (a freshly created stub has empty lists on purpose).
    checked = {**merged, **clean}
    if not str(checked["title"]).strip():
        raise ContentError("Название: пустое значение.")
    async with session_factory() as session:
        await session.execute(
            TEXT_UPSERT_SQL,
            {
                "slug": slug,
                "locale": locale,
                "title": checked["title"],
                "summary": checked["summary"],
                "signs": _json(checked["signs"]),
                "before": _json(checked["before"]),
                "during": _json(checked["during"]),
                "after": _json(checked["after"]),
                "forbidden": _json(checked["forbidden"]),
                "contacts": _json(checked["contacts"]),
                "source": checked["source"],
            },
        )
        await session.commit()
    await _reload(repo)
    saved = await get_threat(session_factory, slug, locale)
    assert saved is not None
    return saved


async def save_category(
    session_factory: async_sessionmaker[AsyncSession],
    repo: ContentRepository | None,
    slug: str,
    title: str,
    emoji: str,
    enabled: bool,
) -> None:
    clean_title = _single(title, "Название", MAX_TITLE)
    clean_emoji = _single(emoji, "Значок", 8)
    async with session_factory() as session:
        result = await session.execute(
            CATEGORY_SAVE_SQL,
            {
                "slug": slug,
                "title": clean_title,
                "emoji": clean_emoji,
                "enabled": enabled,
            },
        )
        if result.first() is None:
            raise ContentError("Раздел не найден.")
        await session.commit()
    await _reload(repo)


async def set_threat_enabled(
    session_factory: async_sessionmaker[AsyncSession],
    repo: ContentRepository | None,
    slug: str,
    enabled: bool,
) -> bool:
    async with session_factory() as session:
        row = (
            await session.execute(ENABLED_SQL, {"slug": slug, "enabled": enabled})
        ).first()
        await session.commit()
    if row is None:
        raise ContentError("Карточка не найдена.")
    await _reload(repo)
    return bool(row[0])


async def delete_threat(
    session_factory: async_sessionmaker[AsyncSession],
    repo: ContentRepository | None,
    slug: str,
) -> bool:
    """Soft delete: the row and its quizzes stay, the card disappears."""
    return await set_threat_enabled(session_factory, repo, slug, False)


async def restore_threat(
    session_factory: async_sessionmaker[AsyncSession],
    repo: ContentRepository | None,
    slug: str,
) -> bool:
    return await set_threat_enabled(session_factory, repo, slug, True)


async def create_threat(
    session_factory: async_sessionmaker[AsyncSession],
    repo: ContentRepository | None,
    category_slug: str,
    slug: str,
    severity: int,
    title: str = "",
) -> ThreatDetail:
    """A new card with an empty Russian text, ready for the same editor."""
    if not SLUG_PATTERN.fullmatch(slug):
        raise ContentError("Slug: 3–32 символа a-z, 0-9 и дефис.")
    if isinstance(severity, bool) or not 1 <= severity <= 3:
        raise ContentError("Важность: от 1 до 3.")
    clean_title = _single(title, "Название", MAX_TITLE) if title.strip() else slug
    async with session_factory() as session:
        if (await session.execute(CATEGORY_EXISTS_SQL, {"slug": category_slug})).first() is None:
            raise ContentError("Раздел не найден.")
        if (await session.execute(THREAT_EXISTS_SQL, {"slug": slug})).first() is not None:
            raise ContentError("Такой slug уже занят.")
        sort = (await session.execute(NEXT_SORT_SQL, {"category": category_slug})).scalar()
        await session.execute(
            INSERT_THREAT_SQL,
            {
                "slug": slug,
                "category": category_slug,
                "severity": severity,
                "sort": int(sort or 0),
            },
        )
        await session.execute(
            INSERT_TEXT_SQL,
            {
                "slug": slug,
                "locale": DEFAULT_LOCALE,
                "title": clean_title,
                "summary": "",
            },
        )
        await session.commit()
    await _reload(repo)
    detail = await get_threat(session_factory, slug, DEFAULT_LOCALE)
    assert detail is not None
    return detail


__all__ = [
    "CategoryItem",
    "ContentError",
    "DEFAULT_LOCALE",
    "EDIT_FIELDS",
    "LIST_FIELDS",
    "LOCALES",
    "MAX_ITEM",
    "MAX_SUMMARY",
    "SLUG_PATTERN",
    "ThreatDetail",
    "ThreatItem",
    "create_threat",
    "delete_threat",
    "get_threat",
    "list_categories",
    "list_threats",
    "parse_field_input",
    "restore_threat",
    "save_category",
    "save_threat",
    "set_threat_enabled",
    "validate_patch",
]