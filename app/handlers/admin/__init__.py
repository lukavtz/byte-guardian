from __future__ import annotations

from aiogram import Bot, Dispatcher

from app.handlers.admin.broadcast import (
    STATUS_LABELS,
    audience_count,
    broadcast_screen,
    last_broadcast,
)
from app.handlers.admin.common import (
    ADMIN_ONLY_ALERT,
    BROADCAST_HINT,
    EXTRA_PROMPTS,
    FIELD_ICONS,
    FIELD_LABELS,
    PROMPT_TEXT,
    SLUG_PATTERN,
    TOAST_CANCELLED,
    TOAST_SAVED,
    prompt_screen,
    router,
)
from app.handlers.admin.content import (
    QUIZ_OPTIONS,
    QUIZ_PENDING,
    add_category_screen,
    admin_edit_text,
    cancel_edit,
    category_editor_screen,
    clear_pending_on_start,
    content_screen,
    quiz_question_screen,
    quiz_screen,
    threat_editor_screen,
    threats_screen,
)
from app.handlers.admin.home import (
    CategoryRow,
    admin_callback,
    admin_panel,
    admin_stats,
    category_rows,
    health_checks,
    home_screen,
    toggle_category,
)

__all__ = [
    "ADMIN_ONLY_ALERT",
    "BROADCAST_HINT",
    "EXTRA_PROMPTS",
    "FIELD_ICONS",
    "FIELD_LABELS",
    "PROMPT_TEXT",
    "QUIZ_OPTIONS",
    "QUIZ_PENDING",
    "SLUG_PATTERN",
    "STATUS_LABELS",
    "TOAST_CANCELLED",
    "TOAST_SAVED",
    "CategoryRow",
    "add_category_screen",
    "admin_callback",
    "admin_edit_text",
    "admin_panel",
    "admin_stats",
    "audience_count",
    "broadcast_screen",
    "cancel_edit",
    "category_editor_screen",
    "category_rows",
    "clear_pending_on_start",
    "content_screen",
    "health_checks",
    "home_screen",
    "last_broadcast",
    "prompt_screen",
    "quiz_question_screen",
    "quiz_screen",
    "register",
    "router",
    "threat_editor_screen",
    "threats_screen",
    "toggle_category",
]


def register(dp: Dispatcher, _bot: Bot) -> None:
    dp.include_router(router)