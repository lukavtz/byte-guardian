from __future__ import annotations

import asyncio
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from aiogram import Bot, Dispatcher, F
from aiogram.filters import Command
from aiogram.types import CallbackQuery, Message
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.config import Settings
from app.handlers.admin.broadcast import (
    audience_count,
    broadcast_screen,
    last_broadcast,
)
from app.handlers.admin.common import (
    ADMIN_ONLY_ALERT,
    BROADCAST_HINT,
    EXTRA_PROMPTS,
    FIELD_LABELS,
    SLUG_PATTERN,
    TOAST_CANCELLED,
    _esc,
    _probes,
    _redraw,
    _repo,
    _send_new,
    prompt_screen,
    router,
)
from app.handlers.admin.content import (
    QUIZ_PENDING,
    _editor,
    add_category_screen,
    category_editor_screen,
    content_screen,
    quiz_question_screen,
    quiz_screen,
    threat_editor_screen,
    threats_screen,
)
from app.middlewares.allowlist import is_admin
from app.runtime.health import Probe, probe
from app.services import content_admin
from app.services import settings as settings_service
from app.services.callbacks import (
    cb_admin,
    cb_admin_broadcast,
    cb_admin_cancel,
    cb_admin_category_screen,
    cb_admin_content,
    cb_admin_question,
    cb_admin_threats,
    parse_cb,
)
from app.services.i18n import DEFAULT_LOCALE, resolve_locale, t
from app.ui.delivery import show
from app.ui.emoji import premium
from app.ui.keyboards import menu_button, rich_button, rich_row
from app.ui.render import RichScreen

STATS_SQL = text(
    """
    SELECT
      (SELECT count(*) FROM users) AS users,
      (SELECT count(*) FROM threat) AS threats,
      (SELECT count(*) FROM quiz_question) AS quizzes,
      (SELECT count(*) FROM quiz_answer) AS answers
    """
)
CATEGORIES_SQL = text(
    "SELECT slug, enabled, titles, emoji FROM category ORDER BY sort, slug"
)
TOGGLE_SQL = text(
    "UPDATE category SET enabled = NOT enabled WHERE slug = :slug RETURNING enabled"
)


@dataclass(frozen=True, slots=True)
class CategoryRow:
    """One category toggle: stable slug, display title, current state."""

    slug: str
    enabled: bool
    title: str
    emoji: str


def _title(titles: Any, slug: str) -> str:
    if isinstance(titles, dict):
        value = titles.get("ru") or next(iter(titles.values()), "")
        if isinstance(value, str) and value.strip():
            return value
    return slug


async def admin_stats(session_factory: async_sessionmaker[AsyncSession]) -> dict[str, int]:
    async with session_factory() as session:
        async with session.begin():
            row = (await session.execute(STATS_SQL)).mappings().first()
    if row is None:
        return {"users": 0, "threats": 0, "quizzes": 0, "answers": 0}
    return {key: int(row[key]) for key in ("users", "threats", "quizzes", "answers")}


async def category_rows(
    session_factory: async_sessionmaker[AsyncSession],
) -> list[CategoryRow]:
    async with session_factory() as session:
        async with session.begin():
            result = await session.execute(CATEGORIES_SQL)
            return [
                CategoryRow(
                    slug=str(row["slug"]),
                    enabled=bool(row["enabled"]),
                    title=_title(row["titles"], str(row["slug"])),
                    emoji=str(row["emoji"]),
                )
                for row in result.mappings()
            ]


async def toggle_category(
    session_factory: async_sessionmaker[AsyncSession], slug: str
) -> bool | None:
    """Flip only ``enabled`` for one category; ``None`` when the slug is unknown."""
    async with session_factory() as session:
        async with session.begin():
            row = (
                await session.execute(TOGGLE_SQL, {"slug": slug})
            ).first()
    return bool(row[0]) if row is not None else None


async def health_checks(probes: Mapping[str, Probe | None]) -> dict[str, str]:
    """The same readiness probes `/readyz` uses, checked for the panel."""
    return {name: await probe(check) for name, check in probes.items()}


def _stats_table(stats: dict[str, int]) -> str:
    cells = "".join(
        f"<td>{int(stats.get(key, 0))}</td>"
        for key in ("users", "threats", "quizzes", "answers")
    )
    return (
        "<table bordered striped compact>"
        "<tr><th>Пользователи</th><th>Карточки</th>"
        "<th>Вопросы</th><th>Ответы тестов</th></tr>"
        f"<tr>{cells}</tr>"
        "</table>"
    )


def _health_block(health: Mapping[str, str]) -> str:
    ready = "degraded" if any(state == "fail" for state in health.values()) else "ok"
    detail = " · ".join(
        f"{_esc(name)}: {_esc(state)}" for name, state in health.items()
    )
    return (
        f"<p>healthz: <b>ok</b> · readyz: <b>{ready}</b></p>"
        f"<p><code>{detail or 'проблем нет'}</code></p>"
    )


def home_screen(
    stats: dict[str, int],
    categories: list[CategoryRow],
    health: Mapping[str, str],
) -> RichScreen:
    """Summary, content, broadcast, health, then refresh and home.

    ``categories`` stays in the signature for callers that already read the
    rows; the toggles themselves live inside «Контент».
    """
    parts = [
        f"<h1>{premium('panel', '🛠')} Админ-панель</h1>",
        f"<p>{premium('lock', '🔒')} Только для администраторов</p>",
        "<hr/>",
        f"<h2>{premium('stats', '📊')} Сводка</h2>",
        _stats_table(stats),
        f"<p>{premium('people', '👥')} Разделов: <b>{len(categories)}</b></p>",
        f"<h2>{premium('catalog', '📂')} Контент</h2>",
        rich_row(
            rich_button(
                "Контент",
                data=cb_admin_content(),
                style="primary",
                icon=("catalog", "📂"),
            )
        ),
        f"<h2>{premium('broadcast', '📣')} Рассылка</h2>",
        rich_row(
            rich_button(
                "Рассылка",
                data=cb_admin_broadcast(),
                icon=("broadcast", "📣"),
            )
        ),
        f"<h2>{premium('health', '❤️')} Здоровье</h2>",
        _health_block(health),
        rich_row(
            rich_button(
                "Обновить",
                data=cb_admin(),
                icon=("refresh", "🔄"),
            ),
            menu_button(DEFAULT_LOCALE),
        ),
    ]
    return RichScreen("\n".join(parts))


async def _home(
    session_factory: async_sessionmaker[AsyncSession],
    dispatcher: Dispatcher,
) -> RichScreen:
    stats, categories, health = await asyncio.gather(
        admin_stats(session_factory),
        category_rows(session_factory),
        health_checks(_probes(dispatcher)),
    )
    return home_screen(stats, categories, health)


@router.message(Command("admin"))
async def admin_panel(
    message: Message,
    bot: Bot,
    session_factory: async_sessionmaker[AsyncSession],
    settings: Settings,
    dispatcher: Dispatcher,
) -> None:
    user_id = message.from_user.id if message.from_user else None
    if not is_admin(user_id, settings.admin_id_set):
        locale = resolve_locale(message.from_user.language_code if message.from_user else None)
        await message.answer(t(locale, "admin.denied"))
        return
    assert user_id is not None
    await settings_service.edit_pending_discard(session_factory, user_id)
    await show(bot, message.chat.id, await _home(session_factory, dispatcher))


@router.callback_query(F.data.startswith(cb_admin()))
async def admin_callback(
    query: CallbackQuery,
    bot: Bot,
    session_factory: async_sessionmaker[AsyncSession],
    settings: Settings,
    dispatcher: Dispatcher,
) -> None:
    user_id = query.from_user.id if query.from_user else None
    if not is_admin(user_id, settings.admin_id_set):
        await query.answer(ADMIN_ONLY_ALERT, show_alert=True)
        return
    assert user_id is not None

    callback = parse_cb(query.data)
    if callback is None or callback.action != "admin":
        await query.answer()
        return

    if not callback.arg:
        await query.answer("Обновлено")
        await _redraw(query, bot, await _home(session_factory, dispatcher))
        return

    if callback.arg == "bc":
        audience, last = await asyncio.gather(
            audience_count(session_factory),
            last_broadcast(session_factory),
        )
        await query.answer()
        await _redraw(query, bot, broadcast_screen(audience, last))
        return

    if callback.arg == "hint":
        await query.answer(BROADCAST_HINT, show_alert=True)
        return

    if callback.arg == "content":
        await query.answer()
        await _redraw(
            query, bot, content_screen(await content_admin.list_categories(session_factory))
        )
        return

    if callback.arg == "add" and not callback.extra:
        await query.answer()
        await _redraw(
            query,
            bot,
            add_category_screen(await content_admin.list_categories(session_factory)),
        )
        return

    kind = callback.extra[0] if callback.extra else ""

    if kind == "threats":
        threats = await content_admin.list_threats(session_factory, callback.arg)
        await query.answer()
        await _redraw(query, bot, threats_screen(callback.arg, threats))
        return

    if kind == "threat":
        detail = await content_admin.get_threat(
            session_factory, callback.arg, callback.extra[1]
        )
        if detail is None:
            await query.answer("Карточка не найдена.", show_alert=True)
            return
        await query.answer()
        await _redraw(query, bot, threat_editor_screen(detail))
        return

    if kind == "ced":
        item = await content_admin.get_category(session_factory, callback.arg)
        if item is None:
            await query.answer("Раздел не найден.", show_alert=True)
            return
        await query.answer()
        await _redraw(query, bot, category_editor_screen(item))
        return

    if kind == "cedit":
        field = callback.extra[1]
        pending_field = "cat_title" if field == "title" else "cat_emoji"
        await settings_service.edit_pending_save(
            session_factory,
            user_id,
            {"slug": callback.arg, "locale": "ru", "field": pending_field},
        )
        await query.answer()
        await _send_new(
            query,
            bot,
            prompt_screen(
                EXTRA_PROMPTS[pending_field],
                cb_admin_category_screen(callback.arg),
            ),
        )
        return

    if kind == "sev":
        level = int(callback.extra[2])
        await content_admin.set_threat_severity(
            session_factory, _repo(dispatcher), callback.arg, level
        )
        await query.answer(f"Важность: {level}")
        screen = await _editor(session_factory, callback.arg, callback.extra[1])
        if screen is not None:
            await _redraw(query, bot, screen)
        return

    if kind == "move":
        moved = await content_admin.move_threat(
            session_factory, _repo(dispatcher), callback.arg, callback.extra[2]
        )
        await query.answer("Перемещено" if moved else "Дальше некуда")
        detail = await content_admin.get_threat(
            session_factory, callback.arg, callback.extra[1]
        )
        if detail is not None:
            threats = await content_admin.list_threats(
                session_factory, detail.category_slug
            )
            await _redraw(
                query, bot, threats_screen(detail.category_slug, threats)
            )
        return

    if kind == "quiz":
        quizzes = await content_admin.list_quizzes(
            session_factory, callback.arg, callback.extra[1]
        )
        await query.answer()
        await _redraw(
            query, bot, quiz_screen(callback.arg, callback.extra[1], quizzes)
        )
        return

    if kind in {"q", "qe", "qa"}:
        locale = callback.extra[1]
        index = int(callback.extra[2])
        quiz = await content_admin.get_quiz(
            session_factory, callback.arg, locale, index
        )
        if quiz is None:
            await query.answer("Вопрос не найден.", show_alert=True)
            return
        total = len(
            await content_admin.list_quizzes(session_factory, callback.arg, locale)
        )
        if kind == "qe":
            pending_field = QUIZ_PENDING[callback.extra[3]]
            await settings_service.edit_pending_save(
                session_factory,
                user_id,
                {
                    "slug": callback.arg,
                    "locale": locale,
                    "field": pending_field,
                    "qid": quiz.question_id,
                },
            )
            await query.answer()
            await _send_new(
                query,
                bot,
                prompt_screen(
                    EXTRA_PROMPTS[pending_field],
                    cb_admin_question(callback.arg, locale, index),
                ),
            )
            return
        if kind == "qa":
            quiz = await content_admin.set_quiz_answer(
                session_factory,
                _repo(dispatcher),
                callback.arg,
                locale,
                quiz.question_id,
                int(callback.extra[3]),
            )
            await query.answer(f"Верный ответ: {quiz.answer + 1}")
        else:
            await query.answer()
        await _redraw(query, bot, quiz_question_screen(quiz, index, total))
        return

    if kind == "edit":
        _kind, locale, field = callback.extra
        await settings_service.edit_pending_save(
            session_factory,
            user_id,
            {"slug": callback.arg, "locale": locale, "field": field},
        )
        await query.answer()
        await _send_new(
            query,
            bot,
            prompt_screen(
                FIELD_LABELS[field][2], cb_admin_cancel(callback.arg, locale)
            ),
        )
        return

    if kind == "add":
        await settings_service.edit_pending_save(
            session_factory,
            user_id,
            {"slug": "", "locale": "ru", "field": "new_slug", "category": callback.arg},
        )
        await query.answer()
        await _send_new(
            query,
            bot,
            prompt_screen(
                "Пришлите slug новой карточки: a-z, 0-9 и дефис, 3–32 символа.",
                cb_admin_threats(callback.arg),
            ),
        )
        return

    if kind == "toggle":
        detail = await content_admin.get_threat(
            session_factory, callback.arg, callback.extra[1]
        )
        if detail is None:
            await query.answer("Карточка не найдена.", show_alert=True)
            return
        now_enabled = await content_admin.set_threat_enabled(
            session_factory, _repo(dispatcher), callback.arg, not detail.enabled
        )
        await query.answer("Карточка включена" if now_enabled else "Карточка выключена")
        fresh = await content_admin.get_threat(
            session_factory, callback.arg, callback.extra[1]
        )
        if fresh is not None:
            await _redraw(query, bot, threat_editor_screen(fresh))
        return

    if kind == "cancel":
        await settings_service.edit_pending_discard(session_factory, user_id)
        await query.answer(TOAST_CANCELLED)
        detail = await content_admin.get_threat(
            session_factory, callback.arg, callback.extra[1]
        )
        if detail is not None:
            await _redraw(query, bot, threat_editor_screen(detail))
        return

    if callback.extra != ("cat",) or not SLUG_PATTERN.fullmatch(callback.arg):
        await query.answer("Неизвестный раздел.", show_alert=True)
        return

    enabled = await toggle_category(session_factory, callback.arg)
    if enabled is None:
        await query.answer("Неизвестный раздел.", show_alert=True)
        return
    repo = _repo(dispatcher)
    if repo is not None:
        # The DB already holds the new flag; rebuild the index so menu, cards
        # and search stop offering the section in the same breath.
        await repo.load()
    await query.answer("Раздел включён" if enabled else "Раздел выключен")
    await _redraw(
        query, bot, content_screen(await content_admin.list_categories(session_factory))
    )