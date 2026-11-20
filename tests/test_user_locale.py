"""Shared user locale: column wins, Telegram language is the fallback."""

from __future__ import annotations

from typing import Any

import pytest

from app.services.user_locale import get_locale, set_locale


class _Result:
    def __init__(self, rows: list) -> None:
        self.rows = rows

    def first(self):
        return self.rows[0] if self.rows else None


class FakeDB:
    def __init__(self, locales: dict[int, str] | None = None) -> None:
        self.locales = dict(locales or {})

    def factory(self) -> FakeSessions:
        return FakeSessions(self)

    def execute(self, statement: Any, params: dict) -> _Result:
        sql = " ".join(str(statement).split())
        if sql.startswith("SELECT locale FROM users"):
            user_id = params["user_id"]
            return _Result([(self.locales[user_id],)] if user_id in self.locales else [])
        if sql.startswith("UPDATE users SET locale"):
            self.locales[params["user_id"]] = params["locale"]
            return _Result([])
        raise AssertionError("unexpected SQL: " + sql)


class FakeSessions:
    def __init__(self, db: FakeDB) -> None:
        self.db = db

    def __call__(self) -> FakeSessions:
        return self

    async def __aenter__(self) -> FakeSessions:
        return self

    async def __aexit__(self, *exc: object) -> bool:
        return False

    def begin(self) -> FakeSessions:
        return self

    async def execute(self, statement: Any, params: dict | None = None) -> _Result:
        return self.db.execute(statement, params or {})


@pytest.mark.asyncio
async def test_column_wins_over_telegram_language() -> None:
    db = FakeDB({7: "tt"})
    assert await get_locale(db.factory(), 7, "en-US") == "tt"


@pytest.mark.asyncio
async def test_missing_row_falls_back_to_telegram_language() -> None:
    assert await get_locale(FakeDB().factory(), 7, "zh-Hans") == "zh"
    assert await get_locale(FakeDB().factory(), 7, "de") == "ru"


@pytest.mark.asyncio
async def test_unknown_stored_value_falls_back() -> None:
    assert await get_locale(FakeDB({7: "de"}).factory(), 7, "en") == "en"


@pytest.mark.asyncio
async def test_set_locale_writes_the_column() -> None:
    db = FakeDB()
    await set_locale(db.factory(), 7, "en-GB")
    assert db.locales[7] == "en"