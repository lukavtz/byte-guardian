from __future__ import annotations

import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from aiogram.dispatcher.event.bases import SkipHandler

from app.config import Settings
from app.content.repository import ContentRepository, Snapshot
from app.handlers import admin
from app.services import content_admin
from app.services import settings as settings_service
from app.services.callbacks import (
    cb_admin,
    cb_admin_add,
    cb_admin_cancel,
    cb_admin_category_edit,
    cb_admin_category_screen,
    cb_admin_content,
    cb_admin_edit,
    cb_admin_move,
    cb_admin_question,
    cb_admin_quiz,
    cb_admin_quiz_answer,
    cb_admin_quiz_edit,
    cb_admin_severity,
    cb_admin_threat,
    cb_admin_threats,
    cb_admin_toggle,
    parse_cb,
)

TEXT = {
    "threat_slug": "fire-home",
    "locale": "ru",
    "title": "Пожар в квартире",
    "summary": "Дым и огонь.",
    "signs": ["Дым", "Гарь"],
    "before": ["Датчик", "План"],
    "during": ["Звоните 101", "Выходите"],
    "after": ["На улицу", "Не входите"],
    "forbidden": ["Не тушите водой", "Не открывайте окна"],
    "contacts": ["101", "112"],
    "source": "МЧС России",
}


class _Result:
    def __init__(self, rows: list) -> None:
        self.rows = rows

    def first(self):
        return self.rows[0] if self.rows else None

    def mappings(self) -> _Result:
        return self

    def all(self) -> list:
        return self.rows

    def scalar(self):
        row = self.first()
        return row[0] if isinstance(row, tuple) else row


class FakeDB:
    def __init__(self) -> None:
        self.settings: dict[str, object] = {}
        self.categories: dict[str, dict] = {
            "fire": {
                "slug": "fire",
                "sort": 1,
                "emoji": "🔥",
                "enabled": True,
                "titles": {"ru": "Пожар"},
            }
        }
        self.threats: dict[str, dict] = {
            "fire-home": {
                "slug": "fire-home",
                "category_slug": "fire",
                "severity": 3,
                "sort": 0,
                "enabled": True,
            }
        }
        self.texts: dict[tuple[str, str], dict] = {("fire-home", "ru"): dict(TEXT)}
        self.quizzes: dict[tuple[str, str, str], dict] = {
            ("fire-home", "ru", f"q{number}"): {
                "threat_slug": "fire-home",
                "question_id": f"q{number}",
                "locale": "ru",
                "prompt": f"Вопрос {number}?",
                "options": [f"Вариант {number}a", f"Вариант {number}b", f"Вариант {number}c", f"Вариант {number}d"],
                "answer": 0,
                "explanation": f"Объяснение {number}",
                "admin_version": 0,
            }
            for number in (1, 2, 3)
        }

    def factory(self) -> FakeSessionFactory:
        return FakeSessionFactory(self)

    def execute(self, statement, params: dict | None = None) -> _Result:
        sql = " ".join(str(statement).split())
        params = params or {}
        if sql.startswith("INSERT INTO settings"):
            self.settings[params["key"]] = json.loads(params["value"])
            return _Result([])
        if sql.startswith("SELECT value FROM settings"):
            key = params["key"]
            return _Result([(self.settings[key],)] if key in self.settings else [])
        if sql.startswith("DELETE FROM settings"):
            self.settings.pop(params["key"], None)
            return _Result([])
        if sql.startswith("SELECT c.slug, c.emoji"):
            rows = []
            for item in self.categories.values():
                if "WHERE c.slug = :slug" in sql and item["slug"] != params["slug"]:
                    continue
                total = sum(
                    1 for t in self.threats.values() if t["category_slug"] == item["slug"]
                )
                active = sum(
                    1
                    for t in self.threats.values()
                    if t["category_slug"] == item["slug"] and t["enabled"]
                )
                rows.append({**item, "total": total, "active": active})
            return _Result(rows)
        if sql.startswith("SELECT t.slug, t.severity, t.enabled"):
            rows = [
                {**t, "title": self.texts.get((t["slug"], "ru"), {}).get("title", t["slug"])}
                for t in self.threats.values()
                if t["category_slug"] == params["category"]
            ]
            return _Result(rows)
        if sql.startswith("SELECT t.slug, t.category_slug"):
            threat = self.threats.get(params["slug"])
            if threat is None:
                return _Result([])
            emoji = self.categories[threat["category_slug"]]["emoji"]
            return _Result([{**threat, "emoji": emoji}])
        if sql.startswith("SELECT * FROM threat_text"):
            return _Result(
                [
                    text
                    for (slug, _locale), text in self.texts.items()
                    if slug == params["slug"]
                ]
            )
        if sql.startswith("SELECT 1 FROM category"):
            return _Result([(1,)] if params["slug"] in self.categories else [])
        if sql.startswith("SELECT 1 FROM threat"):
            return _Result([(1,)] if params["slug"] in self.threats else [])
        if sql.startswith("SELECT COALESCE(max(sort)"):
            sorts = [
                t["sort"]
                for t in self.threats.values()
                if t["category_slug"] == params["category"]
            ]
            return _Result([(max(sorts, default=-1) + 1,)])
        if sql.startswith("INSERT INTO threat ("):
            self.threats[params["slug"]] = {
                "slug": params["slug"],
                "category_slug": params["category"],
                "severity": params["severity"],
                "sort": params["sort"],
                "enabled": True,
            }
            return _Result([])
        if sql.startswith("INSERT INTO threat_text"):
            key = (params["slug"], params["locale"])
            stored = {
                "threat_slug": params["slug"],
                "locale": params["locale"],
                "title": params["title"],
                "summary": params["summary"],
                "signs": json.loads(params.get("signs", "[]")),
                "before": json.loads(params.get("before", "[]")),
                "during": json.loads(params.get("during", "[]")),
                "after": json.loads(params.get("after", "[]")),
                "forbidden": json.loads(params.get("forbidden", "[]")),
                "contacts": json.loads(params.get("contacts", "[]")),
                "source": params.get("source", ""),
            }
            if "ON CONFLICT" in sql and key in self.texts:
                stored["admin_version"] = self.texts[key].get("admin_version", 0) + 1
                stored["edited_at"] = "now"
            else:
                stored["admin_version"] = 1
                stored["edited_at"] = "now"
            self.texts[key] = stored
            return _Result([])
        if sql.startswith("UPDATE category SET titles"):
            item = self.categories.get(params["slug"])
            if item is None:
                return _Result([])
            item["titles"] = {"ru": params["title"]}
            item["emoji"] = params["emoji"]
            item["enabled"] = params["enabled"]
            return _Result([(params["slug"],)])
        if sql.startswith("UPDATE threat SET enabled"):
            threat = self.threats.get(params["slug"])
            if threat is None:
                return _Result([])
            threat["enabled"] = params["enabled"]
            return _Result([(params["enabled"],)])
        if sql.startswith("UPDATE threat SET severity"):
            threat = self.threats.get(params["slug"])
            if threat is None:
                return _Result([])
            threat["severity"] = params["severity"]
            return _Result([(params["severity"],)])
        if sql.startswith("SELECT slug FROM threat WHERE category_slug"):
            return _Result(
                [
                    (t["slug"],)
                    for t in sorted(
                        (
                            t
                            for t in self.threats.values()
                            if t["category_slug"] == params["category"]
                        ),
                        key=lambda t: (t["sort"], t["slug"]),
                    )
                ]
            )
        if sql.startswith("UPDATE threat SET sort"):
            self.threats[params["slug"]]["sort"] = params["sort"]
            return _Result([])
        if sql.startswith("SELECT threat_slug, question_id, locale"):
            return _Result(
                [
                    quiz
                    for (slug, locale, _qid), quiz in sorted(self.quizzes.items())
                    if slug == params["slug"] and locale == params["locale"]
                ]
            )
        if sql.startswith("UPDATE quiz_question"):
            key = (params["slug"], params["locale"], params["qid"])
            quiz = self.quizzes.get(key)
            if quiz is None:
                return _Result([])
            quiz.update(
                prompt=params["prompt"],
                options=json.loads(params["options"]),
                answer=params["answer"],
                explanation=params["explanation"],
                admin_version=quiz.get("admin_version", 0) + 1,
            )
            return _Result([(params["qid"],)])
        raise AssertionError("unexpected SQL: " + sql)


class _Session:
    def __init__(self, db: FakeDB) -> None:
        self.db = db

    async def __aenter__(self) -> _Session:
        return self

    async def __aexit__(self, *exc: object) -> bool:
        return False

    async def execute(self, statement, params=None) -> _Result:
        return self.db.execute(statement, params or {})

    def begin(self) -> _Session:
        return self

    async def commit(self) -> None:
        return None


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

    def html(self, index: int = -1) -> str:
        return str(self.calls[index][1]["rich_message"].html)  # type: ignore[union-attr]


def make_settings(admin_ids: str = "42") -> Settings:
    return Settings(
        bot_token="123456:" + "a" * 30,
        admin_ids=admin_ids,
        database_url="postgresql+asyncpg://unused/unused",
    )


def make_message(user_id: int = 42, text: str = "Новый текст") -> MagicMock:
    message = MagicMock()
    message.from_user = SimpleNamespace(id=user_id, language_code="ru")
    message.chat = SimpleNamespace(id=100)
    message.text = text
    message.answer = AsyncMock()
    return message


def make_query(user_id: int = 42, data: str = "b1:admin:content") -> MagicMock:
    query = MagicMock()
    query.from_user = SimpleNamespace(id=user_id, language_code="ru")
    query.data = data
    query.answer = AsyncMock()
    query.message = MagicMock()
    query.message.chat = SimpleNamespace(id=100)
    query.message.message_id = 555
    return query


def make_dispatcher(repo: ContentRepository | None = None) -> SimpleNamespace:
    data: dict[str, object] = {"readiness_probes": {}}
    if repo is not None:
        data["repo"] = repo
    return SimpleNamespace(workflow_data=data)


async def _loader(calls: list[int]) -> Snapshot:
    calls.append(1)
    return [], [], [], []


def _repo(calls: list[int]) -> ContentRepository:
    return ContentRepository(loader=lambda: _loader(calls), aliases_path=None)


# --- validation -----------------------------------------------------------


def test_validation_rejects_every_limit() -> None:
    with pytest.raises(content_admin.ContentError):
        content_admin.validate_patch({"summary": "x" * 301})
    with pytest.raises(content_admin.ContentError):
        content_admin.validate_patch({"signs": ["один"]})
    with pytest.raises(content_admin.ContentError):
        content_admin.validate_patch({"signs": ["x" * 161, "второй"]})
    with pytest.raises(content_admin.ContentError):
        content_admin.validate_patch({"contacts": ["1", "2", "3", "4", "5", "6"]})
    with pytest.raises(content_admin.ContentError):
        content_admin.validate_patch({"unknown": "x"})
    assert content_admin.validate_patch({"summary": "норма"}) == {"summary": "норма"}
    # One phone number is a real card; the other lists start at two.
    assert content_admin.validate_patch({"contacts": ["101"]}) == {"contacts": ("101",)}


def test_field_input_splits_by_field_type() -> None:
    assert content_admin.parse_field_input("signs", "Дым\nГарь\n\n") == ("Дым", "Гарь")
    assert content_admin.parse_field_input("contacts", "101, 112") == ("101", "112")
    assert content_admin.parse_field_input("title", "  Пожар  ") == "Пожар"


# --- save + reload --------------------------------------------------------


async def test_save_threat_bumps_admin_version_and_reloads() -> None:
    db = FakeDB()
    calls: list[int] = []

    saved = await content_admin.save_threat(
        db.factory(), _repo(calls), "fire-home", "ru", {"summary": "Новое кратко"}
    )

    assert saved.summary == "Новое кратко"
    row = db.texts[("fire-home", "ru")]
    assert row["admin_version"] == 1
    assert row["edited_at"] == "now"
    assert calls == [1]


async def test_save_threat_rejects_bad_patch_without_reload() -> None:
    db = FakeDB()
    calls: list[int] = []

    with pytest.raises(content_admin.ContentError):
        await content_admin.save_threat(
            db.factory(), _repo(calls), "fire-home", "ru", {"summary": "x" * 301}
        )

    assert db.texts[("fire-home", "ru")].get("admin_version", 0) == 0
    assert calls == []


# --- edit roundtrip through the handler -----------------------------------


async def test_edit_roundtrip_through_message() -> None:
    db = FakeDB()
    calls: list[int] = []
    repo = _repo(calls)
    factory = db.factory()
    await settings_service.edit_pending_save(
        factory, 42, {"slug": "fire-home", "locale": "ru", "field": "signs"}
    )
    message = make_message(text="Дым\nГарь\nИскры")
    bot = FakeBot()

    await admin.admin_edit_text(
        message, bot, factory, make_settings(), make_dispatcher(repo)
    )

    message.answer.assert_awaited_once_with(admin.TOAST_SAVED)
    assert db.texts[("fire-home", "ru")]["signs"] == ["Дым", "Гарь", "Искры"]
    assert await settings_service.edit_pending(factory, 42) is None
    assert calls == [1]
    assert "Искры" in bot.html()


async def test_edit_rejects_and_keeps_pending() -> None:
    db = FakeDB()
    factory = db.factory()
    await settings_service.edit_pending_save(
        factory, 42, {"slug": "fire-home", "locale": "ru", "field": "signs"}
    )
    message = make_message(text="один пункт")

    await admin.admin_edit_text(
        message, FakeBot(), factory, make_settings(), make_dispatcher()
    )

    assert "от 2 до 5" in message.answer.await_args.args[0]
    assert await settings_service.edit_pending(factory, 42) is not None
    assert db.texts[("fire-home", "ru")]["signs"] == ["Дым", "Гарь"]


async def test_non_admin_text_is_denied() -> None:
    message = make_message(user_id=7)

    with pytest.raises(SkipHandler):
        await admin.admin_edit_text(
            message, FakeBot(), FakeDB().factory(), make_settings(), make_dispatcher()
        )

    message.answer.assert_not_awaited()


async def test_non_admin_callback_is_denied() -> None:
    db = FakeDB()
    query = make_query(user_id=7, data=cb_admin_content())

    await admin.admin_callback(
        query, FakeBot(), db.factory(), make_settings(), make_dispatcher()
    )

    query.answer.assert_awaited_once_with(admin.ADMIN_ONLY_ALERT, show_alert=True)


async def test_edit_callback_stores_pending_and_prompts() -> None:
    db = FakeDB()
    query = make_query(data=cb_admin_edit("fire-home", "ru", "summary"))
    bot = FakeBot()

    await admin.admin_callback(
        query, bot, db.factory(), make_settings(), make_dispatcher()
    )

    pending = await settings_service.edit_pending(db.factory(), 42)
    assert pending == {"slug": "fire-home", "locale": "ru", "field": "summary"}
    assert "Пришлите" in bot.html()


# --- create / soft delete / restore --------------------------------------


async def test_create_soft_delete_restore() -> None:
    db = FakeDB()
    calls: list[int] = []
    repo = _repo(calls)

    created = await content_admin.create_threat(
        db.factory(), repo, "fire", "fire-new", 2, title="Новая карточка"
    )

    assert created.slug == "fire-new"
    assert db.threats["fire-new"]["enabled"] is True
    assert db.texts[("fire-new", "ru")]["title"] == "Новая карточка"
    assert db.texts[("fire-new", "ru")]["admin_version"] == 1

    # A stub has empty lists; each field is still editable one at a time.
    filled = await content_admin.save_threat(
        db.factory(), repo, "fire-new", "ru", {"signs": ("Дым", "Гарь")}
    )
    assert filled.signs == ("Дым", "Гарь")

    assert await content_admin.delete_threat(db.factory(), repo, "fire-new") is False
    assert db.threats["fire-new"]["enabled"] is False

    assert await content_admin.restore_threat(db.factory(), repo, "fire-new") is True
    assert db.threats["fire-new"]["enabled"] is True
    assert len(calls) == 4


async def test_create_rejects_bad_slug_and_duplicate() -> None:
    db = FakeDB()
    with pytest.raises(content_admin.ContentError):
        await content_admin.create_threat(db.factory(), None, "fire", "Bad Slug", 2)
    with pytest.raises(content_admin.ContentError):
        await content_admin.create_threat(db.factory(), None, "fire", "fire-home", 2)
    with pytest.raises(content_admin.ContentError):
        await content_admin.create_threat(db.factory(), None, "nope", "fire-new", 2)
    with pytest.raises(content_admin.ContentError):
        await content_admin.create_threat(db.factory(), None, "fire", "fire-new", 4)


async def test_save_category_writes_title_emoji_and_reloads() -> None:
    db = FakeDB()
    calls: list[int] = []

    await content_admin.save_category(
        db.factory(), _repo(calls), "fire", "Пожары", "🔥", False
    )

    assert db.categories["fire"]["titles"] == {"ru": "Пожары"}
    assert db.categories["fire"]["enabled"] is False
    assert calls == [1]


# --- callback payloads ----------------------------------------------------


async def test_pending_expires_after_ten_minutes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    db = FakeDB()
    factory = db.factory()
    clock = {"now": 1000.0}
    monkeypatch.setattr(settings_service.time, "time", lambda: clock["now"])

    await settings_service.edit_pending_save(
        factory, 42, {"slug": "fire-home", "locale": "ru", "field": "signs"}
    )
    stored = db.settings["admin_edit:42"]
    assert isinstance(stored, dict)
    assert isinstance(stored["_at"], str)

    clock["now"] += settings_service.EDIT_TTL_SECONDS
    assert await settings_service.edit_pending(factory, 42) is None
    assert "admin_edit:42" not in db.settings


async def test_pending_without_valid_timestamp_is_dropped() -> None:
    db = FakeDB()
    db.settings["admin_edit:42"] = {
        "slug": "fire-home",
        "locale": "ru",
        "field": "signs",
        "_at": "not-a-number",
    }

    assert await settings_service.edit_pending(db.factory(), 42) is None
    assert "admin_edit:42" not in db.settings


async def test_cancel_command_clears_pending() -> None:
    db = FakeDB()
    factory = db.factory()
    await settings_service.edit_pending_save(
        factory, 42, {"slug": "fire-home", "locale": "ru", "field": "signs"}
    )
    message = make_message(text="/cancel")

    await admin.cancel_edit(message, factory, make_settings())

    message.answer.assert_awaited_once_with(admin.TOAST_CANCELLED)
    assert "admin_edit:42" not in db.settings


async def test_start_command_clears_pending_then_skips() -> None:
    db = FakeDB()
    factory = db.factory()
    await settings_service.edit_pending_save(
        factory, 42, {"slug": "fire-home", "locale": "ru", "field": "signs"}
    )
    message = make_message(text="/start")

    with pytest.raises(SkipHandler):
        await admin.clear_pending_on_start(message, factory, make_settings())

    assert "admin_edit:42" not in db.settings


async def test_validation_failure_keeps_state_with_fresh_timestamp(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    db = FakeDB()
    factory = db.factory()
    clock = {"now": 1000.0}
    monkeypatch.setattr(settings_service.time, "time", lambda: clock["now"])
    await settings_service.edit_pending_save(
        factory, 42, {"slug": "fire-home", "locale": "ru", "field": "signs"}
    )
    clock["now"] += 120

    await admin.admin_edit_text(
        make_message(text="один пункт"),
        FakeBot(),
        factory,
        make_settings(),
        make_dispatcher(),
    )

    stored = db.settings["admin_edit:42"]
    assert stored["_at"] == "1120.0"
    assert await settings_service.edit_pending(factory, 42) is not None


def test_content_callbacks_fit_and_parse() -> None:
    slug = "a" * 32
    payloads = [
        cb_admin(),
        cb_admin_content(),
        cb_admin_threats(slug),
        cb_admin_threat(slug, "ru"),
        cb_admin_edit(slug, "ru", "forbidden"),
        cb_admin_add(slug),
        cb_admin_cancel(slug, "ru"),
        cb_admin_toggle(slug, "ru"),
        cb_admin_category_screen(slug),
        cb_admin_category_edit(slug, "title"),
        cb_admin_severity(slug, "ru", 3),
        cb_admin_move(slug, "ru", "down"),
        cb_admin_quiz(slug, "ru"),
        cb_admin_question(slug, "ru", 99),
        cb_admin_quiz_edit(slug, "ru", 99, "o0"),
        cb_admin_quiz_answer(slug, "ru", 99, 3),
    ]
    for payload in payloads:
        assert len(payload.encode("utf-8")) <= 64, payload
    assert parse_cb(cb_admin_edit("fire-home", "ru", "signs")) is not None
    assert parse_cb(cb_admin_severity("fire-home", "ru", 2)) is not None
    assert parse_cb(cb_admin_move("fire-home", "ru", "up")) is not None
    assert parse_cb(cb_admin_quiz_answer("fire-home", "ru", 2, 3)) is not None
    assert parse_cb("b1:admin:edit:fire-home:xx:signs") is None
    assert parse_cb("b1:admin:edit:fire-home:ru:nope") is None
    assert parse_cb("b1:admin:sev:fire-home:ru:4") is None
    assert parse_cb("b1:admin:qe:fire-home:ru:1:zz") is None


# --- category editor ------------------------------------------------------


async def test_category_edit_roundtrip_through_message() -> None:
    db = FakeDB()
    calls: list[int] = []
    factory = db.factory()
    await settings_service.edit_pending_save(
        factory, 42, {"slug": "fire", "locale": "ru", "field": "cat_title"}
    )
    message = make_message(text="Пожары")

    await admin.admin_edit_text(
        message, FakeBot(), factory, make_settings(), make_dispatcher(_repo(calls))
    )

    message.answer.assert_awaited_once_with(admin.TOAST_SAVED)
    assert db.categories["fire"]["titles"] == {"ru": "Пожары"}
    assert calls == [1]
    assert await settings_service.edit_pending(factory, 42) is None


async def test_category_editor_screen_and_single_primary() -> None:
    item = content_admin.CategoryItem("fire", "Пожар", "🔥", True, 3, 2)
    screen = admin.category_editor_screen(item)

    assert "Пожар" in screen.html
    assert cb_admin_category_edit("fire", "title") in screen.html
    assert screen.html.count('style="primary"') <= 1


# --- severity and sort ----------------------------------------------------


async def test_severity_saves_and_reloads() -> None:
    db = FakeDB()
    calls: list[int] = []

    assert (
        await content_admin.set_threat_severity(
            db.factory(), _repo(calls), "fire-home", 1
        )
        == 1
    )
    assert db.threats["fire-home"]["severity"] == 1
    assert calls == [1]
    with pytest.raises(content_admin.ContentError):
        await content_admin.set_threat_severity(db.factory(), None, "fire-home", 4)


async def test_move_threat_swaps_sort_with_neighbour() -> None:
    db = FakeDB()
    db.threats["fire-two"] = {
        "slug": "fire-two",
        "category_slug": "fire",
        "severity": 2,
        "sort": 1,
        "enabled": True,
    }
    calls: list[int] = []

    assert await content_admin.move_threat(
        db.factory(), _repo(calls), "fire-home", "down"
    )
    assert db.threats["fire-two"]["sort"] == 0
    assert db.threats["fire-home"]["sort"] == 1
    assert not await content_admin.move_threat(
        db.factory(), _repo(calls), "fire-home", "down"
    )
    assert calls == [1]


# --- quiz editor ----------------------------------------------------------


async def test_quiz_save_bumps_admin_version_and_reloads() -> None:
    db = FakeDB()
    calls: list[int] = []

    saved = await content_admin.save_quiz(
        db.factory(),
        _repo(calls),
        "fire-home",
        "ru",
        "q1",
        {"options": ["A", "B", "C", "D"], "answer": 2},
    )

    assert saved.answer == 2
    assert saved.options == ("A", "B", "C", "D")
    quiz = db.quizzes[("fire-home", "ru", "q1")]
    assert quiz["admin_version"] == 1
    assert calls == [1]


async def test_quiz_validation_rejects_bad_values() -> None:
    db = FakeDB()
    with pytest.raises(content_admin.ContentError):
        await content_admin.save_quiz(
            db.factory(), None, "fire-home", "ru", "q1", {"answer": 4}
        )
    with pytest.raises(content_admin.ContentError):
        await content_admin.save_quiz(
            db.factory(), None, "fire-home", "ru", "q1",
            {"options": ["A", "", "C", "D"]},
        )
    with pytest.raises(content_admin.ContentError):
        await content_admin.save_quiz(
            db.factory(), None, "fire-home", "ru", "q1",
            {"explanation": "x" * 601},
        )
    with pytest.raises(content_admin.ContentError):
        await content_admin.save_quiz(
            db.factory(), None, "fire-home", "ru", "missing", {"answer": 1}
        )


async def test_quiz_answer_and_roundtrip_through_message() -> None:
    db = FakeDB()
    calls: list[int] = []
    factory = db.factory()
    await settings_service.edit_pending_save(
        factory,
        42,
        {
            "slug": "fire-home",
            "locale": "ru",
            "field": "q_option0",
            "qid": "q2",
        },
    )
    message = make_message(text="Новый вариант")

    await admin.admin_edit_text(
        message, FakeBot(), factory, make_settings(), make_dispatcher(_repo(calls))
    )

    message.answer.assert_awaited_once_with(admin.TOAST_SAVED)
    quiz = db.quizzes[("fire-home", "ru", "q2")]
    assert quiz["options"][0] == "Новый вариант"
    assert calls == [1]


async def test_quiz_answer_index_saves() -> None:
    db = FakeDB()
    quiz = await content_admin.set_quiz_answer(
        db.factory(), None, "fire-home", "ru", "q1", 3
    )
    assert quiz.answer == 3
    assert db.quizzes[("fire-home", "ru", "q1")]["answer"] == 3


def test_editor_screens_have_at_most_one_primary() -> None:
    detail = content_admin.ThreatDetail(
        slug="fire-home",
        category_slug="fire",
        severity=3,
        enabled=True,
        locale="ru",
        locales=("ru", "en"),
        exact=True,
        title="Пожар",
        summary="Дым",
        signs=("Дым",),
        before=("Датчик",),
        during=("101",),
        after=("Улица",),
        forbidden=("Лифт",),
        contacts=("101",),
        source="МЧС",
    )
    quiz = content_admin.QuizEdit(
        slug="fire-home",
        locale="ru",
        question_id="q1",
        prompt="Что делать?",
        options=("A", "B", "C", "D"),
        answer=1,
        explanation="Потому что.",
    )
    category = content_admin.CategoryItem("fire", "Пожар", "🔥", True, 3, 2)
    screens = [
        admin.content_screen([category]),
        admin.threats_screen(
            "fire", [content_admin.ThreatItem("fire-home", "Пожар", 3, True)]
        ),
        admin.threat_editor_screen(detail),
        admin.category_editor_screen(category),
        admin.quiz_screen("fire-home", "ru", [quiz]),
        admin.quiz_question_screen(quiz, 1, 3),
    ]
    for screen in screens:
        assert screen.html.count('style="primary"') <= 1
    assert cb_admin_question("fire-home", "ru", 1) in admin.quiz_screen(
        "fire-home", "ru", [quiz]
    ).html