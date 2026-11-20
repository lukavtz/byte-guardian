from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy.dialects import postgresql

from app.content.seed import (
    SeedReport,
    _extract,
    category_upsert,
    quiz_upsert,
    seed_content,
    threat_text_upsert,
    threat_upsert,
)


class FakeDB:
    def __init__(self) -> None:
        self.tables: dict[str, dict[tuple[str, ...], dict[str, Any]]] = {
            "category": {},
            "threat": {},
            "threat_text": {},
            "quiz_question": {},
        }

    @staticmethod
    def _key(table: str, row: dict[str, Any]) -> tuple[str, ...]:
        if table == "category":
            return (row["slug"],)
        if table == "threat":
            return (row["slug"],)
        if table == "threat_text":
            return (row["threat_slug"], row["locale"])
        return (row["threat_slug"], row["question_id"], row["locale"])

    async def apply(self, table: str, rows: list[dict[str, Any]]) -> None:
        store = self.tables[table]
        versioned = table != "category"
        for row in rows:
            key = self._key(table, row)
            current = store.get(key)
            if current is None:
                store[key] = {**row, "enabled": True, "admin_version": 0}
                continue
            if versioned and row["content_version"] <= current["content_version"]:
                continue
            if versioned and current.get("admin_version", 0) > 0:
                continue
            if table == "category" and current.get("edited_at"):
                continue
            merged = {**current, **row}
            merged["enabled"] = current["enabled"]
            store[key] = merged

    async def apply_backfill(
        self, table: str, column: str, rows: list[dict[str, Any]]
    ) -> None:
        store = self.tables[table]
        for row in rows:
            key = (
                (row["threat_slug"], row["locale"])
                if table == "threat_text"
                else (row["threat_slug"],)
            )
            current = store.get(key)
            if current is not None and current.get(column) is None:
                current[column] = row["value"]

    def count(self, table: str) -> int:
        return len(self.tables[table])


class _Count:
    def __init__(self, value: int) -> None:
        self.value = value

    def scalar(self) -> int:
        return self.value


class FakeSession:
    def __init__(self, db: FakeDB) -> None:
        self.db = db

    async def __aenter__(self) -> FakeSession:
        return self

    async def __aexit__(self, *exc: object) -> bool:
        return False

    async def execute(
        self, statement: Any, rows: list[dict[str, Any]] | None = None
    ) -> Any:
        if rows is not None:
            sql = " ".join(str(statement).split())
            if sql.upper().startswith("UPDATE"):
                match = re.search(r"SET\s+(\w+)\s*=", sql)
                assert match is not None, sql
                await self.db.apply_backfill(
                    statement.table.name, match.group(1), rows
                )
                return None
            await self.db.apply(statement.table.name, rows)
            return None
        sql = " ".join(str(statement).split())
        if "FROM category" in sql:
            edited = sum(
                1 for row in self.db.tables["category"].values() if row.get("edited_at")
            )
        else:
            table = "threat_text" if "threat_text" in sql else "quiz_question"
            edited = sum(
                1 for row in self.db.tables[table].values() if row.get("admin_version", 0)
            )
        return _Count(edited)

    async def commit(self) -> None:
        return None


def _write(directory: Path, name: str, doc: dict[str, Any]) -> None:
    (directory / name).write_text(json.dumps(doc, ensure_ascii=False), encoding="utf-8")


def _doc(*, version: int = 1, title: str = "Пожар в квартире") -> dict[str, Any]:
    return {
        "version": version,
        "category": {
            "slug": "fire",
            "emoji": "🔥",
            "sort": 1,
            "title": {"ru": "Пожар"},
        },
        "threats": [
            {
                "slug": "fire-home",
                "severity": 3,
                "updated": "2026-11-20",
                "texts": {
                    "ru": {
                        "title": title,
                        "summary": "Дым и огонь в квартире.",
                        "quick": "Звоните 101 и выходите",
                        "mistakes": ["Не открывайте окна → закройте дверь"],
                        "citations": [
                            {
                                "org": "МЧС России",
                                "url": "https://mchs.gov.ru/pozhar",
                            }
                        ],
                        "signs": ["Дым"],
                        "before": ["Датчик"],
                        "during": ["Звоните 101"],
                        "after": ["Не входите"],
                        "forbidden": ["Не открывайте окна"],
                        "contacts": ["101"],
                        "source": "МЧС России · https://mchs.gov.ru/pozhar",
                    }
                },
                "quiz": {
                    "ru": [
                        {
                            "id": "q1",
                            "prompt": "Что делать первым?",
                            "options": ["Вызвать 101", "Открыть окна"],
                            "answer": 0,
                            "explanation": "Сначала вызов.",
                        }
                    ]
                },
            }
        ],
    }


async def test_seed_twice_is_idempotent(tmp_path: Path) -> None:
    db = FakeDB()
    _write(tmp_path, "fire.json", _doc())
    factory = lambda: FakeSession(db)  # noqa: E731

    first = await seed_content(factory, tmp_path)
    second = await seed_content(factory, tmp_path)

    assert isinstance(first, SeedReport)
    assert first.files == second.files == 1
    assert first.threats == 1
    assert first.quizzes == 1
    assert first.errors == second.errors == []
    counts = {name: db.count(name) for name in db.tables}
    assert counts == {
        "category": 1,
        "threat": 1,
        "threat_text": 1,
        "quiz_question": 1,
    }


async def test_reseed_keeps_enabled_and_updates_newer_text(tmp_path: Path) -> None:
    db = FakeDB()
    _write(tmp_path, "fire.json", _doc(version=1, title="Старый заголовок"))
    factory = lambda: FakeSession(db)  # noqa: E731
    await seed_content(factory, tmp_path)

    db.tables["category"][("fire",)]["enabled"] = False
    db.tables["threat"][("fire-home",)]["enabled"] = False

    _write(tmp_path, "fire.json", _doc(version=2, title="Новый заголовок"))
    await seed_content(factory, tmp_path)

    assert db.tables["category"][("fire",)]["enabled"] is False
    assert db.tables["threat"][("fire-home",)]["enabled"] is False
    assert db.tables["threat_text"][("fire-home", "ru")]["title"] == "Новый заголовок"
    assert db.tables["threat_text"][("fire-home", "ru")]["content_version"] == 2


async def test_older_version_does_not_overwrite(tmp_path: Path) -> None:
    db = FakeDB()
    factory = lambda: FakeSession(db)  # noqa: E731
    _write(tmp_path, "fire.json", _doc(version=2, title="Новый заголовок"))
    await seed_content(factory, tmp_path)
    _write(tmp_path, "fire.json", _doc(version=1, title="Старый заголовок"))
    await seed_content(factory, tmp_path)

    assert db.tables["threat_text"][("fire-home", "ru")]["title"] == "Новый заголовок"


async def test_bad_file_is_reported_and_rest_still_seeds(tmp_path: Path) -> None:
    db = FakeDB()
    _write(tmp_path, "fire.json", _doc())
    (tmp_path / "broken.json").write_text("{not json", encoding="utf-8")
    factory = lambda: FakeSession(db)  # noqa: E731

    report = await seed_content(factory, tmp_path)

    assert report.files == 1
    assert report.threats == 1
    assert any("broken.json" in error for error in report.errors)
    assert db.count("threat") == 1


async def test_missing_directory_reports_error(tmp_path: Path) -> None:
    db = FakeDB()
    report = await seed_content(lambda: FakeSession(db), tmp_path / "nope")  # type: ignore[arg-type]

    assert report.files == 0
    assert report.errors


def _text_row(title: str = "План") -> dict[str, Any]:
    return {
        "title": title,
        "summary": "Дым и огонь.",
        "quick": "Вызовите 101",
        "mistakes": [],
        "citations": [{"org": "МЧС России", "url": "https://mchs.gov.ru/plan"}],
        "signs": ["Дым"],
        "before": ["Датчик"],
        "during": ["Звоните 101"],
        "after": ["Не входите"],
        "forbidden": ["Не открывайте окна"],
        "contacts": ["101"],
        "source": "МЧС России",
    }


async def test_admin_edited_text_survives_higher_seed_version(tmp_path: Path) -> None:
    db = FakeDB()
    factory = lambda: FakeSession(db)  # noqa: E731
    _write(tmp_path, "fire.json", _doc(version=1, title="Из JSON"))
    await seed_content(factory, tmp_path)

    edited = db.tables["threat_text"][("fire-home", "ru")]
    edited["title"] = "Из панели"
    edited["admin_version"] = 1

    _write(tmp_path, "fire.json", _doc(version=9, title="Новее JSON"))
    report = await seed_content(factory, tmp_path)

    assert db.tables["threat_text"][("fire-home", "ru")]["title"] == "Из панели"
    assert db.tables["threat_text"][("fire-home", "ru")]["content_version"] == 1
    assert report.skipped == 1


async def test_admin_edited_category_survives_reseed(tmp_path: Path) -> None:
    db = FakeDB()
    factory = lambda: FakeSession(db)  # noqa: E731
    _write(tmp_path, "fire.json", _doc(version=1))
    await seed_content(factory, tmp_path)

    db.tables["category"][("fire",)]["titles"] = {"ru": "Из панели"}
    db.tables["category"][("fire",)]["edited_at"] = "now"
    _write(tmp_path, "fire.json", _doc(version=2))
    report = await seed_content(factory, tmp_path)

    assert db.tables["category"][("fire",)]["titles"] == {"ru": "Из панели"}
    assert report.skipped == 1


async def test_untouched_rows_still_update_next_to_edited_ones(tmp_path: Path) -> None:
    db = FakeDB()
    factory = lambda: FakeSession(db)  # noqa: E731
    doc = _doc(version=1, title="Старое")
    doc["threats"].append(
        {
            "slug": "fire-plan",
            "severity": 1,
            "updated": "2026-11-20",
            "texts": {"ru": _text_row()},
        }
    )
    _write(tmp_path, "fire.json", doc)
    await seed_content(factory, tmp_path)

    db.tables["threat_text"][("fire-home", "ru")]["admin_version"] = 1
    doc["version"] = 2
    doc["threats"][0]["texts"]["ru"]["title"] = "Новое из JSON"
    doc["threats"][1]["texts"]["ru"]["title"] = "План v2"
    _write(tmp_path, "fire.json", doc)

    await seed_content(factory, tmp_path)

    assert db.tables["threat_text"][("fire-home", "ru")]["title"] == "Старое"
    assert db.tables["threat_text"][("fire-plan", "ru")]["title"] == "План v2"


def _sql(statement: Any) -> str:
    return str(statement.compile(dialect=postgresql.dialect()))


def test_versioned_upserts_guard_on_content_version() -> None:
    for statement, table in (
        (threat_upsert(), "threat"),
        (threat_text_upsert(), "threat_text"),
        (quiz_upsert(), "quiz_question"),
    ):
        sql = _sql(statement)
        assert "ON CONFLICT" in sql
        assert f"excluded.content_version > {table}.content_version" in sql


def test_category_upsert_updates_metadata_without_touching_enabled() -> None:
    sql = _sql(category_upsert())

    assert "ON CONFLICT (slug) DO UPDATE SET sort = excluded.sort" in sql
    assert "enabled" not in sql.split("DO UPDATE", maxsplit=1)[1]

async def test_rejected_document_commits_no_partial_rows(tmp_path: Path) -> None:
    db = FakeDB()
    doc = _doc()
    doc["threats"].append({"slug": "fire-bad", "severity": 0, "texts": {}})
    _write(tmp_path, "fire.json", doc)
    factory = lambda: FakeSession(db)  # noqa: E731

    report = await seed_content(factory, tmp_path)

    assert report.files == 0
    assert report.errors
    assert {name: db.count(name) for name in db.tables} == {
        "category": 0,
        "threat": 0,
        "threat_text": 0,
        "quiz_question": 0,
    }


async def test_non_object_category_is_reported_without_crashing(tmp_path: Path) -> None:
    db = FakeDB()
    _write(tmp_path, "bad.json", {"version": 1, "category": ["fire"], "threats": []})
    factory = lambda: FakeSession(db)  # noqa: E731

    report = await seed_content(factory, tmp_path)

    assert report.files == 0
    assert any("expected object" in error for error in report.errors)
    assert db.count("category") == 0


def _strip_enrichment(doc: dict[str, Any]) -> dict[str, Any]:
    return doc


def test_extracted_rows_map_enrichment_fields() -> None:
    rows: dict[str, list[dict[str, Any]]] = {
        "categories": [],
        "threats": [],
        "texts": [],
        "quizzes": [],
    }

    _extract(_doc(), rows)

    assert rows["threats"][0]["updated"] == "2026-11-20"
    text = rows["texts"][0]
    assert text["quick"] == "Звоните 101 и выходите"
    assert text["mistakes"] == ["Не открывайте окна → закройте дверь"]
    assert text["citations"] == [
        {"org": "МЧС России", "url": "https://mchs.gov.ru/pozhar"}
    ]


def test_enrichment_columns_are_in_versioned_upserts() -> None:
    text_sql = _sql(threat_text_upsert())
    threat_sql = _sql(threat_upsert())

    assert "quick = excluded.quick" in text_sql
    assert "mistakes = excluded.mistakes" in text_sql
    assert "citations = excluded.citations" in text_sql
    assert "updated = excluded.updated" in threat_sql


async def test_enrichment_is_persisted_on_new_rows(tmp_path: Path) -> None:
    db = FakeDB()
    _write(tmp_path, "fire.json", _doc(version=2))

    await seed_content(lambda: FakeSession(db), tmp_path)  # type: ignore[arg-type]

    row = db.tables["threat_text"][("fire-home", "ru")]
    assert row["quick"] == "Звоните 101 и выходите"
    assert row["mistakes"] == ["Не открывайте окна → закройте дверь"]
    assert row["citations"] == [
        {"org": "МЧС России", "url": "https://mchs.gov.ru/pozhar"}
    ]
    assert db.tables["threat"][("fire-home",)]["updated"] == "2026-11-20"


async def test_null_enrichment_backfills_admin_edited_text_without_touching_copy(
    tmp_path: Path,
) -> None:
    db = FakeDB()
    factory = lambda: FakeSession(db)  # noqa: E731
    _write(tmp_path, "fire.json", _strip_enrichment(_doc(version=1)))
    await seed_content(factory, tmp_path)

    edited = db.tables["threat_text"][("fire-home", "ru")]
    for key in ("quick", "mistakes", "citations"):
        edited[key] = None
    edited["summary"] = "Ручной текст"
    edited["admin_version"] = 1
    _write(tmp_path, "fire.json", _doc(version=2))

    report = await seed_content(factory, tmp_path)

    assert edited["summary"] == "Ручной текст"
    assert edited["content_version"] == 1
    assert edited["quick"] == "Звоните 101 и выходите"
    assert edited["mistakes"] == ["Не открывайте окна → закройте дверь"]
    assert edited["citations"] == [
        {"org": "МЧС России", "url": "https://mchs.gov.ru/pozhar"}
    ]
    assert report.skipped == 1


async def test_existing_enrichment_survives_reseed(tmp_path: Path) -> None:
    db = FakeDB()
    factory = lambda: FakeSession(db)  # noqa: E731
    _write(tmp_path, "fire.json", _doc(version=1))
    await seed_content(factory, tmp_path)

    edited = db.tables["threat_text"][("fire-home", "ru")]
    edited["admin_version"] = 1
    edited["quick"] = "Ручное действие"
    edited["mistakes"] = ["Своя ошибка"]
    edited["citations"] = [{"org": "Ручной источник", "url": "https://mchs.gov.ru/ruchnoy"}]
    newer = _doc(version=2)
    newer["threats"][0]["texts"]["ru"]["quick"] = "Новое действие"
    _write(tmp_path, "fire.json", newer)

    await seed_content(factory, tmp_path)

    assert edited["quick"] == "Ручное действие"
    assert edited["mistakes"] == ["Своя ошибка"]
    assert edited["citations"] == [
        {"org": "Ручной источник", "url": "https://mchs.gov.ru/ruchnoy"}
    ]


async def test_threat_updated_backfill_is_null_only(tmp_path: Path) -> None:
    db = FakeDB()
    factory = lambda: FakeSession(db)  # noqa: E731
    doc = _doc()
    _write(tmp_path, "fire.json", doc)
    await seed_content(factory, tmp_path)

    threat = db.tables["threat"][("fire-home",)]
    threat["updated"] = "2020-01-01"
    await seed_content(factory, tmp_path)
    assert threat["updated"] == "2020-01-01"

    del threat["updated"]
    await seed_content(factory, tmp_path)
    assert threat["updated"] == "2026-11-20"


@pytest.mark.parametrize(
    "bad_quick", ["x" * 121, "Звоните 101\nи выходите", "Смотрите https://mchs.gov.ru"]
)
async def test_invalid_quick_is_reported_without_partial_rows(
    tmp_path: Path, bad_quick: str
) -> None:
    db = FakeDB()
    doc = _doc()
    doc["threats"][0]["texts"]["ru"]["quick"] = bad_quick
    _write(tmp_path, "fire.json", doc)

    report = await seed_content(lambda: FakeSession(db), tmp_path)  # type: ignore[arg-type]

    assert report.files == 0
    assert any("quick" in error for error in report.errors)
    assert db.count("threat_text") == 0


@pytest.mark.parametrize(
    "bad_mistakes",
    [["a"] * 4, ["x" * 141], "Не бегите"],
)
async def test_invalid_mistakes_are_reported_without_partial_rows(
    tmp_path: Path, bad_mistakes: object
) -> None:
    db = FakeDB()
    doc = _doc()
    doc["threats"][0]["texts"]["ru"]["mistakes"] = bad_mistakes
    _write(tmp_path, "fire.json", doc)

    report = await seed_content(lambda: FakeSession(db), tmp_path)  # type: ignore[arg-type]

    assert report.files == 0
    assert any("mistakes" in error for error in report.errors)
    assert db.count("threat_text") == 0


@pytest.mark.parametrize(
    "bad_citations",
    [
        [{"org": "МЧС России", "url": "https://example.com/x"}],
        [{"org": "МЧС России", "url": "http://mchs.gov.ru/x"}],
        [{"org": "М", "url": "https://mchs.gov.ru/x"}],
        [{"org": "МЧС России", "url": "https://mchs.gov.ru/x"}] * 2,
        [],
    ],
)
async def test_invalid_citations_are_reported_without_partial_rows(
    tmp_path: Path, bad_citations: object
) -> None:
    db = FakeDB()
    doc = _doc()
    doc["threats"][0]["texts"]["ru"]["citations"] = bad_citations
    _write(tmp_path, "fire.json", doc)

    report = await seed_content(lambda: FakeSession(db), tmp_path)  # type: ignore[arg-type]

    assert report.files == 0
    assert any("citations" in error for error in report.errors)
    assert db.count("threat_text") == 0


@pytest.mark.parametrize("bad_updated", ["20.11.2026", "2026-02-30"])
async def test_invalid_updated_is_reported_without_partial_rows(
    tmp_path: Path, bad_updated: str
) -> None:
    db = FakeDB()
    doc = _doc()
    doc["threats"][0]["updated"] = bad_updated
    _write(tmp_path, "fire.json", doc)

    report = await seed_content(lambda: FakeSession(db), tmp_path)  # type: ignore[arg-type]

    assert report.files == 0
    assert any("updated" in error for error in report.errors)
    assert db.count("threat") == 0
