from __future__ import annotations

import logging
import os
from pathlib import Path

from aiogram import Bot, Dispatcher, Router
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.types import CallbackQuery, ErrorEvent
from redis.asyncio import Redis

from app.config import Settings
from app.content.repository import ContentRepository
from app.content.seed import seed_content
from app.db.session import SessionFactory, create_session_factory
from app.handlers import llm as llm_handler
from app.handlers import register_handlers
from app.handlers.nav import menu_screen
from app.middlewares.allowlist import AllowlistMiddleware
from app.middlewares.callback_ack import CallbackAckMiddleware
from app.middlewares.dedupe import DedupeMiddleware
from app.middlewares.rate_limit import RateLimitMiddleware
from app.middlewares.repo import RepoMiddleware
from app.middlewares.user_sync import UserSyncMiddleware
from app.runtime.queue import Queue
from app.services.retry import is_retry_after_error, sleep_retry_after
from app.services.user_locale import get_locale
from app.ui.delivery import show
from app.webhook import readiness_probes

logger = logging.getLogger(__name__)

DEFAULT_CONTENT_DIR = Path(__file__).resolve().parents[1] / "content"


def content_directory() -> Path:
    override = os.environ.get("CONTENT_DIR")
    return Path(override) if override else DEFAULT_CONTENT_DIR


async def initialize_content(
    repo: ContentRepository, session_factory: SessionFactory
) -> None:
    path = content_directory()
    try:
        report = await seed_content(session_factory, path)
    except Exception:
        logger.warning(
            "content seed failed for %s; using existing rows", path, exc_info=True
        )
    else:
        for error in report.errors:
            logger.warning("content seed: %s", error)
        if report.skipped:
            logger.info(
                "content seed: %s admin-edited rows kept", report.skipped
            )
    try:
        await repo.load()
    except Exception:
        logger.exception("content load failed; refusing to start without content")
        raise


async def handle_error(event: ErrorEvent) -> bool:
    exception = event.exception
    if is_retry_after_error(exception):
        delay = await sleep_retry_after(exception)
        logger.warning("flood control: retried after %s seconds", delay)
        return True
    logger.error("unhandled update error: %s", exception)
    return True


def create_bot(settings: Settings) -> Bot:
    return Bot(
        token=settings.bot_token,
        default=DefaultBotProperties(parse_mode=ParseMode.HTML),
    )


def create_redis(settings: Settings) -> Redis | None:
    if not settings.redis_url:
        logger.warning("REDIS_URL is empty; dedupe and rate limiting are disabled")
        return None
    return Redis.from_url(settings.redis_url, decode_responses=True)


ORPHAN_TOAST = "Кнопка устарела — вот меню"


def register_orphan_callbacks(dispatcher: Dispatcher) -> None:
    """Answer callbacks no router claimed and put the menu back in private chat.

    Included after every handler router, so it only sees data that no screen
    recognises: an old bot release, a deleted card, a foreign inline button.
    Unlike an inline button these update ids stay valid, so the answer also
    replaces the dead message when the chat is a private one.
    """
    router = Router(name="orphan_callbacks")

    @router.callback_query()
    async def on_orphan_callback(
        query: CallbackQuery,
        bot: Bot,
        repo: ContentRepository,
        session_factory: SessionFactory,
        settings: Settings | None = None,
    ) -> None:
        await query.answer(ORPHAN_TOAST)
        message = query.message
        if message is None or getattr(message.chat, "type", None) != "private":
            return
        user_id = query.from_user.id if query.from_user else 0
        locale = await get_locale(session_factory, user_id)
        admin = bool(settings and user_id in settings.admin_id_set)
        await show(
            bot,
            message.chat.id,
            await menu_screen(repo, locale, admin),
            message.message_id,
        )

    dispatcher.include_router(router)


def create_dispatcher(settings: Settings, bot: Bot, redis: Redis | None) -> Dispatcher:
    dispatcher = Dispatcher()
    session_factory = create_session_factory(settings.database_url)
    dispatcher.workflow_data["session_factory"] = session_factory
    dispatcher.workflow_data["settings"] = settings
    # Registered before handlers so a failing callback handler still stops the spinner.
    dispatcher.callback_query.middleware(CallbackAckMiddleware())
    if session_factory is not None:
        dispatcher.update.middleware(UserSyncMiddleware(session_factory))
    if session_factory is None:
        raise RuntimeError("DATABASE_URL is required for the queue")
    repo = ContentRepository(
        session_factory, aliases_path=content_directory() / "aliases.json"
    )
    dispatcher.workflow_data["repo"] = repo
    dispatcher.workflow_data["readiness_probes"] = readiness_probes(dispatcher, redis)
    dispatcher.update.middleware(RepoMiddleware(repo))
    dispatcher.workflow_data["queue"] = Queue(
        session_factory,
        visibility_timeout_seconds=settings.queue_visibility_timeout_seconds,
    )
    dispatcher.update.middleware(DedupeMiddleware(redis))
    # Outer, not inner: aiogram re-runs a dispatcher-level inner middleware once
    # per nested router, so a 1/s private limit dropped every message.
    dispatcher.message.outer_middleware(RateLimitMiddleware(redis))
    dispatcher.message.outer_middleware(AllowlistMiddleware(settings.admin_id_set))
    dispatcher.errors.register(handle_error)
    # The ask budget is shared across replicas whenever Redis is configured.
    llm_handler.LIMITER = llm_handler.RateLimiter(redis=redis)

    async def startup_content(**_kwargs: object) -> None:
        await initialize_content(repo, session_factory)

    dispatcher.startup.register(startup_content)
    register_handlers(dispatcher, bot)
    register_orphan_callbacks(dispatcher)
    return dispatcher
