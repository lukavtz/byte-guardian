from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.config import Settings
from app.handlers import admin
from app.services import settings as settings_service


class _Result:
    def __init__(self, rows: list) -> None:
        self.rows = rows

    def first(self):
        return self.rows[0] if self.rows else None

    def mappings(self) -> _Result:
        return self

    def __iter__(self):
        return iter(self.rows)


class _Session:
    def __init__(self, db: FakeDB) -> None:
        self.db = db

    async def __aenter__(self) -> _Session:
        return self

    async def __aexit__(self, *exc: object) -> bool:
        return False

    def begin(self) -> _Session:
        return self

    async def execute(self, statement, params=None) -> _Result:
        return self.db.execute(statement, params or {})


class FakeDB:
    def __init__(
        self,
        stats: tuple[int, int, int, int] = (0, 0, 0, 0),
        categories: list[tuple[str, bool]] | None = None,
    ) -> None:
        self.stats = stats
        self.categories = categories or []
        self.settings: dict[str, str] = {}
        self.toggle_calls: list[tuple[str, dict]] = []

    def factory(self) -> FakeSessionFactory:
        return FakeSessionFactory(self)

    def execute(self, statement, params: dict) -> _Result:
        sql = " ".join(str(statement).split())
        if sql.startswith("INSERT INTO settings"):
            self.settings[params["key"]] = params["value"]
            return _Result([])
        if sql.startswith("SELECT value FROM settings"):
            key = params["key"]
            return _Result([(self.settings[key],)] if key in self.settings else [])
        if sql.startswith("DELETE FROM settings"):
            key = params["key"]
            if key in self.settings:
                return _Result([(self.settings.pop(key),)])
            return _Result([])
        if sql.startswith("SELECT (SELECT count(*)"):
            users, threats, quizzes, answers = self.stats
            return _Result(
                [
                    {
                        "users": users,
                        "threats": threats,
                        "quizzes": quizzes,
                        "answers": answers,
                    }
                ]
            )
        if sql.startswith("SELECT slug, enabled FROM category"):
            return _Result(list(self.categories))
        if sql.startswith("UPDATE category SET enabled = NOT enabled"):
            self.toggle_calls.append((sql, params))
            for index, (slug, enabled) in enumerate(self.categories):
                if slug == params["slug"]:
                    self.categories[index] = (slug, not enabled)
                    return _Result([(not enabled,)])
            return _Result([])
        raise AssertionError("unexpected SQL: " + sql)


class FakeSessionFactory:
    def __init__(self, db: FakeDB) -> None:
        self.db = db

    def __call__(self) -> _Session:
        return _Session(self.db)


def make_settings(admin_ids: str = "42") -> Settings:
    return Settings(
        bot_token="123456:" + "a" * 30,
        admin_ids=admin_ids,
        database_url="postgresql+asyncpg://unused/unused",
    )


def make_message(user_id: int = 42) -> MagicMock:
    message = MagicMock()
    message.from_user = SimpleNamespace(id=user_id)
    message.answer = AsyncMock()
    return message


def make_query(user_id: int, data: str) -> MagicMock:
    query = MagicMock()
    query.from_user = SimpleNamespace(id=user_id)
    query.data = data
    query.answer = AsyncMock()
    query.message = MagicMock()
    query.message.edit_text = AsyncMock()
    return query


async def test_non_admin_is_denied() -> None:
    db = FakeDB(stats=(5, 5, 5, 5))
    message = make_message(user_id=7)

    await admin.admin_panel(message, db.factory(), make_settings())

    message.answer.assert_awaited_once_with(admin.ADMIN_ONLY_TEXT)
    assert not db.toggle_calls


async def test_panel_renders_counters_and_toggles() -> None:
    db = FakeDB(stats=(7, 12, 30, 99), categories=[("fire", True), ("cyber", False)])
    message = make_message()

    await admin.admin_panel(message, db.factory(), make_settings())

    text = message.answer.await_args.args[0]
    assert "Users: 7" in text
    assert "Threats: 12" in text
    assert "Quizzes: 30" in text
    assert "Answers: 99" in text
    keyboard = message.answer.await_args.kwargs["reply_markup"]
    data = [button.callback_data for row in keyboard.inline_keyboard for button in row]
    assert data == ["adm:t:fire", "adm:t:cyber"]


async def test_toggle_changes_only_enabled() -> None:
    db = FakeDB(categories=[("fire", True), ("cyber", False)])

    enabled = await admin.toggle_category(db.factory(), "fire")

    assert enabled is False
    assert db.categories == [("fire", False), ("cyber", False)]
    sql, params = db.toggle_calls[-1]
    assert "SET enabled = NOT enabled" in sql
    assert params == {"slug": "fire"}
    assert await admin.toggle_category(db.factory(), "unknown") is None


async def test_toggle_callback_rejects_non_admin() -> None:
    db = FakeDB(categories=[("fire", True)])
    query = make_query(user_id=7, data="adm:t:fire")

    await admin.admin_toggle(query, db.factory(), make_settings())

    query.answer.assert_awaited_once()
    assert query.answer.await_args.kwargs["show_alert"] is True
    assert db.categories == [("fire", True)]


async def test_toggle_callback_flips_and_refreshes_panel() -> None:
    db = FakeDB(categories=[("fire", True)])
    query = make_query(user_id=42, data="adm:t:fire")

    await admin.admin_toggle(query, db.factory(), make_settings())

    assert db.categories == [("fire", False)]
    query.message.edit_text.assert_awaited_once()
    text = query.message.edit_text.await_args.args[0]
    assert "⬜ fire" in text


async def test_settings_round_trip() -> None:
    db = FakeDB()
    factory = db.factory()

    await settings_service.settings_set(factory, "toggles", {"fire": False})
    assert await settings_service.settings_get(factory, "toggles") == {"fire": False}
    assert await settings_service.settings_get(factory, "missing") is None


async def test_draft_survives_restart_and_is_per_admin() -> None:
    db = FakeDB()

    await settings_service.draft_save(db.factory(), 42, "<b>hello</b>")

    assert await settings_service.draft_take(db.factory(), 42) == "<b>hello</b>"
    assert await settings_service.draft_take(db.factory(), 43) is None


async def test_draft_expires_after_an_hour(monkeypatch: pytest.MonkeyPatch) -> None:
    db = FakeDB()
    clock = {"now": 1_000.0}
    monkeypatch.setattr(settings_service.time, "time", lambda: clock["now"])

    await settings_service.draft_save(db.factory(), 42, "draft")
    clock["now"] += settings_service.DRAFT_TTL_SECONDS - 1
    assert await settings_service.draft_take(db.factory(), 42) == "draft"

    clock["now"] += 1
    assert await settings_service.draft_take(db.factory(), 42) is None
    assert "broadcast_draft:42" not in db.settings


async def test_draft_take_is_atomic() -> None:
    db = FakeDB()

    await settings_service.draft_save(db.factory(), 42, "<b>once</b>")
    first, second = await asyncio.gather(
        settings_service.draft_take(db.factory(), 42),
        settings_service.draft_take(db.factory(), 42),
    )

    assert {first, second} == {"<b>once</b>", None}
    assert "broadcast_draft:42" not in db.settings
    assert await settings_service.draft_take(db.factory(), 42) is None


async def test_settings_value_json_is_parsed() -> None:
    db = FakeDB()
    db.settings["legacy"] = json.dumps({"a": 1})

    assert await settings_service.settings_get(db.factory(), "legacy") == {"a": 1}