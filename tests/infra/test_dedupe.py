from __future__ import annotations

import logging

from aiogram.types import Update

from app.middlewares.dedupe import DedupeMiddleware


class BrokenRedis:
    async def set(self, *_args: object, **_kwargs: object) -> bool:
        raise ConnectionError("redis is down")


async def test_broken_redis_still_delivers_update(caplog) -> None:
    delivered: list[object] = []

    async def handler(event: object, _data: dict[str, object]) -> str:
        delivered.append(event)
        return "ok"

    middleware = DedupeMiddleware(BrokenRedis())  # type: ignore[arg-type]
    with caplog.at_level(logging.WARNING, logger="app.middlewares.dedupe"):
        assert await middleware(handler, Update(update_id=1), {}) == "ok"

    assert len(delivered) == 1


async def test_first_failure_is_logged_once(caplog) -> None:
    async def handler(_event: object, _data: dict[str, object]) -> None:
        return None

    middleware = DedupeMiddleware(BrokenRedis())  # type: ignore[arg-type]
    with caplog.at_level(logging.WARNING, logger="app.middlewares.dedupe"):
        for update_id in (1, 2, 3):
            await middleware(handler, Update(update_id=update_id), {})

    warnings = [record for record in caplog.records if "dedupe unavailable" in record.message]
    assert len(warnings) == 1