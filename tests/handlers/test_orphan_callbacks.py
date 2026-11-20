from __future__ import annotations

import datetime as dt
from typing import Any

import pytest
from aiogram import Dispatcher, F, Router
from aiogram.methods import AnswerCallbackQuery, EditMessageText, SendRichMessage
from aiogram.types import CallbackQuery, Chat, Message, Update, User

from app.bot import ORPHAN_TOAST, register_orphan_callbacks


class FakeBot:
    id = 1

    def __init__(self) -> None:
        self.answers: list[tuple[str, str | None]] = []
        self.edits: list[tuple[int, int]] = []
        self.sends: list[int] = []
        self.sent = object()

    async def answer_callback_query(self, callback_query_id: str, text: str | None = None, **_kwargs: Any) -> bool:
        self.answers.append((callback_query_id, text))
        return True

    async def edit_message_text(self, **kwargs: Any) -> object:
        self.edits.append((kwargs["chat_id"], kwargs["message_id"]))
        return self.sent

    async def send_rich_message(self, **kwargs: Any) -> object:
        self.sends.append(kwargs["chat_id"])
        return self.sent

    async def __call__(self, method: Any) -> object:
        if isinstance(method, AnswerCallbackQuery):
            return await self.answer_callback_query(
                method.callback_query_id, method.text
            )
        if isinstance(method, EditMessageText):
            self.edits.append((method.chat_id, method.message_id))
            return self.sent
        if isinstance(method, SendRichMessage):
            self.sends.append(method.chat_id)
            return self.sent
        raise AssertionError(f"unexpected method: {method!r}")


class _Result:
    def first(self) -> None:
        return None


class _Session:
    async def __aenter__(self) -> _Session:
        return self

    async def __aexit__(self, *_exc: object) -> bool:
        return False

    def begin(self) -> _Session:
        return self

    async def execute(self, *_args: object, **_kwargs: object) -> _Result:
        return _Result()


class FakeSessions:
    def __call__(self) -> _Session:
        return _Session()


class FakeRepo:
    def categories(self) -> list[object]:
        return []


def _update(data: str, chat_type: str = "private") -> Update:
    chat_id = 42 if chat_type == "private" else -100
    user = User(id=42, is_bot=False, first_name="Tester")
    message = Message(
        message_id=7,
        date=dt.datetime.now(dt.UTC),
        chat=Chat(id=chat_id, type=chat_type),
        text="menu",
    )
    query = CallbackQuery(id="cb-1", from_user=user, chat_instance="ci", data=data, message=message)
    return Update(update_id=1, callback_query=query)


def _dispatcher() -> Dispatcher:
    dispatcher = Dispatcher()
    dispatcher.workflow_data.update(
        repo=FakeRepo(), session_factory=FakeSessions(), settings=None
    )
    return dispatcher


@pytest.mark.asyncio
async def test_unknown_callback_is_answered_and_menu_restored() -> None:
    dispatcher = _dispatcher()
    register_orphan_callbacks(dispatcher)
    bot = FakeBot()

    await dispatcher.feed_update(bot, _update("b1:gone"))

    assert bot.answers == [("cb-1", ORPHAN_TOAST)]
    assert bot.edits == [(42, 7)]
    assert bot.sends == []


@pytest.mark.asyncio
async def test_unknown_group_callback_is_answered_without_edit() -> None:
    dispatcher = _dispatcher()
    register_orphan_callbacks(dispatcher)
    bot = FakeBot()

    await dispatcher.feed_update(bot, _update("b1:gone", chat_type="supergroup"))

    assert bot.answers == [("cb-1", ORPHAN_TOAST)]
    assert bot.edits == []


@pytest.mark.asyncio
async def test_known_callback_never_reaches_the_fallback() -> None:
    dispatcher = _dispatcher()
    handled: list[str | None] = []
    known = Router(name="known")

    @known.callback_query(F.data == "b1:known")
    async def on_known(query: CallbackQuery) -> None:
        handled.append(query.data)

    dispatcher.include_router(known)
    register_orphan_callbacks(dispatcher)
    bot = FakeBot()

    await dispatcher.feed_update(bot, _update("b1:known"))

    assert handled == ["b1:known"]
    assert bot.answers == []
    assert bot.edits == []