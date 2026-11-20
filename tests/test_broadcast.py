from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import asyncpg
import pytest

from app.config import Settings
from app.handlers import broadcast
from app.jobs import broadcast as broadcast_job
from app.runtime.queue import ClaimedJob
from app.services import broadcast as service


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
        users: list[tuple[int, bool]] | None = None,
        opt_in_column: bool = True,
    ) -> None:
        self.users = dict(users or [])
        self.opt_in_column = opt_in_column
        self.broadcasts: dict[str, dict] = {}
        self.deliveries: dict[tuple[str, int], dict] = {}
        self.sql: list[str] = []

    def factory(self) -> FakeSessionFactory:
        return FakeSessionFactory(self)

    def execute(self, statement, params: dict) -> _Result:
        sql = " ".join(str(statement).split())
        self.sql.append(sql)
        if "broadcasts_opt_in" in sql and not self.opt_in_column:
            raise asyncpg.UndefinedColumnError(
                'column "broadcasts_opt_in" does not exist'
            )
        if sql.startswith("SELECT id FROM users"):
            after = params["after_id"]
            ids = [
                user_id
                for user_id in sorted(self.users)
                if user_id > after
                and ("broadcasts_opt_in" not in sql or self.users[user_id])
            ]
            return _Result([(user_id,) for user_id in ids[: params["limit"]]])
        if sql.startswith("INSERT INTO broadcasts"):
            broadcast_id = f"{len(self.broadcasts) + 1:08d}-0000-0000-0000-000000000000"
            self.broadcasts[broadcast_id] = {
                "status": "pending",
                "last_user_id": 0,
                "sent": 0,
                "failed": 0,
                
            }
            return _Result([(broadcast_id,)])
        if sql.startswith("SELECT status, last_user_id, sent, failed FROM broadcasts"):
            row = self.broadcasts.get(params["broadcast_id"])
            return _Result([dict(row)]) if row is not None else _Result([])
        if sql.startswith("UPDATE broadcasts SET status = 'running'"):
            self.broadcasts[params["broadcast_id"]]["status"] = "running"
            return _Result([])
        if sql.startswith("UPDATE broadcasts SET status = 'done'"):
            self.broadcasts[params["broadcast_id"]]["status"] = "done"
            return _Result([])
        if sql.startswith("DELETE FROM broadcast_deliveries"):
            return _Result([])
        if sql.startswith("INSERT INTO broadcast_deliveries"):
            key = (params["broadcast_id"], params["chat_id"])
            if key in self.deliveries:
                return _Result([])
            self.deliveries[key] = {"status": "pending", "error": None}
            return _Result([(params["chat_id"],)])
        if sql.startswith("UPDATE broadcast_deliveries"):
            key = (params["broadcast_id"], params["chat_id"])
            self.deliveries[key] = {"status": params["status"], "error": params["error"]}
            return _Result([])
        if sql.startswith("UPDATE broadcasts SET last_user_id"):
            row = self.broadcasts[params["broadcast_id"]]
            row["last_user_id"] = params["last_user_id"]
            row["sent"] = params["sent"]
            row["failed"] = params["failed"]
            return _Result([])
        if sql.startswith("UPDATE users SET broadcasts_opt_in"):
            self.users[params["user_id"]] = params["opt_in"]
            return _Result([])
        raise AssertionError("unexpected SQL: " + sql)


class FakeSessionFactory:
    def __init__(self, db: FakeDB) -> None:
        self.db = db

    def __call__(self) -> _Session:
        return _Session(self.db)


class FakeRedis:
    def __init__(self) -> None:
        self.counts: dict[str, int] = {}

    async def incr(self, key: str) -> int:
        self.counts[key] = self.counts.get(key, 0) + 1
        return self.counts[key]

    async def expire(self, key: str, seconds: int) -> bool:
        return True


class FakeBot:
    def __init__(self, blocked: set[int] | None = None) -> None:
        self.blocked = blocked or set()
        self.sent: list[int] = []

    async def send_message(self, chat_id: int, text: str) -> None:
        if chat_id in self.blocked:
            raise RuntimeError("Forbidden: bot was blocked by the user")
        self.sent.append(chat_id)


class FakeQueue:
    def __init__(self) -> None:
        self.enqueued: list[tuple[str, dict, dict]] = []

    async def enqueue(self, kind: str, payload: dict, **kwargs) -> str:
        self.enqueued.append((kind, payload, kwargs))
        return "job-1"


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


def make_context(db: FakeDB, bot: FakeBot) -> SimpleNamespace:
    return SimpleNamespace(
        bot=bot,
        session_factory=db.factory(),
        queue=FakeQueue(),
        settings=SimpleNamespace(queue_visibility_timeout_seconds=60),
        redis=FakeRedis(),
    )


async def test_audience_only_includes_opted_in_users() -> None:
    db = FakeDB(users=[(1, True), (2, False), (3, True)])

    assert await service.broadcast_audience(db.factory()) == [1, 3]


async def test_audience_falls_back_when_column_missing() -> None:
    db = FakeDB(users=[(1, True), (2, False), (3, True)], opt_in_column=False)

    assert await service.broadcast_audience(db.factory()) == [1, 2, 3]
    assert any("broadcasts_opt_in" in sql for sql in db.sql)


async def test_stop_disables_broadcasts() -> None:
    db = FakeDB(users=[(7, True)])
    message = make_message(user_id=7)

    await broadcast.stop_broadcasts(message, db.factory())

    assert db.users[7] is False
    message.answer.assert_awaited_once_with(broadcast.STOP_TEXT)


async def test_non_admin_broadcast_is_denied() -> None:
    db = FakeDB()
    message = make_message(user_id=7)

    await broadcast.broadcast_command(
        message, SimpleNamespace(args="hello"), db.factory(), make_settings()
    )

    message.answer.assert_awaited_once_with(broadcast.ADMIN_ONLY_TEXT)


async def test_broadcast_saves_draft_and_shows_confirm(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    db = FakeDB()
    factory = db.factory()
    save = AsyncMock()
    monkeypatch.setattr(broadcast, "draft_save", save)
    message = make_message()

    await broadcast.broadcast_command(
        message, SimpleNamespace(args=" <b>hello</b> "), factory, make_settings()
    )

    save.assert_awaited_once_with(factory, 42, "<b>hello</b>")
    keyboard = message.answer.await_args.kwargs["reply_markup"]
    data = [button.callback_data for row in keyboard.inline_keyboard for button in row]
    assert data == ["b1:bc:yes", "b1:bc:no"]


async def test_broadcast_rejects_empty_text() -> None:
    db = FakeDB()
    message = make_message()

    await broadcast.broadcast_command(
        message, SimpleNamespace(args="  "), db.factory(), make_settings()
    )

    assert "Usage" in message.answer.await_args.args[0]


async def test_broadcast_counts_utf16_units(monkeypatch: pytest.MonkeyPatch) -> None:
    db = FakeDB()
    save = AsyncMock()
    monkeypatch.setattr(broadcast, "draft_save", save)
    message = make_message()

    await broadcast.broadcast_command(
        message, SimpleNamespace(args="😀" * 2001), db.factory(), make_settings()
    )

    assert "limited" in message.answer.await_args.args[0]
    save.assert_not_awaited()


async def test_confirm_queues_broadcast(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    db = FakeDB()
    factory = db.factory()
    monkeypatch.setattr(broadcast, "draft_take", AsyncMock(return_value="<b>hi</b>"))
    discard = AsyncMock()
    monkeypatch.setattr(broadcast, "draft_discard", discard)
    queue = FakeQueue()
    query = make_query(user_id=42, data="b1:bc:yes")

    await broadcast.broadcast_confirm(query, factory, queue, make_settings())

    assert len(queue.enqueued) == 1
    kind, payload, kwargs = queue.enqueued[0]
    assert kind == broadcast_job.KIND
    assert payload["text"] == "<b>hi</b>"
    assert payload["createdBy"] == 42
    assert payload["broadcastId"] in db.broadcasts
    assert kwargs["idempotency_key"] == "broadcast:" + payload["broadcastId"]
    discard.assert_awaited_once_with(factory, 42)
    assert "queued" in query.message.edit_text.await_args.args[0]


async def test_confirm_with_expired_draft_does_not_queue(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    db = FakeDB()
    monkeypatch.setattr(broadcast, "draft_take", AsyncMock(return_value=None))
    queue = FakeQueue()
    query = make_query(user_id=42, data="b1:bc:yes")

    await broadcast.broadcast_confirm(query, db.factory(), queue, make_settings())

    assert queue.enqueued == []
    assert query.answer.await_args.kwargs["show_alert"] is True


async def test_non_admin_cannot_confirm(monkeypatch: pytest.MonkeyPatch) -> None:
    db = FakeDB()
    monkeypatch.setattr(broadcast, "draft_take", AsyncMock(return_value="<b>hi</b>"))
    queue = FakeQueue()
    query = make_query(user_id=7, data="b1:bc:yes")

    await broadcast.broadcast_confirm(query, db.factory(), queue, make_settings())

    assert queue.enqueued == []
    assert query.answer.await_args.kwargs["show_alert"] is True


async def test_cancel_discards_draft(monkeypatch: pytest.MonkeyPatch) -> None:
    db = FakeDB()
    factory = db.factory()
    discard = AsyncMock()
    monkeypatch.setattr(broadcast, "draft_discard", discard)
    queue = FakeQueue()
    query = make_query(user_id=42, data="b1:bc:no")

    await broadcast.broadcast_confirm(query, factory, queue, make_settings())

    discard.assert_awaited_once_with(factory, 42)
    assert queue.enqueued == []
    assert query.message.edit_text.await_args.args[0] == "Broadcast cancelled."


async def test_job_skips_blocked_and_saves_cursor() -> None:
    db = FakeDB(users=[(1, True), (2, True), (3, True)])
    factory = db.factory()
    ctx = make_context(db, FakeBot(blocked={2}))
    broadcast_id = await service.create_broadcast(factory, "hello", 42)
    job = ClaimedJob(
        id="job-1",
        kind=broadcast_job.KIND,
        payload={"broadcastId": broadcast_id, "text": "hello", "createdBy": 42},
        attempts=1,
        max_attempts=5,
    )

    await broadcast_job.handle(job, ctx)

    assert ctx.bot.sent == [1, 3]
    row = db.broadcasts[broadcast_id]
    assert row["last_user_id"] == 3
    assert row["sent"] == 2
    assert row["failed"] == 1
    assert row["status"] == "done"
    assert db.deliveries[(broadcast_id, 2)]["status"] == "failed"
    assert "blocked" in db.deliveries[(broadcast_id, 2)]["error"]


async def test_job_resumes_from_saved_cursor() -> None:
    db = FakeDB(users=[(1, True), (2, True), (3, True), (4, True)])
    factory = db.factory()
    ctx = make_context(db, FakeBot())
    broadcast_id = await service.create_broadcast(factory, "hello", 42)
    db.broadcasts[broadcast_id]["last_user_id"] = 2
    db.broadcasts[broadcast_id]["sent"] = 2
    job = ClaimedJob(
        id="job-1",
        kind=broadcast_job.KIND,
        payload={"broadcastId": broadcast_id, "text": "hello", "createdBy": 42},
        attempts=1,
        max_attempts=5,
    )

    await broadcast_job.handle(job, ctx)

    assert ctx.bot.sent == [3, 4]
    row = db.broadcasts[broadcast_id]
    assert row["last_user_id"] == 4
    assert row["sent"] == 4
    assert row["failed"] == 0


async def test_pacing_is_25_per_second() -> None:
    redis = FakeRedis()

    await service.take_rate_slot(redis)

    assert service.PER_SECOND == 25
    assert list(redis.counts.values()) == [1]