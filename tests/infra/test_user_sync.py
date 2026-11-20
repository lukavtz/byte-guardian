"""User rows are written from the Update wrapper, not the event itself."""

from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any

import pytest
from aiogram.types import CallbackQuery, Chat, Message, Update, User

from app.middlewares.user_sync import UserSyncMiddleware


class _Result:
    def first(self):
        return None


class Recorder:
    def __init__(self) -> None:
        self.rows: list[dict[str, Any]] = []

    def factory(self) -> Recorder:
        return self

    def __call__(self) -> Recorder:
        return self

    async def __aenter__(self) -> Recorder:
        return self

    async def __aexit__(self, *exc: object) -> bool:
        return False

    def begin(self) -> Recorder:
        return self

    async def execute(self, statement: Any, params: dict | None = None) -> _Result:
        assert "INSERT INTO users" in " ".join(str(statement).split())
        self.rows.append(dict(params or {}))
        return _Result()


def _user(**overrides: Any) -> User:
    fields = {"id": 42, "is_bot": False, "first_name": "Анна", "username": "ann"}
    fields.update(overrides)
    return User(**fields)


def _message_update(user: User | None) -> Update:
    message = Message(
        message_id=1,
        date=datetime.now(UTC),
        chat=Chat(id=42, type="private"),
        from_user=user,
        text="/start",
    )
    return Update(update_id=1, message=message)


def _callback_update(user: User) -> Update:
    query = CallbackQuery(
        id="cb-1", from_user=user, chat_instance="ci", data="b1:lang:en"
    )
    return Update(update_id=2, callback_query=query)


async def run(update: Any, recorder: Recorder) -> Any:
    middleware = UserSyncMiddleware(recorder.factory())  # type: ignore[arg-type]
    seen: list[Any] = []

    async def handler(event: Any, data: dict[str, Any]) -> str:
        seen.append(event)
        return "handled"

    result = await middleware(handler, update, {})
    assert result == "handled"
    assert seen == [update]
    return result


@pytest.mark.asyncio
async def test_start_message_inserts_the_row() -> None:
    recorder = Recorder()
    await run(_message_update(_user(last_name="Иванова")), recorder)

    assert recorder.rows == [
        {"id": 42, "username": "ann", "display_name": "Анна Иванова"}
    ]


@pytest.mark.asyncio
async def test_callback_query_inserts_the_row() -> None:
    recorder = Recorder()
    await run(_callback_update(_user()), recorder)

    assert recorder.rows == [{"id": 42, "username": "ann", "display_name": "Анна"}]


@pytest.mark.asyncio
async def test_missing_username_and_long_name_are_handled() -> None:
    recorder = Recorder()
    await run(_message_update(_user(username=None, first_name="А" * 90)), recorder)

    assert recorder.rows == [
        {"id": 42, "username": None, "display_name": "А" * 64}
    ]


@pytest.mark.asyncio
async def test_update_without_a_sender_writes_nothing() -> None:
    recorder = Recorder()
    await run(_message_update(None), recorder)
    await run(SimpleNamespace(update_id=3), recorder)

    assert recorder.rows == []