"""HTML widget quiz: the game button and the ``widget_query`` answers.

The widget holds only prompts, options and a session seed. Every tap comes back
here, where the chosen option is checked against the database before anything
is written; the widget never learns the right answer in advance.
"""

from __future__ import annotations

import json
import logging
import secrets
from html import escape

from aiogram import Bot, Dispatcher, F, Router
from aiogram.exceptions import TelegramBadRequest
from aiogram.types import (
    CallbackQuery,
    Message,
    SharedWidgetQuery,
    WidgetQuery,
    WidgetQueryTypeData,
)
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.content.repository import ContentRepository
from app.content.types import QuizView
from app.services.callbacks import parse_cb
from app.services.i18n import t
from app.services.quiz import (
    QUESTIONS,
    owner_of,
    pick_questions,
    record_answer,
    score,
)
from app.services.user_locale import get_locale
from app.ui.delivery import show
from app.ui.render import RichScreen, render_question
from app.widgets.quiz import build_quiz

logger = logging.getLogger(__name__)

router = Router(name="widgets")

WIDGET_WIDTH = 360
WIDGET_HEIGHT = 480
EXPLANATION_LIMIT = 200
TITLE_LIMIT = 100
HEX = frozenset("0123456789abcdef")

SessionFactory = async_sessionmaker[AsyncSession]

_STORED_SQL = text(
    """
    SELECT correct FROM quiz_answer
    WHERE telegram_id = :telegram_id
      AND threat_slug = :threat_slug
      AND question_id = :question_id
      AND session_seed = :session_seed
    ORDER BY created_at, id
    LIMIT 1
    """
)


def _parse(data: str) -> tuple[str, str, int, str] | None:
    """``{"s":slug,"q":qid,"a":n,"t":seed}`` -> the four values, or ``None``."""
    try:
        raw = json.loads(data)
    except (TypeError, ValueError):
        return None
    if not isinstance(raw, dict):
        return None
    slug, qid, answer, seed = raw.get("s"), raw.get("q"), raw.get("a"), raw.get("t")
    if not isinstance(slug, str) or not isinstance(qid, str) or not isinstance(seed, str):
        return None
    if not slug or ":" in slug or not qid or ":" in qid:
        return None
    if len(seed) != 4 or not set(seed) <= HEX:
        return None
    if isinstance(answer, bool) or not isinstance(answer, int) or not 0 <= answer <= 3:
        return None
    return slug, qid, answer, seed


def _clip(value: object, limit: int) -> str:
    text = " ".join(str(value).split())
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


async def _stored_correct(
    session_factory: SessionFactory, user_id: int, slug: str, qid: str, seed: str
) -> bool | None:
    """Verdict already stored for this user, or ``None`` when another user owns it."""
    params = {
        "telegram_id": user_id,
        "threat_slug": slug,
        "question_id": qid,
        "session_seed": seed,
    }
    async with session_factory() as session:
        row = (await session.execute(_STORED_SQL, params)).first()
    return bool(row[0]) if row is not None else None


async def _answer(bot: Bot, query_id: str, payload: dict[str, object]) -> None:
    await bot.answer_widget_query(
        widget_query_id=query_id,
        data=json.dumps(payload, ensure_ascii=False, separators=(",", ":")),
    )


async def _handle(
    bot: Bot,
    query_id: str,
    user_id: int,
    data: str,
    repo: ContentRepository,
    session_factory: SessionFactory,
) -> None:
    parsed = _parse(data)
    if parsed is None:
        await _answer(bot, query_id, {"ok": False})
        return
    slug, qid, chosen, seed = parsed
    locale = await get_locale(session_factory, user_id, None)
    quizzes: list[QuizView] = repo.quizzes(slug, locale)
    quiz = next((item for item in quizzes if item.question_id == qid), None)
    if quiz is None or chosen >= len(quiz.options):
        await _answer(bot, query_id, {"ok": False})
        return

    correct = chosen == quiz.answer
    inserted = await record_answer(
        session_factory, user_id, slug, qid, seed, chosen, correct
    )
    if not inserted:
        # A retry after a lost answer must report the stored verdict, never the
        # one this tap just proposed.
        owner = await owner_of(session_factory, slug, qid, seed)
        if owner != user_id:
            await _answer(bot, query_id, {"ok": False})
            return
        stored = await _stored_correct(session_factory, user_id, slug, qid, seed)
        if stored is None:
            await _answer(bot, query_id, {"ok": False})
            return
        correct = stored

    correct_count, _total = await score(session_factory, user_id, slug, seed)
    total = min(QUESTIONS, len(quizzes)) or QUESTIONS
    correct_count = min(correct_count, total)
    await _answer(
        bot,
        query_id,
        {
            "ok": True,
            "correct": correct,
            "explanation": escape(_clip(quiz.explanation, EXPLANATION_LIMIT)),
            "score": f"{correct_count}/{total}",
        },
    )


@router.widget_query()
async def on_widget_query(
    query: WidgetQuery,
    bot: Bot,
    repo: ContentRepository,
    session_factory: SessionFactory,
) -> None:
    if not isinstance(query.type, WidgetQueryTypeData):
        await _answer(bot, query.id, {"ok": False})
        return
    await _handle(
        bot, query.id, query.from_user.id, query.data, repo, session_factory
    )


@router.shared_widget_query()
async def on_shared_widget_query(
    query: SharedWidgetQuery,
    bot: Bot,
    repo: ContentRepository,
    session_factory: SessionFactory,
) -> None:
    if not isinstance(query.type, WidgetQueryTypeData):
        await _answer(bot, query.id, {"ok": False})
        return
    await _handle(
        bot, query.id, query.from_user.id, query.data, repo, session_factory
    )


@router.callback_query(F.data.startswith("b1:game:"))
async def on_game(
    query: CallbackQuery,
    bot: Bot,
    repo: ContentRepository,
    session_factory: SessionFactory,
) -> None:
    parsed = parse_cb(query.data)
    message = query.message
    if parsed is None or parsed.action != "game" or not isinstance(message, Message):
        await query.answer()
        return
    locale = await get_locale(
        session_factory, query.from_user.id, query.from_user.language_code
    )
    if message.chat.type != "private":
        await query.answer(t(locale, "game.private"), show_alert=True)
        return

    seed = secrets.token_hex(2)
    questions = pick_questions(repo.quizzes(parsed.arg, locale), seed)
    if not questions:
        await query.answer(t(locale, "errors.generic"), show_alert=True)
        return
    await query.answer()

    card = repo.card(parsed.arg, locale)
    caption = (
        t(locale, "game.caption", title=_clip(card.title, TITLE_LIMIT))
        if card is not None
        else t(locale, "game.title")
    )
    try:
        await bot.send_widget(
            chat_id=message.chat.id,
            html=build_quiz(questions, parsed.arg, seed, locale),
            caption=escape(caption),
            allow_queries=True,
            width=WIDGET_WIDTH,
            height=WIDGET_HEIGHT,
        )
    except TelegramBadRequest:
        # Older clients and bots without Bot API 10.4 get the rich quiz instead.
        logger.warning("widget refused; falling back to the rich quiz", exc_info=True)
        screen = render_question(
            questions[0], parsed.arg, 1, len(questions), seed, locale
        )
        note = f"<p>{escape(t(locale, 'game.fallback'))}</p>"
        await show(
            bot,
            message.chat.id,
            RichScreen(screen.html + note, screen.keyboard),
            message.message_id,
        )


def register(dp: Dispatcher, _bot: Bot) -> None:
    extra = dp.workflow_data.setdefault("allowed_updates_extra", [])
    extra.extend(["widget_query", "shared_widget_query"])
    dp.include_router(router)
