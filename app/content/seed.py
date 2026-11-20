"""Idempotent content seeding from ``content/*.json`` into Postgres."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from sqlalchemy import Insert, func, select
from sqlalchemy.dialects import postgresql
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.db.content import Category, QuizQuestion, Threat, ThreatText


@dataclass(slots=True)
class SeedReport:
    files: int = 0
    threats: int = 0
    quizzes: int = 0
    # Rows an admin edited: the upsert's WHERE keeps them as they are.
    skipped: int = 0
    errors: list[str] = field(default_factory=list)


def category_upsert() -> Insert:
    stmt = postgresql.insert(Category)
    excluded = stmt.excluded
    return stmt.on_conflict_do_update(
        index_elements=[Category.slug],
        set_={
            "sort": excluded.sort,
            "emoji": excluded.emoji,
            "titles": excluded.titles,
        },
        where=Category.edited_at.is_(None),
    )


def threat_upsert() -> Insert:
    stmt = postgresql.insert(Threat)
    excluded = stmt.excluded
    return stmt.on_conflict_do_update(
        index_elements=[Threat.slug],
        set_={
            "category_slug": excluded.category_slug,
            "severity": excluded.severity,
            "sort": excluded.sort,
            "content_version": excluded.content_version,
        },
        where=excluded.content_version > Threat.content_version,
    )


def threat_text_upsert() -> Insert:
    stmt = postgresql.insert(ThreatText)
    excluded = stmt.excluded
    return stmt.on_conflict_do_update(
        index_elements=[ThreatText.threat_slug, ThreatText.locale],
        set_={
            "title": excluded.title,
            "summary": excluded.summary,
            "signs": excluded.signs,
            "before": excluded.before,
            "during": excluded.during,
            "after": excluded.after,
            "forbidden": excluded.forbidden,
            "contacts": excluded.contacts,
            "source": excluded.source,
            "content_version": excluded.content_version,
        },
        where=(excluded.content_version > ThreatText.content_version)
        & (ThreatText.admin_version == 0),
    )


def quiz_upsert() -> Insert:
    stmt = postgresql.insert(QuizQuestion)
    excluded = stmt.excluded
    return stmt.on_conflict_do_update(
        index_elements=[
            QuizQuestion.threat_slug,
            QuizQuestion.question_id,
            QuizQuestion.locale,
        ],
        set_={
            "prompt": excluded.prompt,
            "options": excluded.options,
            "answer": excluded.answer,
            "explanation": excluded.explanation,
            "content_version": excluded.content_version,
        },
        where=(excluded.content_version > QuizQuestion.content_version)
        & (QuizQuestion.admin_version == 0),
    )


def _lines(value: object, name: str) -> list[str]:
    if value is None:
        return []
    if not isinstance(value, list) or not all(isinstance(v, str) for v in value):
        raise ValueError(f"{name}: expected list of strings")
    return list(value)


def _require(value: object, name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name}: expected non-empty string")
    return value


def _extract(
    doc: dict[str, Any],
    rows: dict[str, list[dict[str, Any]]],
) -> tuple[int, int]:
    version = doc.get("version", 0)
    if not isinstance(version, int):
        raise ValueError("version: expected integer")
    category = doc.get("category")
    if not isinstance(category, dict):
        raise ValueError("category: expected object")
    slug = _require(category.get("slug"), "category.slug")
    titles = category.get("title", {})
    if not isinstance(titles, dict) or not all(
        isinstance(k, str) and isinstance(v, str) for k, v in titles.items()
    ):
        raise ValueError("category.title: expected object of locale -> string")
    category_slug = slug
    rows["categories"].append(
        {
            "slug": slug,
            "sort": int(category.get("sort", 0)),
            "emoji": category.get("emoji") or "•",
            "titles": titles,
        }
    )

    threats = doc.get("threats", [])
    if not isinstance(threats, list):
        raise ValueError("threats: expected list")
    threat_count = 0
    quiz_count = 0
    for index, threat in enumerate(threats):
        if not isinstance(threat, dict):
            raise ValueError(f"threats[{index}]: expected object")
        threat_slug = _require(threat.get("slug"), f"threats[{index}].slug")
        severity = threat.get("severity")
        if not isinstance(severity, int) or severity < 1:
            raise ValueError(f"{threat_slug}.severity: expected integer >= 1")
        rows["threats"].append(
            {
                "slug": threat_slug,
                "category_slug": category_slug,
                "severity": severity,
                "sort": index,
                "content_version": version,
            }
        )
        threat_count += 1

        texts = threat.get("texts")
        if not isinstance(texts, dict) or not texts:
            raise ValueError(f"{threat_slug}.texts: expected locale map")
        for locale, text in texts.items():
            if not isinstance(locale, str) or not isinstance(text, dict):
                raise ValueError(f"{threat_slug}.texts: malformed locale entry")
            prefix = f"{threat_slug}.{locale}"
            rows["texts"].append(
                {
                    "threat_slug": threat_slug,
                    "locale": locale,
                    "title": _require(text.get("title"), f"{prefix}.title"),
                    "summary": _require(
                        text.get("summary"), f"{prefix}.summary"
                    ),
                    "signs": _lines(text.get("signs"), f"{prefix}.signs"),
                    "before": _lines(text.get("before"), f"{prefix}.before"),
                    "during": _lines(text.get("during"), f"{prefix}.during"),
                    "after": _lines(text.get("after"), f"{prefix}.after"),
                    "forbidden": _lines(
                        text.get("forbidden"), f"{prefix}.forbidden"
                    ),
                    "contacts": _lines(
                        text.get("contacts"), f"{prefix}.contacts"
                    ),
                    "source": text.get("source") or "",
                    "content_version": version,
                }
            )

        quiz = threat.get("quiz") or {}
        if not isinstance(quiz, dict):
            raise ValueError(f"{threat_slug}.quiz: expected locale map")
        for locale, questions in quiz.items():
            if not isinstance(questions, list):
                raise ValueError(f"{threat_slug}.quiz.{locale}: expected list")
            for number, question in enumerate(questions, start=1):
                if not isinstance(question, dict):
                    raise ValueError(
                        f"{threat_slug}.quiz.{locale}[{number}]: expected object"
                    )
                options = _lines(
                    question.get("options"),
                    f"{threat_slug}.quiz.{locale}[{number}].options",
                )
                answer = question.get("answer")
                if not isinstance(answer, int) or not 0 <= answer < len(options):
                    raise ValueError(
                        f"{threat_slug}.quiz.{locale}[{number}].answer: out of range"
                    )
                rows["quizzes"].append(
                    {
                        "threat_slug": threat_slug,
                        "question_id": str(question.get("id") or f"q{number}"),
                        "locale": locale,
                        "prompt": _require(
                            question.get("prompt"),
                            f"{threat_slug}.quiz.{locale}[{number}].prompt",
                        ),
                        "options": options,
                        "answer": answer,
                        "explanation": question.get("explanation") or "",
                        "content_version": version,
                    }
                )
                quiz_count += 1
    return threat_count, quiz_count


def _read(path: Path) -> tuple[list[dict[str, Any]], list[str]]:
    docs: list[dict[str, Any]] = []
    errors: list[str] = []
    if not path.is_dir():
        errors.append(f"{path}: content directory not found")
        return docs, errors
    for file in sorted(path.glob("*.json")):
        if file.name == "aliases.json":
            continue
        try:
            doc = json.loads(file.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            errors.append(f"{file.name}: {exc}")
            continue
        if not isinstance(doc, dict):
            errors.append(f"{file.name}: root must be an object")
            continue
        docs.append(doc)
    return docs, errors


async def seed_content(
    session_factory: async_sessionmaker[AsyncSession], path: Path
) -> SeedReport:
    """Upsert every ``*.json`` under ``path``; one bad file never stops the rest."""
    docs, errors = _read(path)
    report = SeedReport(errors=errors)
    rows: dict[str, list[dict[str, Any]]] = {
        "categories": [],
        "threats": [],
        "texts": [],
        "quizzes": [],
    }
    for doc in docs:
        scratch: dict[str, list[dict[str, Any]]] = {
            "categories": [],
            "threats": [],
            "texts": [],
            "quizzes": [],
        }
        try:
            threats, quizzes = _extract(doc, scratch)
        except (TypeError, ValueError) as exc:
            category = doc.get("category")
            slug = category.get("slug", "?") if isinstance(category, dict) else "?"
            errors.append(f"{slug}: {exc}")
            continue
        for key, extra in scratch.items():
            rows[key].extend(extra)
        report.files += 1
        report.threats += threats
        report.quizzes += quizzes

    statements = (
        ("categories", category_upsert()),
        ("threats", threat_upsert()),
        ("texts", threat_text_upsert()),
        ("quizzes", quiz_upsert()),
    )
    async with session_factory() as session:
        for key, statement in statements:
            if not rows[key]:
                continue
            await session.execute(statement, rows[key])
        await session.commit()
        # The upsert silently keeps edited rows; report how many so a reseed
        # never looks like it replaced the owner's text. COUNT runs after the
        # upsert because edited rows are exactly the ones it did not touch.
        for model, guard in (
            (Category, Category.edited_at.is_not(None)),
            (ThreatText, ThreatText.admin_version > 0),
            (QuizQuestion, QuizQuestion.admin_version > 0),
        ):
            result = await session.execute(
                select(func.count()).select_from(model).where(guard)
            )
            if result is not None:
                report.skipped += int(result.scalar() or 0)
    return report