from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from sqlalchemy.dialects import postgresql

from app.content.seed import SeedReport, category_upsert, quiz_upsert, seed_content, threat_text_upsert, threat_upsert


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
                store[key] = {**row, "enabled": True}
                continue
            if versioned and row["content_version"] <= current["content_version"]:
                continue
            merged = {**current, **row}
            merged["enabled"] = current["enabled"]
            store[key] = merged

    def count(self, table: str) -> int:
        return len(self.tables[table])


class FakeSession:
    def __init__(self, db: FakeDB) -> None:
        self.db = db

    async def __aenter__(self) -> FakeSession:
        return self

    async def __aexit__(self, *exc: object) -> bool:
        return False

    async def execute(self, statement: Any, rows: list[dict[str, Any]] | None = None) -> None:
        if rows is not None:
            await self.db.apply(statement.table.name, rows)

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
                "texts": {
                    "ru": {
                        "title": title,
                        "summary": "Дым и огонь в квартире.",
                        "signs": ["Дым"],
                        "before": ["Датчик"],
                        "during": ["Звоните 101"],
                        "after": ["Не входите"],
                        "forbidden": ["Не открывайте окна"],
                        "contacts": ["101"],
                        "source": "МЧС России",
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
