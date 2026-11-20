from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.config import Settings
from app.handlers import admin
from app.services import settings as settings_service
from app.services.callbacks import Callback, cb_admin, cb_admin_broadcast, cb_admin_category, cb_admin_hint, parse_cb
from app.services.i18n import DEFAULT_LOCALE, t


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
        categories: list[tuple[str, bool, str, str]] | None = None,
        audience: int = 0,
        last: dict | None = None,
    ) -> None:
        self.stats = stats
        self.categories = categories or []
        self.audience = audience
        self.last = last
        self.settings: dict[str, object] = {}
        self.toggle_calls: list[tuple[str, dict]] = []

    def factory(self) -> FakeSessionFactory:
        return FakeSessionFactory(self)

    def execute(self, statement, params: dict) -> _Result:
        sql = " ".join(str(statement).split())
        if sql == "SELECT 1":
            return _Result([(1,)])
        if sql.startswith("INSERT INTO settings"):
            self.settings[params["key"]] = json.loads(params["value"])
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
        if sql.startswith("SELECT slug, enabled, titles, emoji FROM category"):
            return _Result(
                [
                    {
                        "slug": slug,
                        "enabled": enabled,
                        "titles": {"ru": title},
                        "emoji": emoji,
                    }
                    for slug, enabled, title, emoji in self.categories
                ]
            )
        if sql.startswith("SELECT count(*) FROM users WHERE broadcasts_opt_in"):
            return _Result([(self.audience,)])
        if sql.startswith("SELECT status, sent, failed FROM broadcasts"):
            return _Result([self.last] if self.last is not None else [])
        if sql.startswith("UPDATE category SET enabled = NOT enabled"):
            self.toggle_calls.append((sql, params))
            for index, (slug, enabled, title, emoji) in enumerate(self.categories):
                if slug == params["slug"]:
                    self.categories[index] = (slug, not enabled, title, emoji)
                    return _Result([(not enabled,)])
            return _Result([])
        raise AssertionError("unexpected SQL: " + sql)


class FakeSessionFactory:
    def __init__(self, db: FakeDB) -> None:
        self.db = db

    def __call__(self) -> _Session:
        return _Session(self.db)


class FakeBot:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict]] = []

    async def send_rich_message(self, **kwargs: object) -> MagicMock:
        self.calls.append(("send_rich_message", dict(kwargs)))
        return MagicMock(message_id=1)

    async def edit_message_text(self, **kwargs: object) -> MagicMock:
        self.calls.append(("edit_message_text", dict(kwargs)))
        return MagicMock(message_id=1)

    def html(self, index: int = 0) -> str:
        return str(self.calls[index][1]["rich_message"].html)  # type: ignore[union-attr]


def make_settings(admin_ids: str = "42") -> Settings:
    return Settings(
        bot_token="123456:" + "a" * 30,
        admin_ids=admin_ids,
        database_url="postgresql+asyncpg://unused/unused",
    )


def make_message(user_id: int = 42) -> MagicMock:
    message = MagicMock()
    message.from_user = SimpleNamespace(id=user_id, language_code="ru")
    message.chat = SimpleNamespace(id=100)
    message.answer = AsyncMock()
    return message


def make_query(user_id: int, data: str) -> MagicMock:
    query = MagicMock()
    query.from_user = SimpleNamespace(id=user_id, language_code="ru")
    query.data = data
    query.answer = AsyncMock()
    query.message = MagicMock()
    query.message.chat = SimpleNamespace(id=100)
    query.message.message_id = 555
    return query


def make_dispatcher(probes: dict | None = None) -> SimpleNamespace:
    return SimpleNamespace(workflow_data={"readiness_probes": probes or {}})


async def _ok() -> None:
    return None


def _probes() -> dict:
    return {"postgres": _ok, "redis": None}


def _categories() -> list[admin.CategoryRow]:
    return [
        admin.CategoryRow("fire", True, "Пожар", "🔥"),
        admin.CategoryRow("cyber", False, "Кибербезопасность", "🔐"),
    ]


def test_home_screen_has_table_values_and_premium_tags() -> None:
    screen = admin.home_screen(
        {"users": 7, "threats": 12, "quizzes": 30, "answers": 99},
        _categories(),
        {"postgres": "ok", "redis": "skip"},
    )
    html = screen.html

    assert "<th>Пользователи</th>" in html
    assert "<td>7</td><td>12</td><td>30</td><td>99</td>" in html
    assert "<h1>" in html and "Админ-панель" in html
    assert "Только для администраторов" in html
    assert 'emoji-id="6021401276904905698"' in html  # panel
    assert "b1:admin:cat:fire" in html
    assert "b1:admin:cat:cyber" in html
    assert "✅" in html and "⛔" in html
    assert "healthz: <b>ok</b>" in html
    assert "<img" not in html


def test_home_screen_has_one_primary_button() -> None:
    screen = admin.home_screen(
        {"users": 1, "threats": 2, "quizzes": 3, "answers": 4},
        _categories(),
        {"postgres": "ok"},
    )

    assert screen.html.count('style="primary"') == 1
    assert "Обновить" in screen.html


def test_broadcast_screen_shows_audience_and_one_primary() -> None:
    screen = admin.broadcast_screen(7, {"status": "done", "sent": 5, "failed": 1})
    html = screen.html

    assert "Аудитория: <b>7</b>" in html
    assert "готово" in html
    assert "отправлено 5" in html and "ошибок 1" in html
    assert html.count('style="primary"') == 1
    assert "Написать" in html and "Назад" in html


def test_broadcast_screen_without_history() -> None:
    html = admin.broadcast_screen(0, None).html

    assert "Аудитория: <b>0</b>" in html
    assert "Рассылок ещё не было." in html


def test_admin_callback_data_fits_64_bytes() -> None:
    payloads = [
        cb_admin(),
        cb_admin_broadcast(),
        cb_admin_hint(),
        cb_admin_category("a" * 32),
    ]
    for payload in payloads:
        assert len(payload.encode("utf-8")) <= 64, payload
    assert parse_cb(cb_admin_category("fire")) == Callback(
        "admin", arg="fire", extra=("cat",)
    )
    assert parse_cb(cb_admin_broadcast()) == Callback("admin", arg="bc")
    assert parse_cb(cb_admin_hint()) == Callback("admin", arg="hint")


async def test_non_admin_is_denied() -> None:
    db = FakeDB(stats=(5, 5, 5, 5))
    message = make_message(user_id=7)
    bot = FakeBot()

    await admin.admin_panel(
        message, bot, db.factory(), make_settings(), make_dispatcher()
    )

    message.answer.assert_awaited_once_with(t(DEFAULT_LOCALE, "admin.denied"))
    assert not bot.calls
    assert not db.toggle_calls


async def test_admin_panel_sends_rich_home() -> None:
    db = FakeDB(
        stats=(7, 12, 30, 99),
        categories=[("fire", True, "Пожар", "🔥")],
    )
    message = make_message()
    bot = FakeBot()

    await admin.admin_panel(
        message, bot, db.factory(), make_settings(), make_dispatcher(_probes())
    )

    assert bot.calls[0][0] == "send_rich_message"
    html = bot.html()
    assert "Админ-панель" in html
    assert "<td>7</td><td>12</td><td>30</td><td>99</td>" in html
    assert "b1:admin:cat:fire" in html


async def test_toggle_changes_only_enabled() -> None:
    db = FakeDB(categories=[("fire", True, "Пожар", "🔥"), ("cyber", False, "Кибер", "🔐")])

    enabled = await admin.toggle_category(db.factory(), "fire")

    assert enabled is False
    assert db.categories == [
        ("fire", False, "Пожар", "🔥"),
        ("cyber", False, "Кибер", "🔐"),
    ]
    sql, params = db.toggle_calls[-1]
    assert "SET enabled = NOT enabled" in sql
    assert params == {"slug": "fire"}
    assert await admin.toggle_category(db.factory(), "unknown") is None


async def test_toggle_callback_rejects_non_admin() -> None:
    db = FakeDB(categories=[("fire", True, "Пожар", "🔥")])
    query = make_query(user_id=7, data="b1:admin:cat:fire")
    bot = FakeBot()

    await admin.admin_callback(
        query, bot, db.factory(), make_settings(), make_dispatcher()
    )

    query.answer.assert_awaited_once_with(admin.ADMIN_ONLY_ALERT, show_alert=True)
    assert db.categories == [("fire", True, "Пожар", "🔥")]
    assert not bot.calls


async def test_toggle_callback_flips_and_refreshes() -> None:
    db = FakeDB(categories=[("fire", True, "Пожар", "🔥")])
    query = make_query(user_id=42, data="b1:admin:cat:fire")
    bot = FakeBot()

    await admin.admin_callback(
        query, bot, db.factory(), make_settings(), make_dispatcher(_probes())
    )

    assert db.categories == [("fire", False, "Пожар", "🔥")]
    assert query.answer.await_args.args[0] == "Раздел выключен"
    assert bot.calls[0][0] == "edit_message_text"
    html = bot.html()
    assert "⛔" in html
    assert "✅" not in html


async def test_broadcast_callback_opens_screen() -> None:
    db = FakeDB(
        audience=7,
        last={"status": "running", "sent": 3, "failed": 1},
    )
    query = make_query(user_id=42, data="b1:admin:bc")
    bot = FakeBot()

    await admin.admin_callback(
        query, bot, db.factory(), make_settings(), make_dispatcher()
    )

    html = bot.html()
    assert "Аудитория: <b>7</b>" in html
    assert "идёт" in html
    assert "b1:admin" in html


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
    db.settings["legacy"] = {"a": 1}

    assert await settings_service.settings_get(db.factory(), "legacy") == {"a": 1}