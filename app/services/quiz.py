"""Quiz session logic: seeded question pick, one-row-per-answer writes, score.

The screen is stateless — the seed rides in the callback. Determinism comes from
``random.Random(seed)`` over questions sorted by id, so a retried seed shows the
same order and the DB order from Postgres cannot shuffle it.
"""

from __future__ import annotations

import random
from collections.abc import Sequence

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.content.types import QuizView

QUESTIONS = 3

_OWNER_SQL = text(
    """
    SELECT telegram_id, chosen FROM quiz_answer
    WHERE threat_slug = :threat_slug
      AND question_id = :question_id
      AND session_seed = :session_seed
    ORDER BY created_at, id
    LIMIT 1
    """
)

_INSERT_SQL = text(
    """
    INSERT INTO quiz_answer
      (telegram_id, threat_slug, question_id, session_seed, chosen, correct)
    VALUES
      (:telegram_id, :threat_slug, :question_id, :session_seed, :chosen, :correct)
    ON CONFLICT (threat_slug, question_id, session_seed) DO NOTHING
    RETURNING id
    """
)

_STREAK_SQL = text(
    """
    SELECT correct FROM quiz_answer
    WHERE telegram_id = :telegram_id
    ORDER BY created_at, id
    """
)

_SCORE_SQL = text(
    """
    SELECT
      count(*) AS total,
      count(*) FILTER (WHERE correct) AS answered
    FROM quiz_answer
    WHERE telegram_id = :telegram_id
      AND threat_slug = :threat_slug
      AND session_seed = :session_seed
    """
)

SessionFactory = async_sessionmaker[AsyncSession]


def pick_questions(
    quizzes: Sequence[QuizView], seed: str, n: int = QUESTIONS
) -> list[QuizView]:
    """Pick up to ``n`` questions in the same order for the same ``seed``."""
    if n <= 0 or not quizzes:
        return []
    ordered = sorted(quizzes, key=lambda quiz: quiz.question_id)
    return random.Random(seed).sample(ordered, min(n, len(ordered)))


async def answer_of(
    session_factory: SessionFactory, slug: str, qid: str, seed: str
) -> tuple[int, int] | None:
    """``(telegram_id, chosen)`` for this seeded question, if it was answered."""
    params = {"threat_slug": slug, "question_id": qid, "session_seed": seed}
    async with session_factory() as session:
        row = (await session.execute(_OWNER_SQL, params)).first()
    return (int(row[0]), int(row[1])) if row is not None else None


async def owner_of(
    session_factory: SessionFactory, slug: str, qid: str, seed: str
) -> int | None:
    """Telegram id that answered this question under this seed, if any."""
    answer = await answer_of(session_factory, slug, qid, seed)
    return answer[0] if answer is not None else None


async def record_answer(
    session_factory: SessionFactory,
    tg_id: int,
    slug: str,
    qid: str,
    seed: str,
    chosen: int,
    correct: bool,
) -> bool:
    """Insert one answer; ``False`` means the session question is already taken."""
    params = {
        "telegram_id": tg_id,
        "threat_slug": slug,
        "question_id": qid,
        "session_seed": seed,
        "chosen": chosen,
        "correct": correct,
    }
    async with session_factory() as session:
        async with session.begin():
            row = (await session.execute(_INSERT_SQL, params)).first()
    return row is not None


async def score(
    session_factory: SessionFactory, tg_id: int, slug: str, seed: str
) -> tuple[int, int]:
    """Return ``(correct, total)`` answers this user gave under this seed."""
    params = {"telegram_id": tg_id, "threat_slug": slug, "session_seed": seed}
    async with session_factory() as session:
        row = (await session.execute(_SCORE_SQL, params)).mappings().first()
    if row is None:
        return (0, 0)
    return (int(row["answered"]), int(row["total"]))


async def streaks(
    session_factory: SessionFactory, tg_id: int
) -> tuple[int, int]:
    """``(current, best)`` runs of correct answers across every card.

    Ordered by answer time, so a wrong answer resets the current run and the
    best run is the longest one ever recorded for this user.
    """
    async with session_factory() as session:
        rows = (await session.execute(_STREAK_SQL, {"telegram_id": tg_id})).all()
    current = best = 0
    for (correct,) in rows:
        current = current + 1 if correct else 0
        best = max(best, current)
    return current, best


__all__ = [
    "answer_of",
    "owner_of",
    "pick_questions",
    "record_answer",
    "score",
    "streaks",
]