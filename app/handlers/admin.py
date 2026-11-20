from __future__ import annotations

import asyncio
import re
from collections.abc import Mapping
from dataclasses import dataclass
from html import escape
from typing import Any

from aiogram import Bot, Dispatcher, F, Router
from aiogram.filters import Command
from aiogram.types import CallbackQuery, Message
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.config import Settings
from app.middlewares.allowlist import is_admin
from app.runtime.health import Probe, probe
from app.services.callbacks import (
    cb_admin,
    cb_admin_broadcast,
    cb_admin_category,
    cb_admin_hint,
    cb_broadcast_status,
    cb_menu,
    parse_cb,
)
from app.services.i18n import DEFAULT_LOCALE, resolve_locale, t
from app.ui.delivery import show
from app.ui.emoji import premium
from app.ui.keyboards import rich_button, rich_row
from app.ui.render import RichScreen

router = Router(name="admin")

ADMIN_ONLY_ALERT = "Только для администраторов."
BROADCAST_HINT = "Отправьте /broadcast с текстом сообщения."
SLUG_PATTERN = re.compile(r"^[a-z0-9][a-z0-9-]{0,31}$")
STATUS_LABELS = {
    name: t(DEFAULT_LOCALE, "broadcasts.state." + name)
    for name in ("pending", "running", "done", "failed")
}

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
AUDIENCE_SQL = text("SELECT count(*) FROM users WHERE broadcasts_opt_in IS TRUE")
LAST_BROADCAST_SQL = text(
    "SELECT status, sent, failed FROM broadcasts ORDER BY created_at DESC LIMIT 1"
)


@dataclass(frozen=True, slots=True)
class CategoryRow:
    """One category toggle: stable slug, display title, current state."""

    slug: str
    enabled: bool
    title: str
    emoji: str


def _esc(text_value: object) -> str:
    return escape(str(text_value), quote=False)


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


async def audience_count(session_factory: async_sessionmaker[AsyncSession]) -> int:
    async with session_factory() as session:
        async with session.begin():
            row = (await session.execute(AUDIENCE_SQL)).first()
    return int(row[0]) if row is not None else 0


async def last_broadcast(
    session_factory: async_sessionmaker[AsyncSession],
) -> dict[str, Any] | None:
    async with session_factory() as session:
        async with session.begin():
            row = (await session.execute(LAST_BROADCAST_SQL)).mappings().first()
    return dict(row) if row is not None else None


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
        f"<caption>{premium('stats', '🔢')} Сводка</caption>"
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
    """The control screen: counters, category toggles, broadcast, health."""
    parts = [
        f"<h1>{premium('panel', '🛠')} Админ-панель</h1>",
        f"<p>{premium('lock', '🔒')} Только для администраторов</p>",
        "<hr/>",
        _stats_table(stats),
        f"<h2>{premium('people', '👥')} Разделы</h2>",
    ]
    if categories:
        parts.extend(
            rich_row(
                rich_button(
                    item.title,
                    data=cb_admin_category(item.slug),
                    icon=("done", "✅") if item.enabled else ("cancel", "⛔"),
                )
            )
            for item in categories
        )
    else:
        parts.append("<p>Разделов пока нет.</p>")
    parts.extend(
        (
            f"<h2>{premium('broadcast', '📣')} Рассылка</h2>",
            rich_row(
                rich_button(
                    "Рассылка",
                    data=cb_admin_broadcast(),
                    icon=("broadcast", "📣"),
                )
            ),
            f"<h2>{premium('health', '💓')} Здоровье</h2>",
            _health_block(health),
            rich_row(
                rich_button(
                    "Обновить",
                    data=cb_admin(),
                    style="primary",
                    icon=("refresh", "🔄"),
                )
            ),
            rich_row(
                rich_button("Меню", data=cb_menu(), icon=("menu", "🏘"))
            ),
        )
    )
    return RichScreen("\n".join(parts))


def broadcast_screen(audience: int, last: Mapping[str, Any] | None) -> RichScreen:
    """Audience size, last send, and the one way to start a new one."""
    if last is None:
        last_line = "<p>Рассылок ещё не было.</p>"
    else:
        status = STATUS_LABELS.get(str(last.get("status")), str(last.get("status")))
        last_line = (
            f"<p>Последняя: <b>{_esc(status)}</b>"
            f" · отправлено {int(last.get('sent') or 0)}"
            f" · ошибок {int(last.get('failed') or 0)}</p>"
        )
    html = "\n".join(
        (
            f"<h1>{premium('broadcast', '📣')} Рассылка</h1>",
            f"<p>{premium('people', '👥')} Аудитория: <b>{audience}</b></p>",
            last_line,
            "<p>Подготовьте текст и отправьте <code>/broadcast текст</code>.</p>",
            "<hr/>",
            rich_row(
                rich_button(
                    "Написать",
                    data=cb_admin_hint(),
                    style="primary",
                    icon=("broadcast", "📣"),
                ),
                rich_button(
                    "Ход рассылки",
                    data=cb_broadcast_status(),
                    icon=("hourglass", "⏳"),
                ),
            ),
            rich_row(
                rich_button("Назад", data=cb_admin(), icon=("back", "🏠"))
            ),
        )
    )
    return RichScreen(html)


def _probes(dispatcher: Dispatcher) -> Mapping[str, Probe | None]:
    value = dispatcher.workflow_data.get("readiness_probes", {})
    return value if isinstance(value, Mapping) else {}


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


async def _redraw(
    query: CallbackQuery, bot: Bot, screen: RichScreen
) -> None:
    message = query.message
    if message is None:
        return
    chat_id = getattr(getattr(message, "chat", None), "id", None)
    message_id = getattr(message, "message_id", None)
    if chat_id is None or message_id is None:
        return
    await show(bot, chat_id, screen, message_id)


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

    if callback.extra != ("cat",) or not SLUG_PATTERN.fullmatch(callback.arg):
        await query.answer("Неизвестный раздел.", show_alert=True)
        return

    enabled = await toggle_category(session_factory, callback.arg)
    if enabled is None:
        await query.answer("Неизвестный раздел.", show_alert=True)
        return
    await query.answer("Раздел включён" if enabled else "Раздел выключен")
    await _redraw(query, bot, await _home(session_factory, dispatcher))


def register(dp: Dispatcher, _bot: Bot) -> None:
    dp.include_router(router)