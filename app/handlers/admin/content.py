from __future__ import annotations

from aiogram import Bot, Dispatcher, F
from aiogram.dispatcher.event.bases import SkipHandler
from aiogram.filters import Command
from aiogram.types import Message
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.config import Settings
from app.handlers.admin.common import (
    EXTRA_PROMPTS,
    FIELD_ICONS,
    FIELD_LABELS,
    TOAST_CANCELLED,
    TOAST_SAVED,
    _esc,
    _menu_row,
    _repo,
    router,
)
from app.middlewares.allowlist import is_admin
from app.services import content_admin
from app.services import settings as settings_service
from app.services.callbacks import (
    cb_admin,
    cb_admin_add,
    cb_admin_category,
    cb_admin_category_edit,
    cb_admin_category_screen,
    cb_admin_content,
    cb_admin_edit,
    cb_admin_move,
    cb_admin_question,
    cb_admin_quiz,
    cb_admin_quiz_answer,
    cb_admin_quiz_edit,
    cb_admin_severity,
    cb_admin_threat,
    cb_admin_threats,
    cb_admin_toggle,
)
from app.ui.delivery import show
from app.ui.emoji import premium
from app.ui.keyboards import rich_button, rich_row
from app.ui.render import RichScreen

QUIZ_PENDING = {
    "p": "q_prompt",
    "o0": "q_option0",
    "o1": "q_option1",
    "o2": "q_option2",
    "o3": "q_option3",
    "e": "q_explanation",
}
QUIZ_OPTIONS = ("o0", "o1", "o2", "o3")


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


async def _editor(
    session_factory: async_sessionmaker[AsyncSession], slug: str, locale: str
) -> RichScreen | None:
    detail = await content_admin.get_threat(session_factory, slug, locale)
    return None if detail is None else threat_editor_screen(detail)


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