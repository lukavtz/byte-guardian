from __future__ import annotations

import asyncio
import dataclasses
import importlib
import pkgutil
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

from aiogram import Bot
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.config import Settings
from app.runtime.queue import ClaimedJob, Queue

JobHandler = Callable[[ClaimedJob, asyncio.Event], Awaitable[None]]


@dataclass(frozen=True)
class JobContext:
    bot: Bot
    session_factory: async_sessionmaker[AsyncSession]
    queue: Queue
    settings: Settings
    redis: Redis | None
    stop: asyncio.Event | None = None


def _bind(handle: Callable[[ClaimedJob, JobContext], Awaitable[None]], ctx: JobContext) -> JobHandler:
    async def handler(job: ClaimedJob, stop: asyncio.Event) -> None:
        await handle(job, dataclasses.replace(ctx, stop=stop))

    return handler


def _job_module_names() -> list[str]:
    package: Any = importlib.import_module(__name__)
    return sorted(
        info.name
        for info in pkgutil.iter_modules(package.__path__)
        if not info.name.startswith("_")
    )


def load_job_handlers(ctx: JobContext) -> dict[str, JobHandler]:
    handlers: dict[str, JobHandler] = {}
    for module_name in _job_module_names():
        module = importlib.import_module(f"{__name__}.{module_name}")
        kind = getattr(module, "KIND", None)
        handle = getattr(module, "handle", None)
        if not isinstance(kind, str) or not kind:
            raise RuntimeError(f"job module {module_name} must define a non-empty KIND")
        if not callable(handle):
            raise RuntimeError(f"job module {module_name} must define handle(job, ctx)")
        if kind in handlers:
            raise RuntimeError(f"duplicate job kind: {kind}")
        handlers[kind] = _bind(handle, ctx)
    return handlers
