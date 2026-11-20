from __future__ import annotations

import asyncio
import re
from collections.abc import Mapping
from dataclasses import dataclass
from html import escape
from typing import Any

from aiogram import Bot, Dispatcher, F, Router
from aiogram.dispatcher.event.bases import SkipHandler
from aiogram.filters import Command
from aiogram.types import CallbackQuery, Message
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.config import Settings
from app.content.repository import ContentRepository
from app.middlewares.allowlist import is_admin
from app.runtime.health import Probe, probe
from app.services import content_admin
from app.services import settings as settings_service
from app.services.callbacks import (
    cb_admin,
    cb_admin_add,
    cb_admin_broadcast,
    cb_admin_cancel,
    cb_admin_category,
    cb_admin_category_edit,
    cb_admin_category_screen,
    cb_admin_content,
    cb_admin_edit,
    cb_admin_hint,
    cb_admin_move,
    cb_admin_question,
    cb_admin_quiz,
    cb_admin_quiz_answer,
    cb_admin_quiz_edit,
    cb_admin_severity,
    cb_admin_threat,
    cb_admin_threats,
    cb_admin_toggle,
    cb_broadcast_status,
    parse_cb,
)
from app.services.i18n import DEFAULT_LOCALE, resolve_locale, t
from app.ui.delivery import show
from app.ui.emoji import premium
from app.ui.keyboards import menu_button, rich_button, rich_row
from app.ui.render import RichScreen

router = Router(name="admin")

ADMIN_ONLY_ALERT = "Только для администраторов."
BROADCAST_HINT = "Отправьте /broadcast с текстом сообщения."
SLUG_PATTERN = re.compile(r"^[a-z0-9][a-z0-9-]{0,31}$")
STATUS_LABELS = {
    name: t(DEFAULT_LOCALE, "broadcasts.state." + name)
    for name in ("pending", "running", "done", "failed")
}
# Field -> (label, icon, prompt). The short keys are the callback payload.
FIELD_LABELS: dict[str, tuple[str, str, str]] = {
    "title": ("Название", "description", "Пришлите новое название."),
    "summary": ("Кратко", "digest", "Пришлите новое краткое описание (до 300 символов)."),
    "signs": ("Признаки", "eyes", "Пришлите признаки: по одному в строке (2–5)."),
    "before": ("До", "hourglass", "Пришлите шаги «до»: по одному в строке (2–5)."),
    "during": ("Во время", "fire", "Пришлите шаги «во время»: по одному в строке (2–5)."),
    "after": ("После", "refresh", "Пришлите шаги «после»: по одному в строке (2–5)."),
    "forbidden": ("Нельзя", "cancel", "Пришлите запреты: по одному в строке (2–5)."),
    "contacts": ("Контакты", "support", "Пришлите контакты через запятую."),
    "source": ("Источник", "link", "Пришлите источник."),
}
PROMPT_TEXT = "Пришлите новый текст."
TOAST_SAVED = "Сохранено"
TOAST_CANCELLED = "Отменено"
# Pending-edit fields that are not threat text: key -> prompt.
EXTRA_PROMPTS: dict[str, str] = {
    "cat_title": "Пришлите название раздела.",
    "cat_emoji": "Пришлите один значок раздела.",
    "q_prompt": "Пришлите текст вопроса (до 600 символов).",
    "q_option0": "Пришлите вариант 1.",
    "q_option1": "Пришлите вариант 2.",
    "q_option2": "Пришлите вариант 3.",
    "q_option3": "Пришлите вариант 4.",
    "q_explanation": "Пришлите объяснение (до 600 символов, можно пустое).",
}
QUIZ_PENDING = {
    "p": "q_prompt",
    "o0": "q_option0",
    "o1": "q_option1",
    "o2": "q_option2",
    "o3": "q_option3",
    "e": "q_explanation",
}
QUIZ_OPTIONS = ("o0", "o1", "o2", "o3")

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


def _menu_row() -> str:
    """The universal home button, last row on every admin page."""
    return rich_row(menu_button(DEFAULT_LOCALE))


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
            _menu_row(),
        )
    )
    return RichScreen(html)


def content_screen(categories: list[content_admin.CategoryItem]) -> RichScreen:
    """Every section with its card count, then the one add entry at the bottom."""
    rows = [
        rich_row(
            rich_button(
                f"{item.title} · {item.active}/{item.total}",
                data=cb_admin_threats(item.slug),
                icon=("done", "✅") if item.enabled else ("cancel", "⛔"),
            ),
            rich_button(
                "Изменить",
                data=cb_admin_category_screen(item.slug),
                icon=("description", "📖"),
            ),
        )
        for item in categories
    ]
    if not rows:
        rows.append("<p>Разделов пока нет.</p>")
    rows.append(
        rich_row(
            rich_button(
                "Добавить карточку",
                data=cb_admin_add(),
                style="success",
                icon=("new", "🆕"),
            )
        )
    )
    rows.append(
        rich_row(rich_button("Назад", data=cb_admin(), icon=("back", "🏠")))
    )
    rows.append(_menu_row())
    html = (
        f"<h1>{premium('catalog', '📂')} Контент</h1>"
        "<p>Раздел → карточки → поля. Правки переживают пересев.</p><hr/>"
    )
    return RichScreen(html + "\n".join(rows))


def add_category_screen(categories: list[content_admin.CategoryItem]) -> RichScreen:
    """Pick the section a new card goes into, one tap per section."""
    rows = [
        rich_row(
            rich_button(
                f"{item.title} · {item.active}/{item.total}",
                data=cb_admin_add(item.slug),
                icon=(item.slug, _esc(item.emoji)),
            )
        )
        for item in categories
    ]
    if not rows:
        rows.append("<p>Разделов пока нет.</p>")
    rows.append(
        rich_row(rich_button("Назад", data=cb_admin_content(), icon=("back", "🏠")))
    )
    rows.append(_menu_row())
    html = (
        f"<h1>{premium('new', '🆕')} Добавить карточку</h1>"
        "<p>Выберите раздел новой карточки.</p><hr/>"
    )
    return RichScreen(html + "\n".join(rows))


def threats_screen(
    category: str, threats: list[content_admin.ThreatItem]
) -> RichScreen:
    """Cards of one section; a disabled card keeps its row, marked."""
    rows = [
        rich_row(
            rich_button(
                f"{'●' * item.severity}{'○' * (3 - item.severity)} {item.title}",
                data=cb_admin_threat(item.slug),
                icon=("done", "✅") if item.enabled else ("cancel", "⛔"),
            )
        )
        for item in threats
    ]
    if not rows:
        rows.append("<p>Карточек пока нет.</p>")
    rows.append(
        rich_row(
            rich_button(
                "Добавить карточку",
                data=cb_admin_add(category),
                style="success",
                icon=("new", "🆕"),
            )
        )
    )
    rows.append(
        rich_row(rich_button("Назад", data=cb_admin_content(), icon=("back", "🏠")))
    )
    rows.append(_menu_row())
    html = (
        f"<h1>{premium('catalog', '📂')} {_esc(category)}</h1>"
        f"<p>{premium('stats', '🔢')} Карточек: <b>{len(threats)}</b></p><hr/>"
    )
    return RichScreen(html + "\n".join(rows))


def category_editor_screen(item: content_admin.CategoryItem) -> RichScreen:
    """A section's title, icon and on/off switch, in one place."""
    rows = [
        rich_row(
            rich_button(
                "Название",
                data=cb_admin_category_edit(item.slug, "title"),
                icon=("description", "📖"),
            ),
            rich_button(
                "Значок",
                data=cb_admin_category_edit(item.slug, "emoji"),
                icon=("category", "🏷"),
            ),
        ),
        rich_row(
            rich_button(
                "Выключить" if item.enabled else "Включить",
                data=cb_admin_category(item.slug),
                style="danger" if item.enabled else "success",
                icon=("mute", "🔕") if item.enabled else ("done", "✅"),
            )
        ),
        rich_row(rich_button("Назад", data=cb_admin_content(), icon=("back", "🏠"))),
        _menu_row(),
    ]
    html = (
        f"<h1>{premium('category', '🏷')} {_esc(item.title)}</h1>"
        f"<p>{_esc(item.emoji)} · {'включён' if item.enabled else 'выключен'}"
        f" · карточек {item.active}/{item.total}</p><hr/>"
    )
    return RichScreen(html + "\n".join(rows))


def quiz_screen(
    slug: str, locale: str, quizzes: list[content_admin.QuizEdit]
) -> RichScreen:
    rows = [
        rich_row(
            rich_button(
                f"{number}. {item.prompt}",
                data=cb_admin_question(slug, locale, number),
            )
        )
        for number, item in enumerate(quizzes, start=1)
    ]
    if not rows:
        rows.append("<p>Вопросов на этом языке нет.</p>")
    rows.append(
        rich_row(
            rich_button("Назад", data=cb_admin_threat(slug, locale), icon=("back", "🏠"))
        )
    )
    rows.append(_menu_row())
    html = (
        f"<h1>{premium('test', '✅')} Тесты</h1>"
        f"<p>{premium('stats', '🔢')} Вопросов: <b>{len(quizzes)}</b>"
        f" · язык <code>{_esc(locale)}</code></p><hr/>"
    )
    return RichScreen(html + "\n".join(rows))


def quiz_question_screen(
    quiz: content_admin.QuizEdit, index: int, total: int
) -> RichScreen:
    """One question: prompt, four options, the correct index, explanation."""
    options = [
        rich_button(
            f"{number}. {text}",
            data=cb_admin_quiz_edit(quiz.slug, quiz.locale, index, QUIZ_OPTIONS[number - 1]),
            style="success" if number - 1 == quiz.answer else None,
            icon=("done", "✅") if number - 1 == quiz.answer else None,
        )
        for number, text in enumerate(quiz.options, start=1)
    ]
    answers = [
        rich_button(
            str(number),
            data=cb_admin_quiz_answer(quiz.slug, quiz.locale, index, number - 1),
            style="success" if number - 1 == quiz.answer else None,
        )
        for number in range(1, 5)
    ]
    rows = [
        *(rich_row(button) for button in options),
        rich_row(*answers),
        rich_row(
            rich_button(
                "Вопрос",
                data=cb_admin_quiz_edit(quiz.slug, quiz.locale, index, "p"),
                icon=("description", "📖"),
            ),
            rich_button(
                "Объяснение",
                data=cb_admin_quiz_edit(quiz.slug, quiz.locale, index, "e"),
                icon=("digest", "📝"),
            ),
        ),
        rich_row(
            rich_button(
                "Назад",
                data=cb_admin_quiz(quiz.slug, quiz.locale),
                icon=("back", "🏠"),
            )
        ),
        _menu_row(),
    ]
    html = (
        f"<h1>{premium('test', '✅')} Вопрос {index}/{total}</h1>"
        f"<p>{_esc(quiz.prompt)}</p>"
        f"<p>{premium('done', '✅')} Верный: <b>{quiz.answer + 1}</b></p>"
        f"<p>{_esc(quiz.explanation) or '—'}</p><hr/>"
    )
    return RichScreen(html + "\n".join(rows))


def _field_block(title: str, items: tuple[str, ...]) -> str:
    if not items:
        return f"<h3>{_esc(title)}</h3><p>—</p>"
    lines = "<br/>".join(f"• {_esc(item)}" for item in items)
    return f"<h3>{_esc(title)}</h3><p>{lines}</p>"


def threat_editor_screen(detail: content_admin.ThreatDetail) -> RichScreen:
    """The card as stored, every field one tap away from replacement."""
    flags = f"{'●' * detail.severity}{'○' * (3 - detail.severity)}"
    state = "включена" if detail.enabled else "выключена"
    rows: list[str] = []
    if len(detail.locales) > 1:
        rows.append(
            rich_row(
                *(
                    rich_button(
                        code,
                        data=cb_admin_threat(detail.slug, code),
                        icon=("done", "✅") if code == detail.locale else ("lang", "🌐"),
                    )
                    for code in detail.locales
                )
            )
        )
    buttons = [
        rich_button(
            label,
            data=cb_admin_edit(detail.slug, detail.locale, field),
            icon=(icon, FIELD_ICONS[icon]),
        )
        for field, (label, icon, _prompt) in FIELD_LABELS.items()
    ]
    rows.extend(rich_row(*buttons[index : index + 2]) for index in range(0, len(buttons), 2))
    rows.append(
        rich_row(
            *(
                rich_button(
                    str(level),
                    data=cb_admin_severity(detail.slug, detail.locale, level),
                    style="success" if level == detail.severity else None,
                    icon=("done", "✅") if level == detail.severity else None,
                )
                for level in (1, 2, 3)
            )
        )
    )
    rows.append(
        rich_row(
            rich_button(
                "Выше",
                data=cb_admin_move(detail.slug, detail.locale, "up"),
                icon=("arrow", "🔽"),
            ),
            rich_button(
                "Ниже",
                data=cb_admin_move(detail.slug, detail.locale, "down"),
                icon=("arrow", "🔽"),
            ),
            rich_button(
                "Тесты",
                data=cb_admin_quiz(detail.slug, detail.locale),
                icon=("test", "✅"),
            ),
        )
    )
    rows.append(
        rich_row(
            rich_button(
                "Выключить" if detail.enabled else "Включить",
                data=cb_admin_toggle(detail.slug, detail.locale),
                style="danger" if detail.enabled else "success",
                icon=("mute", "🔕") if detail.enabled else ("done", "✅"),
            ),
            rich_button("Назад", data=cb_admin_threats(detail.category_slug), icon=("back", "🏠")),
        )
    )
    rows.append(_menu_row())
    missing = (
        ""
        if detail.exact
        else f"<p>{premium('error', '❗')} Текста на <code>{_esc(detail.locale)}</code>"
        " нет: показан ru. Сохранение создаст перевод.</p>"
    )
    html = "".join(
        (
            f"<h1>{premium('description', '📖')} {_esc(detail.title)}</h1>",
            f"<p>{flags} · {_esc(detail.category_slug)} · {_esc(state)}</p>",
            missing,
            f"<p>{_esc(detail.summary) or '—'}</p><hr/>",
            _field_block("Признаки", detail.signs),
            _field_block("До", detail.before),
            _field_block("Во время", detail.during),
            _field_block("После", detail.after),
            _field_block("Нельзя", detail.forbidden),
            _field_block("Контакты", detail.contacts),
            f"<h3>Источник</h3><p>{_esc(detail.source) or '—'}</p><hr/>",
        )
    )
    return RichScreen(html + "\n".join(rows))


def prompt_screen(text: str, cancel_data: str) -> RichScreen:
    """One short instruction and the way out; the next message is the value."""
    return RichScreen(
        "\n".join(
            (
                f"<h1>{premium('description', '📖')} Правка</h1>",
                f"<p>{_esc(PROMPT_TEXT)}</p>",
                f"<p>{_esc(text)}</p>",
                rich_row(
                    rich_button("Отмена", data=cancel_data, icon=("cancel", "❌"))
                ),
                _menu_row(),
            )
        )
    )


FIELD_ICONS = {
    "description": "📖",
    "digest": "📝",
    "eyes": "👀",
    "hourglass": "⏳",
    "fire": "🔥",
    "refresh": "🔄",
    "cancel": "❌",
    "support": "💬",
    "link": "🔗",
}


def _repo(dispatcher: Dispatcher) -> ContentRepository | None:
    """The live index, registered in `workflow_data` next to the probes."""
    value = dispatcher.workflow_data.get("repo")
    return value if isinstance(value, ContentRepository) else None


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


async def _editor(
    session_factory: async_sessionmaker[AsyncSession], slug: str, locale: str
) -> RichScreen | None:
    detail = await content_admin.get_threat(session_factory, slug, locale)
    return None if detail is None else threat_editor_screen(detail)


async def _send_new(query: CallbackQuery, bot: Bot, screen: RichScreen) -> None:
    """The prompt is a second message: the editor card stays where it is."""
    message = query.message
    chat_id = getattr(getattr(message, "chat", None), "id", None)
    if chat_id is not None:
        await show(bot, chat_id, screen)


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


@router.message(F.text, ~F.text.startswith("/"))
async def admin_edit_text(
    message: Message,
    bot: Bot,
    session_factory: async_sessionmaker[AsyncSession],
    settings: Settings,
    dispatcher: Dispatcher,
) -> None:
    """The next plain message from an admin with a pending field, before AI chat."""
    user = message.from_user
    if user is None or not is_admin(user.id, settings.admin_id_set):
        raise SkipHandler()
    pending = await settings_service.edit_pending_take(session_factory, user.id)
    if pending is None:
        raise SkipHandler()
    raw = (message.text or "").strip()
    field = pending.get("field", "")

    if field == "new_slug":
        if not content_admin.SLUG_PATTERN.fullmatch(raw):
            await message.answer("Slug: 3–32 символа a-z, 0-9 и дефис. Пришлите снова.")
            await settings_service.edit_pending_save(session_factory, user.id, pending)
            return
        pending = {
            "slug": raw,
            "locale": "ru",
            "field": "new_title",
            "category": pending.get("category", ""),
        }
        await settings_service.edit_pending_save(session_factory, user.id, pending)
        await message.answer("Пришлите название новой карточки.")
        return

    if field == "new_title":
        try:
            detail = await content_admin.create_threat(
                session_factory,
                _repo(dispatcher),
                pending.get("category", ""),
                pending.get("slug", ""),
                2,
                title=raw,
            )
        except content_admin.ContentError as error:
            await message.answer(f"{error} Начните добавление заново.")
            return
        await message.answer(TOAST_SAVED)
        await show(bot, message.chat.id, threat_editor_screen(detail))
        return

    if field in {"cat_title", "cat_emoji"}:
        item = await content_admin.get_category(
            session_factory, pending.get("slug", "")
        )
        if item is None:
            await message.answer("Раздел не найден.")
            return
        title = raw if field == "cat_title" else item.title
        emoji = raw if field == "cat_emoji" else item.emoji
        try:
            await content_admin.save_category(
                session_factory,
                _repo(dispatcher),
                item.slug,
                title,
                emoji,
                item.enabled,
            )
        except content_admin.ContentError as error:
            await message.answer(str(error))
            await settings_service.edit_pending_save(session_factory, user.id, pending)
            return
        await message.answer(TOAST_SAVED)
        fresh = await content_admin.get_category(session_factory, item.slug)
        if fresh is not None:
            await show(bot, message.chat.id, category_editor_screen(fresh))
        return

    if field.startswith("q_") and field in EXTRA_PROMPTS:
        slug = pending.get("slug", "")
        locale = pending.get("locale", content_admin.DEFAULT_LOCALE)
        qid = pending.get("qid", "")
        quizzes = await content_admin.list_quizzes(session_factory, slug, locale)
        current = next((q for q in quizzes if q.question_id == qid), None)
        if current is None:
            await message.answer("Вопрос не найден.")
            return
        if field == "q_prompt":
            patch: dict[str, object] = {"prompt": raw}
        elif field == "q_explanation":
            patch = {"explanation": raw}
        else:
            position = int(field[-1])
            if position >= len(current.options):
                await message.answer("Этого варианта нет: сначала заполните предыдущие.")
                await settings_service.edit_pending_save(
                    session_factory, user.id, pending
                )
                return
            options = list(current.options)
            options[position] = raw
            patch = {"options": options}
        try:
            saved = await content_admin.save_quiz(
                session_factory, _repo(dispatcher), slug, locale, qid, patch
            )
        except content_admin.ContentError as error:
            await message.answer(str(error))
            await settings_service.edit_pending_save(session_factory, user.id, pending)
            return
        await message.answer(TOAST_SAVED)
        index = next(
            (
                number
                for number, quiz in enumerate(quizzes, start=1)
                if quiz.question_id == qid
            ),
            1,
        )
        await show(
            bot,
            message.chat.id,
            quiz_question_screen(saved, index, len(quizzes)),
        )
        return

    if field not in content_admin.EDIT_FIELDS:
        raise SkipHandler()
    try:
        detail = await content_admin.save_threat(
            session_factory,
            _repo(dispatcher),
            pending.get("slug", ""),
            pending.get("locale", content_admin.DEFAULT_LOCALE),
            {field: content_admin.parse_field_input(field, raw)},
        )
    except content_admin.ContentError as error:
        # Keep the pending key: the admin can fix the text and send it again.
        await message.answer(str(error))
        await settings_service.edit_pending_save(session_factory, user.id, pending)
        return
    await message.answer(TOAST_SAVED)
    await show(bot, message.chat.id, threat_editor_screen(detail))


@router.message(Command("cancel"))
async def cancel_edit(
    message: Message,
    session_factory: async_sessionmaker[AsyncSession],
    settings: Settings,
) -> None:
    """`/cancel` always answers, even when no field was waiting."""
    user = message.from_user
    if user is None or not is_admin(user.id, settings.admin_id_set):
        raise SkipHandler()
    await settings_service.edit_pending_discard(session_factory, user.id)
    await message.answer(TOAST_CANCELLED)


@router.message(Command("start"))
async def clear_pending_on_start(
    message: Message,
    session_factory: async_sessionmaker[AsyncSession],
    settings: Settings,
) -> None:
    """An admin starting over is not mid-edit; then let `commands` draw the menu."""
    user = message.from_user
    if user is None or not is_admin(user.id, settings.admin_id_set):
        raise SkipHandler()
    await settings_service.edit_pending_discard(session_factory, user.id)
    raise SkipHandler()


def register(dp: Dispatcher, _bot: Bot) -> None:
    dp.include_router(router)