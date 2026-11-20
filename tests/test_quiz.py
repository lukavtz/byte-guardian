"""Offline quiz tests: seeded picks, idempotent answers, ownership, screens."""

from __future__ import annotations

import datetime as dt
from typing import Any

import pytest
from aiogram.types import CallbackQuery, Chat, InputRichMessage, Message, User

from app.content.types import QuizView
from app.services.callbacks import cb_quiz, cb_quiz_next
from app.services.quiz import answer_of, owner_of, pick_questions, record_answer, score, streaks

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


class _Result:
    def __init__(self, rows: list[Any]) -> None:
        self.rows = rows

    def first(self) -> Any:
        return self.rows[0] if self.rows else None

    def mappings(self) -> _Result:
        return self

    def all(self) -> list[Any]:
        return list(self.rows)

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

    async def execute(self, statement, params: dict[str, Any] | None = None) -> _Result:
        return self.db.execute(statement, params or {})


class FakeDB:
    def __init__(self) -> None:
        self.rows: list[dict[str, Any]] = []

    def factory(self) -> FakeSessionFactory:
        return FakeSessionFactory(self)

    def _key(self, params: dict[str, Any]) -> tuple[Any, ...]:
        return (
            params["threat_slug"],
            params["question_id"],
            params["session_seed"],
        )

    def execute(self, statement, params: dict[str, Any]) -> _Result:
        sql = " ".join(str(statement).split())
        if sql.startswith("SELECT locale, locale_chosen FROM users"):
            return _Result([])
        if sql.startswith("INSERT INTO quiz_answer"):
            key = self._key(params)
            if any(self._key(row) == key for row in self.rows):
                return _Result([])
            self.rows.append(dict(params))
            return _Result([{"id": "row-1"}])
        if sql.startswith("SELECT telegram_id, chosen FROM quiz_answer"):
            for row in self.rows:
                if (
                    row["threat_slug"] == params["threat_slug"]
                    and row["question_id"] == params["question_id"]
                    and row["session_seed"] == params["session_seed"]
                ):
                    return _Result([(row["telegram_id"], row["chosen"])])
            return _Result([])
        if sql.startswith("SELECT correct FROM quiz_answer"):
            return _Result(
                [
                    (row["correct"],)
                    for row in self.rows
                    if row["telegram_id"] == params["telegram_id"]
                ]
            )
        if "AS total" in sql:
            picked = [
                row
                for row in self.rows
                if row["telegram_id"] == params["telegram_id"]
                and row["threat_slug"] == params["threat_slug"]
                and row["session_seed"] == params["session_seed"]
            ]
            answered = sum(1 for row in picked if row["correct"])
            return _Result([{"total": len(picked), "answered": answered}])
        raise AssertionError(f"unexpected SQL: {sql}")


class FakeSessionFactory:
    def __init__(self, db: FakeDB) -> None:
        self.db = db

    def __call__(self) -> _Session:
        return _Session(self.db)


class FakeBot:
    """Records API calls; ``__call__`` serves the awaited callback answers."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self.sent = object()

    async def __call__(self, method: Any) -> object:
        self.calls.append((type(method).__name__, method.model_dump()))
        return True

    async def send_rich_message(self, **kwargs: Any) -> object:
        self.calls.append(("send_rich_message", kwargs))
        return self.sent

    async def edit_message_text(self, **kwargs: Any) -> object:
        self.calls.append(("edit_message_text", kwargs))
        return self.sent


class FakeRepo:
    def __init__(self, quizzes: list[QuizView]) -> None:
        self._quizzes = quizzes

    def quizzes(self, slug: str, locale: str) -> list[QuizView]:
        return [quiz for quiz in self._quizzes if quiz.threat_slug == slug]


def _callback(data: str, bot: FakeBot, user_id: int = TG_ID) -> CallbackQuery:
    user = User(id=user_id, is_bot=False, first_name="Tester")
    message = Message(
        message_id=10,
        date=dt.datetime.now(dt.UTC),
        chat=Chat(id=user_id, type="private"),
        text="card",
    )
    query = CallbackQuery(
        id="cb-1", from_user=user, chat_instance="ci", data=data, message=message
    )
    return query.as_(bot)


def _screen_html(call: tuple[str, dict[str, Any]]) -> str:
    rich: InputRichMessage = call[1]["rich_message"]
    assert isinstance(rich, InputRichMessage)
    assert rich.html is not None
    return rich.html


def _answers(bot: FakeBot) -> list[dict[str, Any]]:
    return [payload for name, payload in bot.calls if name == "AnswerCallbackQuery"]


# --- service --------------------------------------------------------------


def test_pick_is_deterministic_for_one_seed() -> None:
    first = pick_questions(_quizzes(), SEED)
    second = pick_questions(_quizzes(), SEED)

    assert [quiz.question_id for quiz in first] == [quiz.question_id for quiz in second]
    assert len(first) == 3


def test_pick_ignores_input_order() -> None:
    straight = pick_questions(_quizzes(), SEED)
    shuffled = pick_questions(list(reversed(_quizzes())), SEED)

    assert [quiz.question_id for quiz in straight] == [
        quiz.question_id for quiz in shuffled
    ]


def test_pick_caps_at_three_and_keeps_fewer() -> None:
    assert len(pick_questions(_quizzes(), SEED)) == 3
    assert [quiz.question_id for quiz in pick_questions([_quiz("q1")], SEED)] == ["q1"]
    assert pick_questions([], SEED) == []


@pytest.mark.asyncio
async def test_record_answer_is_idempotent() -> None:
    db = FakeDB()
    factory = db.factory()

    assert await record_answer(factory, TG_ID, SLUG, "q1", SEED, 0, True) is True
    assert await record_answer(factory, TG_ID, SLUG, "q1", SEED, 1, False) is False
    assert len(db.rows) == 1


@pytest.mark.asyncio
async def test_score_counts_answers_for_one_seed() -> None:
    db = FakeDB()
    factory = db.factory()
    await record_answer(factory, TG_ID, SLUG, "q1", SEED, 0, True)
    await record_answer(factory, TG_ID, SLUG, "q2", SEED, 0, True)
    await record_answer(factory, TG_ID, SLUG, "q3", SEED, 1, False)
    await record_answer(factory, TG_ID, SLUG, "q4", "ffff", 0, True)

    assert await score(factory, TG_ID, SLUG, SEED) == (2, 3)
    assert await score(factory, TG_ID, SLUG, "0000") == (0, 0)


@pytest.mark.asyncio
async def test_owner_is_reported() -> None:
    db = FakeDB()
    factory = db.factory()
    await record_answer(factory, OTHER_ID, SLUG, "q1", SEED, 0, True)

    assert await owner_of(factory, SLUG, "q1", SEED) == OTHER_ID
    assert await owner_of(factory, SLUG, "q2", SEED) is None


@pytest.mark.asyncio
async def test_streaks_count_runs_across_cards() -> None:
    db = FakeDB()

    def add(slug: str, correct: bool) -> None:
        db.rows.append(
            {
                "telegram_id": TG_ID,
                "threat_slug": slug,
                "question_id": f"q-{len(db.rows)}",
                "session_seed": SEED,
                "chosen": 0,
                "correct": correct,
            }
        )

    add("fire-home", True)
    add("fire-home", True)
    add("chem-smoke", False)
    add("rad-leak", True)
    db.rows.append(
        {
            "telegram_id": OTHER_ID,
            "threat_slug": "rad-leak",
            "question_id": "q-x",
            "session_seed": SEED,
            "chosen": 0,
            "correct": True,
        }
    )

    assert await streaks(db.factory(), TG_ID) == (1, 2)
    assert await streaks(db.factory(), OTHER_ID) == (1, 1)
    assert await streaks(db.factory(), 404) == (0, 0)


def test_quiz_answer_has_one_row_per_session_question() -> None:
    from sqlalchemy import UniqueConstraint

    from app.db.content import QuizAnswer

    unique = {
        constraint.name: tuple(column.name for column in constraint.columns)
        for constraint in QuizAnswer.__table__.constraints
        if isinstance(constraint, UniqueConstraint)
    }
    assert unique["uq_quiz_answer_session"] == (
        "threat_slug",
        "question_id",
        "session_seed",
    )


# --- handler --------------------------------------------------------------


@pytest.mark.asyncio
async def test_three_answers_walk_to_the_result() -> None:
    from app.handlers import quiz as quiz_handler

    db = FakeDB()
    bot = FakeBot()
    repo = FakeRepo(_quizzes())
    questions = pick_questions(_quizzes(), SEED)

    for position, quiz in enumerate(questions):
        await quiz_handler.quiz_callback(
            callback=_callback(cb_quiz(SLUG, quiz.question_id, quiz.answer, SEED), bot),
            bot=bot,
            repo=repo,
            session_factory=db.factory(),
        )
        # The answer stays on screen with its verdict, not the next question.
        answer_call = bot.calls[-1]
        assert answer_call[0] == "edit_message_text"
        assert answer_call[1]["message_id"] == 10
        html = _screen_html(answer_call)
        assert quiz.prompt in html
        assert f"Вопрос {position + 1} из 3" in html
        assert quiz.explanation in html

        await quiz_handler.quiz_callback(
            callback=_callback(cb_quiz_next(SLUG, quiz.question_id, SEED), bot),
            bot=bot,
            repo=repo,
            session_factory=db.factory(),
        )
        html = _screen_html(bot.calls[-1])
        if position < len(questions) - 1:
            assert questions[position + 1].prompt in html
            assert f"Вопрос {position + 2} из 3" in html
        else:
            assert "3 из 3" in html

    assert len(db.rows) == 3
    assert all(row["correct"] for row in db.rows)


@pytest.mark.asyncio
async def test_wrong_option_is_recorded_as_incorrect() -> None:
    from app.handlers import quiz as quiz_handler

    db = FakeDB()
    bot = FakeBot()
    questions = pick_questions(_quizzes(), SEED)
    quiz = questions[0]
    wrong = (quiz.answer + 1) % len(quiz.options)

    await quiz_handler.quiz_callback(
        callback=_callback(cb_quiz(SLUG, quiz.question_id, wrong, SEED), bot),
        bot=bot,
        repo=FakeRepo(_quizzes()),
        session_factory=db.factory(),
    )

    assert db.rows[0]["correct"] is False
    assert db.rows[0]["chosen"] == wrong


@pytest.mark.asyncio
async def test_repeat_click_does_not_write_twice() -> None:
    from app.handlers import quiz as quiz_handler

    db = FakeDB()
    bot = FakeBot()
    questions = pick_questions(_quizzes(), SEED)
    quiz = questions[0]
    data = cb_quiz(SLUG, quiz.question_id, quiz.answer, SEED)

    for _ in range(2):
        await quiz_handler.quiz_callback(
            callback=_callback(data, bot),
            bot=bot,
            repo=FakeRepo(_quizzes()),
            session_factory=db.factory(),
        )

    assert len(db.rows) == 1
    html = _screen_html(bot.calls[-1])
    assert quiz.prompt in html
    assert "Вопрос 1 из 3" in html


@pytest.mark.asyncio
async def test_second_tap_on_another_option_keeps_the_stored_verdict() -> None:
    from app.handlers import quiz as quiz_handler

    db = FakeDB()
    bot = FakeBot()
    quiz = pick_questions(_quizzes(), SEED)[0]
    wrong = (quiz.answer + 1) % len(quiz.options)

    await quiz_handler.quiz_callback(
        callback=_callback(cb_quiz(SLUG, quiz.question_id, quiz.answer, SEED), bot),
        bot=bot,
        repo=FakeRepo(_quizzes()),
        session_factory=db.factory(),
    )
    await quiz_handler.quiz_callback(
        callback=_callback(cb_quiz(SLUG, quiz.question_id, wrong, SEED), bot),
        bot=bot,
        repo=FakeRepo(_quizzes()),
        session_factory=db.factory(),
    )

    assert len(db.rows) == 1 and db.rows[0]["chosen"] == quiz.answer
    html = _screen_html(bot.calls[-1])
    assert 'style="success"' in html and 'style="danger"' not in html
    assert await answer_of(db.factory(), SLUG, quiz.question_id, SEED) == (
        TG_ID,
        quiz.answer,
    )


@pytest.mark.asyncio
async def test_foreign_click_shows_alert_and_writes_nothing() -> None:
    from app.handlers import quiz as quiz_handler

    db = FakeDB()
    bot = FakeBot()
    quiz = pick_questions(_quizzes(), SEED)[0]
    db.rows.append(
        {
            "telegram_id": OTHER_ID,
            "threat_slug": SLUG,
            "question_id": quiz.question_id,
            "session_seed": SEED,
            "chosen": quiz.answer,
            "correct": True,
        }
    )

    await quiz_handler.quiz_callback(
        callback=_callback(
            cb_quiz(SLUG, quiz.question_id, quiz.answer, SEED), bot, user_id=TG_ID
        ),
        bot=bot,
        repo=FakeRepo(_quizzes()),
        session_factory=db.factory(),
    )

    alerts = _answers(bot)
    assert alerts and alerts[0]["show_alert"] is True
    assert len(db.rows) == 1


@pytest.mark.asyncio
async def test_concurrent_winner_blocks_the_loser() -> None:
    from app.handlers import quiz as quiz_handler

    class RacingDB(FakeDB):
        """Another user lands the same session row first; our insert loses."""

        def execute(self, statement, params: dict[str, Any]) -> _Result:
            sql = " ".join(str(statement).split())
            if sql.startswith("INSERT INTO quiz_answer"):
                self.rows.append({**params, "telegram_id": OTHER_ID})
                return _Result([])
            return super().execute(statement, params)

    db = RacingDB()
    bot = FakeBot()
    quiz = pick_questions(_quizzes(), SEED)[0]

    await quiz_handler.quiz_callback(
        callback=_callback(cb_quiz(SLUG, quiz.question_id, quiz.answer, SEED), bot),
        bot=bot,
        repo=FakeRepo(_quizzes()),
        session_factory=db.factory(),
    )

    alerts = _answers(bot)
    assert alerts and alerts[0]["show_alert"] is True
    assert [row["telegram_id"] for row in db.rows] == [OTHER_ID]
    assert not [name for name, _ in bot.calls if name == "send_rich_message"]


@pytest.mark.asyncio
async def test_retry_starts_a_fresh_seed() -> None:
    from app.handlers import quiz as quiz_handler

    db = FakeDB()
    bot = FakeBot()

    await quiz_handler.quiz_callback(
        callback=_callback(cb_quiz_next(SLUG, "start", "ffff"), bot),
        bot=bot,
        repo=FakeRepo(_quizzes()),
        session_factory=db.factory(),
    )

    assert db.rows == []
    html = _screen_html(bot.calls[-1])
    assert "Вопрос 1 из 3" in html
    assert pick_questions(_quizzes(), "ffff")[0].prompt in html


@pytest.mark.asyncio
async def test_card_without_questions_gets_an_alert() -> None:
    from app.handlers import quiz as quiz_handler

    db = FakeDB()
    bot = FakeBot()

    await quiz_handler.quiz_callback(
        callback=_callback(cb_quiz_next(SLUG, "start", SEED), bot),
        bot=bot,
        repo=FakeRepo([]),
        session_factory=db.factory(),
    )

    alerts = _answers(bot)
    assert alerts and alerts[0]["show_alert"] is True
    assert not [name for name, _ in bot.calls if name == "send_rich_message"]