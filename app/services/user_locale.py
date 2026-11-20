"""The one place a user's language is read or written.

The ``users.locale`` column wins; a row that never chose a language falls back
to the Telegram ``language_code`` and finally to ``ru``. ``locale_chosen``
records whether the column came from the user, so ``/start`` may seed the
column once and then leave it alone.
"""

from __future__ import annotations

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.services.i18n import ENABLED_LOCALES, resolve_locale

SELECT_STATE_SQL = text("SELECT locale, locale_chosen FROM users WHERE id = :user_id")
UPDATE_SQL = text(
    "UPDATE users SET locale = :locale, locale_chosen = true WHERE id = :user_id"
)


async def locale_state(
    session_factory: async_sessionmaker[AsyncSession], tg_id: int
) -> tuple[str | None, bool]:
    """The stored locale (``None`` when unusable) and whether the user chose it."""
    if not tg_id:
        return None, False
    async with session_factory() as session:
        async with session.begin():
            row = (await session.execute(SELECT_STATE_SQL, {"user_id": tg_id})).first()
    if row is None:
        return None, False
    stored = row[0] if row[0] in ENABLED_LOCALES else None
    return stored, bool(row[1])


async def get_locale(
    session_factory: async_sessionmaker[AsyncSession],
    tg_id: int,
    language_code: str | None = None,
) -> str:
    stored, _chosen = await locale_state(session_factory, tg_id)
    return stored or resolve_locale(language_code)


async def set_locale(
    session_factory: async_sessionmaker[AsyncSession], tg_id: int, locale: str
) -> None:
    async with session_factory() as session:
        async with session.begin():
            await session.execute(
                UPDATE_SQL, {"user_id": tg_id, "locale": resolve_locale(locale)}
            )


__all__ = ["get_locale", "locale_state", "set_locale"]