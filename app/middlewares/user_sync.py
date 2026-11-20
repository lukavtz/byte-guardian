from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

from aiogram import BaseMiddleware, Bot, Dispatcher
from aiogram.types import TelegramObject
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

USER_UPSERT_SQL = text(
    """
    INSERT INTO users (id, username, display_name)
    VALUES (:id, :username, :display_name)
    ON CONFLICT (id) DO UPDATE
    SET username = EXCLUDED.username,
        display_name = EXCLUDED.display_name,
        updated_at = now()
    """
)
MAX_USERNAME = 32
MAX_DISPLAY_NAME = 64


def _display_name(first_name: str, last_name: str | None) -> str:
    return " ".join(part for part in (first_name, last_name) if part)[:MAX_DISPLAY_NAME]


class UserSyncMiddleware(BaseMiddleware):
    def __init__(
        self, session_factory: async_sessionmaker[AsyncSession] | None = None
    ) -> None:
        self._session_factory = session_factory

    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        user = getattr(event, "from_user", None)
        session_factory = data.get("session_factory") or self._session_factory
        if user is not None and session_factory is not None:
            username = user.username[:MAX_USERNAME] if user.username else None
            async with session_factory() as session:
                async with session.begin():
                    await session.execute(
                        USER_UPSERT_SQL,
                        {
                            "id": user.id,
                            "username": username,
                            "display_name": _display_name(user.first_name, user.last_name),
                        },
                    )
        return await handler(event, data)


def register(dp: Dispatcher, _bot: Bot) -> None:
    dp.update.middleware(UserSyncMiddleware())
