from __future__ import annotations

import re
from collections.abc import Mapping
from html import escape

from aiogram import Bot, Dispatcher, Router
from aiogram.types import CallbackQuery

from app.content.repository import ContentRepository
from app.runtime.health import Probe
from app.services.i18n import DEFAULT_LOCALE
from app.ui.delivery import show
from app.ui.emoji import premium
from app.ui.keyboards import menu_button, rich_button, rich_row
from app.ui.render import RichScreen

router = Router(name="admin")

ADMIN_ONLY_ALERT = "Только для администраторов."
BROADCAST_HINT = "Отправьте /broadcast с текстом сообщения."
SLUG_PATTERN = re.compile(r"^[a-z0-9][a-z0-9-]{0,31}$")
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


def _esc(text_value: object) -> str:
    return escape(str(text_value), quote=False)


def _menu_row() -> str:
    """The universal home button, last row on every admin page."""
    return rich_row(menu_button(DEFAULT_LOCALE))


def _back_menu_row(data: str) -> str:
    """Back on the left, green menu on the right: one row, one way home."""
    return rich_row(
        rich_button("Назад", data=data, style="danger", icon=("back", "🏠")),
        menu_button(DEFAULT_LOCALE),
    )


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


def _repo(dispatcher: Dispatcher) -> ContentRepository | None:
    """The live index, registered in `workflow_data` next to the probes."""
    value = dispatcher.workflow_data.get("repo")
    return value if isinstance(value, ContentRepository) else None


def _probes(dispatcher: Dispatcher) -> Mapping[str, Probe | None]:
    value = dispatcher.workflow_data.get("readiness_probes", {})
    return value if isinstance(value, Mapping) else {}


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