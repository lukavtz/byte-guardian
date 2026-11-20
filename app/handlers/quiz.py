"""Quiz callbacks: answer a question, advance, or show the seeded result."""

from __future__ import annotations

from aiogram import Bot, Dispatcher, F, Router
from aiogram.types import CallbackQuery, Message
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.content.repository import ContentRepository
from app.content.types import QuizView
from app.services.callbacks import parse_cb
from app.services.i18n import resolve_locale, t
from app.services.quiz import owner_of, pick_questions, record_answer, score
from app.ui.delivery import show
from app.ui.render import render_question, render_result

router = Router(name="quiz")

START = "start"


async def _alert(callback: CallbackQuery, locale: str) -> None:
    await callback.answer(t(locale, "errors.generic"), show_alert=True)


def _locale(callback: CallbackQuery) -> str:
    user = callback.from_user
    return resolve_locale(user.language_code if user is not None else None)


def _index_of(questions: list[QuizView], qid: str) -> int | None:
    if qid == START:
        return 0
    return next(
        (position for position, quiz in enumerate(questions) if quiz.question_id == qid),
        None,
    )


@router.callback_query(F.data.startswith("b1:quiz"))
async def quiz_callback(
    callback: CallbackQuery,
    bot: Bot,
    repo: ContentRepository,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    parsed = parse_cb(callback.data)
    if parsed is None or callback.from_user is None:
        await callback.answer()
        return
    message = callback.message
    if not isinstance(message, Message):
        await callback.answer()
        return

    locale = _locale(callback)
    slug = parsed.arg
    seed = parsed.extra[-1]
    questions = pick_questions(repo.quizzes(slug, locale), seed)
    if not questions:
        await _alert(callback, locale)
        return

    if parsed.action == "quiz":
        qid, chosen = parsed.extra[0], int(parsed.extra[1])
    else:
        qid, chosen = parsed.extra[0], None

    index = _index_of(questions, qid)
    if index is None or (parsed.action == "quiz_next" and index >= len(questions)):
        await _alert(callback, locale)
        return

    if parsed.action == "quiz":
        quiz = questions[index]
        assert chosen is not None
        if chosen >= len(quiz.options):
            await _alert(callback, locale)
            return
        owner = await owner_of(session_factory, slug, quiz.question_id, seed)
        if owner is not None and owner != callback.from_user.id:
            await _alert(callback, locale)
            return
        await callback.answer()
        inserted = await record_answer(
            session_factory,
            callback.from_user.id,
            slug,
            quiz.question_id,
            seed,
            chosen,
            chosen == quiz.answer,
        )
        if not inserted:
            screen = render_question(
                quiz, slug, index + 1, len(questions), seed, locale
            )
            await show(bot, message.chat.id, screen, message.message_id)
            return
        next_index = index + 1
    else:
        next_index = index if qid == START else index + 1
        await callback.answer()

    if next_index < len(questions):
        screen = render_question(
            questions[next_index], slug, next_index + 1, len(questions), seed, locale
        )
    else:
        correct, total = await score(
            session_factory, callback.from_user.id, slug, seed
        )
        screen = render_result(correct, total, slug, locale)

    await show(bot, message.chat.id, screen, message.message_id)


def register(dp: Dispatcher, _bot: Bot) -> None:
    dp.include_router(router)