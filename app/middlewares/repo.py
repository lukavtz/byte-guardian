"""Inject the content repository into handler data."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

from aiogram import BaseMiddleware
from aiogram.types import TelegramObject

from app.content.repository import ContentRepository


class RepoMiddleware(BaseMiddleware):
    def __init__(self, repo: ContentRepository) -> None:
        self._repo = repo

    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        data["repo"] = self._repo
        return await handler(event, data)
