"""The one place a user's language is read or written.

The ``users.locale`` column wins; a row that never chose a language falls back
to the Telegram ``language_code`` and finally to ``ru``.
"""

from __future__ import annotations

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.services.i18n import ENABLED_LOCALES, resolve_locale

SELECT_SQL = text("SELECT locale FROM users WHERE id = :user_id")
UPDATE_SQL = text("UPDATE users SET locale = :locale WHERE id = :user_id")


async def get_locale(
    session_factory: async_sessionmaker[AsyncSession],
    tg_id: int,
    language_code: str | None = None,
) -> str:
    if tg_id:
        async with session_factory() as session:
            async with session.begin():
                row = (await session.execute(SELECT_SQL, {"user_id": tg_id})).first()
        if row is not None and row[0] in ENABLED_LOCALES:
            return row[0]
    return resolve_locale(language_code)


async def set_locale(
    session_factory: async_sessionmaker[AsyncSession], tg_id: int, locale: str
) -> None:
    async with session_factory() as session:
        async with session.begin():
            await session.execute(
                UPDATE_SQL, {"user_id": tg_id, "locale": resolve_locale(locale)}
            )


__all__ = ["get_locale", "set_locale"]