from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.handlers.admin.common import _back_menu_row, _esc
from app.services.callbacks import cb_admin, cb_admin_hint, cb_broadcast_status
from app.services.i18n import DEFAULT_LOCALE, t
from app.ui.emoji import premium
from app.ui.keyboards import rich_button, rich_row
from app.ui.render import RichScreen

STATUS_LABELS = {
    name: t(DEFAULT_LOCALE, "broadcasts.state." + name)
    for name in ("pending", "running", "done", "failed")
}
AUDIENCE_SQL = text("SELECT count(*) FROM users WHERE broadcasts_opt_in IS TRUE")
LAST_BROADCAST_SQL = text(
    "SELECT status, sent, failed FROM broadcasts ORDER BY created_at DESC LIMIT 1"
)


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
            _back_menu_row(cb_admin()),
        )
    )
    return RichScreen(html)