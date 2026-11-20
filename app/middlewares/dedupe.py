"""Claim each update once in Redis, so a Telegram retry cannot run it twice.

The claim fails open: a Redis outage must not stop the bot from answering
users. The first failure is logged once and the update goes through; blocking
delivery on an optional cache would turn a degraded dependency into an outage.
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from typing import Any

from aiogram import BaseMiddleware
from aiogram.types import TelegramObject, Update
from redis.asyncio import Redis

logger = logging.getLogger(__name__)

DEDUPE_TTL_SECONDS = 86_400


class DedupeMiddleware(BaseMiddleware):
    def __init__(self, redis: Redis | None) -> None:
        self.redis = redis
        self._unavailable_logged = False
        if redis is None:
            logger.warning("dedupe disabled: REDIS_URL is not set")

    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        update_id = event.update_id if isinstance(event, Update) else None
        if update_id is None or self.redis is None:
            return await handler(event, data)
        try:
            claimed = await self.redis.set(
                f"update:{update_id}", "1", nx=True, ex=DEDUPE_TTL_SECONDS
            )
        except Exception:
            if not self._unavailable_logged:
                logger.warning(
                    "dedupe unavailable; delivering updates without it", exc_info=True
                )
                self._unavailable_logged = True
            return await handler(event, data)
        if not claimed:
            logger.info("duplicate update %s ignored", update_id)
            return None
        return await handler(event, data)