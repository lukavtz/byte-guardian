"""HTML widget quiz: document limits, escaping, query contract, handlers."""

from __future__ import annotations

import datetime as dt
import json
import zlib
from typing import Any

import pytest
from aiogram import Dispatcher
from aiogram.exceptions import TelegramBadRequest
from aiogram.types import (
    CallbackQuery,
    Chat,
    Message,
    User,
    WidgetQuery,
    WidgetQueryTypeData,
)

from app.content.types import CardView, QuizView
from app.handlers import widgets as widget_handler
from app.services.quiz import pick_questions
from app.ui.render import render_card
from app.widgets.quiz import HTML_LIMIT, ZLIB_LIMIT, build_quiz, query_data

TG_ID = 1001
OTHER_ID = 2002
SLUG = "fire-home"
SEED = "a3f1"


def _quiz(qid: str, answer: int = 0) -> QuizView:
    return QuizView(
        threat_slug=SLUG,
        question_id=qid,
        prompt=f"Prompt {qid}",
        options=("one", "two", "three", "four"),
        answer=answer,
        explanation=f"Because {qid}",
    )


def _quizzes() -> list[QuizView]:
    return [_quiz("q1"), _quiz("q2"), _quiz("q3"), _quiz("q4")]


class FakeDB:
    def __init__(self) -> None:
        self.rows: list[dict[str, Any]] = []

    def factory(self) -> FakeDB:
        return self

    def __call__(self) -> FakeDB:
        return self

    async def __aenter__(self) -> FakeDB:
        return self

    async def __aexit__(self, *exc: object) -> bool:
        return False

    def begin(self) -> FakeDB:
        return self

    async def execute(self, statement, params: dict[str, Any] | None = None) -> Any:
        params = params or {}
        sql = " ".join(str(statement).split())

        class Result:
            def __init__(self, rows: list[Any]) -> None:
                self.rows = rows

            def first(self) -> Any:
                return self.rows[0] if self.rows else None

            def mappings(self) -> Result:
                return self

        if sql.startswith("SELECT locale, locale_chosen FROM users"):
            return Result([])
        if sql.startswith("INSERT INTO quiz_answer"):
            if any(
                (row["threat_slug"], row["question_id"], row["session_seed"])
                == (params["threat_slug"], params["question_id"], params["session_seed"])
                for row in self.rows
            ):
                return Result([])
            self.rows.append(dict(params))
            return Result([{"id": "row-1"}])
        if sql.startswith("SELECT correct FROM quiz_answer"):
            for row in self.rows:
                if (
                    row["telegram_id"] == params["telegram_id"]
                    and row["threat_slug"] == params["threat_slug"]
                    and row["question_id"] == params["question_id"]
                    and row["session_seed"] == params["session_seed"]
                ):
                    return Result([(row["correct"],)])
            return Result([])
        if sql.startswith("SELECT telegram_id FROM quiz_answer"):
            for row in self.rows:
                if (
                    row["threat_slug"] == params["threat_slug"]
                    and row["question_id"] == params["question_id"]
                    and row["session_seed"] == params["session_seed"]
                ):
                    return Result([(row["telegram_id"],)])
            return Result([])
        if "AS total" in sql:
            picked = [
                row
                for row in self.rows
                if row["telegram_id"] == params["telegram_id"]
                and row["threat_slug"] == params["threat_slug"]
                and row["session_seed"] == params["session_seed"]
            ]
            return Result(
                [{"total": len(picked), "answered": sum(row["correct"] for row in picked)}]
            )
        raise AssertionError(f"unexpected SQL: {sql}")


class FakeRepo:
    def __init__(self, quizzes: list[QuizView] | None = None) -> None:
        self._quizzes = quizzes if quizzes is not None else _quizzes()

    def quizzes(self, slug: str, locale: str) -> list[QuizView]:
        return [quiz for quiz in self._quizzes if quiz.threat_slug == slug]

    def card(self, slug: str, locale: str) -> None:
        return None


class FakeBot:
    def __init__(self, widget_error: str | None = None) -> None:
        self.answers: list[tuple[str, str]] = []
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self.widget_error = widget_error
        self.sent = object()

    async def __call__(self, method: Any) -> object:
        self.calls.append((type(method).__name__, method.model_dump()))
        return True

    async def answer_widget_query(
        self, widget_query_id: str, data: str | None = None, **kwargs: Any
    ) -> bool:
        self.answers.append((widget_query_id, data or ""))
        return True

    async def send_widget(self, **kwargs: Any) -> object:
        if self.widget_error is not None:
            raise TelegramBadRequest(method=None, message=self.widget_error)
        self.calls.append(("send_widget", kwargs))
        return self.sent

    async def edit_message_text(self, **kwargs: Any) -> object:
        self.calls.append(("edit_message_text", kwargs))
        return self.sent


def _query(data: str, query_id: str = "wq-1") -> WidgetQuery:
    return WidgetQuery(
        id=query_id,
        from_user=User(id=TG_ID, is_bot=False, first_name="Tester"),
        chat_instance="ci",
        type=WidgetQueryTypeData(),
        data=data,
    )


def _callback(data: str, bot: FakeBot, chat_type: str = "private") -> CallbackQuery:
    user = User(id=TG_ID, is_bot=False, first_name="Tester")
    message = Message(
        message_id=10,
        date=dt.datetime.now(dt.UTC),
        chat=Chat(id=TG_ID if chat_type == "private" else -100, type=chat_type),
        text="card",
    )
    return CallbackQuery(
        id="cb-1", from_user=user, chat_instance="ci", data=data, message=message
    ).as_(bot)


def _payload(bot: FakeBot) -> dict[str, Any]:
    assert bot.answers, "handler never answered the widget query"
    return json.loads(bot.answers[-1][1])


def test_register_declares_widget_update_types() -> None:
    dp = Dispatcher()
    widget_handler.register(dp, None)  # type: ignore[arg-type]

    assert set(dp.workflow_data["allowed_updates_extra"]) == {
        "widget_query",
        "shared_widget_query",
    }
    assert {"widget_query", "shared_widget_query"} <= set(dp.resolve_used_update_types())


# --- document -------------------------------------------------------------


def test_document_stays_within_telegram_limits() -> None:
    html = build_quiz(pick_questions(_quizzes(), SEED), SLUG, SEED, "ru")

    assert len(html.encode("utf-8")) <= HTML_LIMIT
    assert len(zlib.compress(html.encode("utf-8"), 9)) <= ZLIB_LIMIT


def test_document_escapes_every_content_string() -> None:
    quiz = QuizView(
        threat_slug=SLUG,
        question_id="q1",
        prompt='<script>alert("x")</script>',
        options=("<b>one</b>", "two & three", "four", "five"),
        answer=0,
        explanation="unused",
    )
    html = build_quiz([quiz], SLUG, SEED, "ru")

    assert "<script>alert" not in html
    assert "&lt;script&gt;alert" in html
    assert "<b>one</b>" not in html
    assert "&lt;b&gt;one&lt;/b&gt;" in html
    assert "http://" not in html and "https://" not in html
    assert "@font-face" not in html and "<img" not in html


def _card() -> CardView:
    return CardView(
        slug=SLUG,
        category_slug="fire",
        category_title="Fire",
        severity=2,
        locale="ru",
        fallback=False,
        title="Fire at home",
        summary="Summary",
        signs=("smoke",),
        before=("plan",),
        during=("leave",),
        after=("call",),
        forbidden=("lift",),
        contacts=("112",),
        source="MCHS",
        icon="🔥",
    )


def test_card_offers_the_game_only_where_widgets_work() -> None:
    default = render_card(_card(), "ru", 1, True).html
    private = render_card(_card(), "ru", 1, True, can_game=True).html
    group = render_card(_card(), "ru", 1, True, can_game=False).html

    assert "b1:game:fire-home" not in default
    assert "b1:quiz_next:" in default
    assert "b1:game:fire-home" in private
    assert "b1:game:fire-home" not in group


def test_query_data_is_compact_and_bounded() -> None:
    data = query_data(SLUG, "q1", 2, SEED)

    assert json.loads(data) == {"s": SLUG, "q": "q1", "a": 2, "t": SEED}
    assert len(data.encode("utf-8")) <= 64


# --- handlers -------------------------------------------------------------


@pytest.mark.asyncio
async def test_right_answer_is_validated_and_recorded() -> None:
    db = FakeDB()
    bot = FakeBot()
    quiz = _quizzes()[0]

    await widget_handler.on_widget_query(
        query=_query(query_data(SLUG, quiz.question_id, quiz.answer, SEED)),
        bot=bot,
        repo=FakeRepo(),
        session_factory=db.factory(),
    )

    payload = _payload(bot)
    assert payload == {
        "ok": True,
        "correct": True,
        "explanation": f"Because {quiz.question_id}",
        "score": "1/3",
    }
    assert db.rows[0]["correct"] is True
    assert db.rows[0]["chosen"] == quiz.answer


@pytest.mark.asyncio
async def test_wrong_answer_returns_correct_false() -> None:
    db = FakeDB()
    bot = FakeBot()

    await widget_handler.on_widget_query(
        query=_query(query_data(SLUG, "q1", 2, SEED)),
        bot=bot,
        repo=FakeRepo(),
        session_factory=db.factory(),
    )

    payload = _payload(bot)
    assert payload["ok"] is True
    assert payload["correct"] is False
    assert payload["explanation"].startswith("Because")
    assert db.rows[0]["correct"] is False


@pytest.mark.asyncio
async def test_unknown_question_and_bad_json_are_refused() -> None:
    db = FakeDB()
    bot = FakeBot()

    for data in ("not json", query_data(SLUG, "missing", 0, SEED)):
        await widget_handler.on_widget_query(
            query=_query(data),
            bot=bot,
            repo=FakeRepo(),
            session_factory=db.factory(),
        )

    assert [json.loads(payload) for _, payload in bot.answers] == [
        {"ok": False},
        {"ok": False},
    ]
    assert db.rows == []


@pytest.mark.asyncio
async def test_retry_reports_the_stored_verdict() -> None:
    db = FakeDB()
    bot = FakeBot()

    for chosen in (0, 2):
        await widget_handler.on_widget_query(
            query=_query(query_data(SLUG, "q1", chosen, SEED)),
            bot=bot,
            repo=FakeRepo(),
            session_factory=db.factory(),
        )

    assert _payload(bot)["ok"] is True
    assert _payload(bot)["correct"] is True
    assert _payload(bot)["score"] == "1/3"
    assert len(db.rows) == 1
    assert db.rows[0]["chosen"] == 0


@pytest.mark.asyncio
async def test_foreign_seed_is_refused() -> None:
    db = FakeDB()
    bot = FakeBot()
    db.rows.append(
        {
            "telegram_id": OTHER_ID,
            "threat_slug": SLUG,
            "question_id": "q1",
            "session_seed": SEED,
            "chosen": 0,
            "correct": True,
        }
    )

    await widget_handler.on_widget_query(
        query=_query(query_data(SLUG, "q1", 0, SEED)),
        bot=bot,
        repo=FakeRepo(),
        session_factory=db.factory(),
    )

    assert _payload(bot) == {"ok": False}
    assert len(db.rows) == 1


@pytest.mark.asyncio
async def test_game_button_sends_the_widget() -> None:
    bot = FakeBot()

    await widget_handler.on_game(
        query=_callback("b1:game:fire-home", bot),
        bot=bot,
        repo=FakeRepo(),
        session_factory=FakeDB().factory(),
    )

    sent = [payload for name, payload in bot.calls if name == "send_widget"]
    assert len(sent) == 1
    assert sent[0]["allow_queries"] is True
    assert (sent[0]["width"], sent[0]["height"]) == (360, 480)
    assert len(sent[0]["html"].encode("utf-8")) <= HTML_LIMIT


@pytest.fixture(autouse=True)
def _open_widget_gate(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(widget_handler, "_widget_refused_until", 0.0)


@pytest.mark.asyncio
async def test_refused_widget_falls_back_to_the_rich_quiz() -> None:
    bot = FakeBot(widget_error="Bad Request: INPUT_MEDIA_WIDGET_NOT_ALLOWED")

    await widget_handler.on_game(
        query=_callback("b1:game:fire-home", bot),
        bot=bot,
        repo=FakeRepo(),
        session_factory=FakeDB().factory(),
    )

    edits = [payload for name, payload in bot.calls if name == "edit_message_text"]
    assert len(edits) == 1
    html = edits[0]["rich_message"].html
    assert "Вопрос 1 из 3" in html
    assert "Мини-игра сейчас недоступна" in html


@pytest.mark.asyncio
async def test_closed_widget_gate_skips_the_api_call() -> None:
    refused = FakeBot(widget_error="Bad Request: INPUT_MEDIA_WIDGET_NOT_ALLOWED")
    await widget_handler.on_game(
        query=_callback("b1:game:fire-home", refused),
        bot=refused,
        repo=FakeRepo(),
        session_factory=FakeDB().factory(),
    )

    # Telegram's refusal must not cost a round-trip on every later tap.
    second = FakeBot()
    await widget_handler.on_game(
        query=_callback("b1:game:fire-home", second),
        bot=second,
        repo=FakeRepo(),
        session_factory=FakeDB().factory(),
    )

    assert not [name for name, _ in second.calls if name == "send_widget"]
    assert [name for name, _ in second.calls].count("edit_message_text") == 1


@pytest.mark.asyncio
async def test_widget_gate_expires(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        widget_handler, "_widget_refused_until", 1.0
    )  # a timestamp from the past
    bot = FakeBot()

    await widget_handler.on_game(
        query=_callback("b1:game:fire-home", bot),
        bot=bot,
        repo=FakeRepo(),
        session_factory=FakeDB().factory(),
    )

    assert len([name for name, _ in bot.calls if name == "send_widget"]) == 1


@pytest.mark.asyncio
async def test_payload_refusal_keeps_the_widget_retryable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # A real bug of ours (HTML too large) must stay visible and must not be
    # mistaken for the platform gate.
    bot = FakeBot(widget_error="Bad Request: INPUT_MEDIA_WIDGET_TOO_LARGE")
    await widget_handler.on_game(
        query=_callback("b1:game:fire-home", bot),
        bot=bot,
        repo=FakeRepo(),
        session_factory=FakeDB().factory(),
    )

    assert widget_handler._widget_allowed()
    assert not [name for name, _ in bot.calls if name == "send_widget"]


@pytest.mark.asyncio
async def test_group_callback_never_sends_a_widget() -> None:
    bot = FakeBot()
    query = _callback("b1:game:fire-home", bot, chat_type="group")

    await widget_handler.on_game(
        query=query,
        bot=bot,
        repo=FakeRepo(),
        session_factory=FakeDB().factory(),
    )

    assert not [name for name, _ in bot.calls if name == "send_widget"]
