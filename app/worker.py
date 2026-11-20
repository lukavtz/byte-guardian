from __future__ import annotations

import asyncio
import contextlib
import signal

from app.bot import create_bot, create_redis
from app.config import get_settings
from app.db.session import create_session_factory
from app.jobs import JobContext, load_job_handlers
from app.logger import configure_logging
from app.runtime.queue import Queue
from app.runtime.telemetry import init_telemetry, shutdown_telemetry
from app.runtime.worker import run_worker

SHUTDOWN_TIMEOUT_SECONDS = 30


def _install_signal_handlers(stop: asyncio.Event) -> None:
    loop = asyncio.get_running_loop()
    for name in ("SIGINT", "SIGTERM"):
        signum = getattr(signal, name)
        try:
            loop.add_signal_handler(signum, stop.set)
        except NotImplementedError:  # Windows event loops
            signal.signal(signum, lambda *_: loop.call_soon_threadsafe(stop.set))


async def main() -> None:
    settings = get_settings()
    configure_logging(settings.log_level)
    init_telemetry(
        settings.otel_service_name or "byte_guardian",
        settings.otel_exporter_otlp_endpoint,
        settings.sentry_dsn,
    )

    bot = create_bot(settings)
    redis = create_redis(settings)
    session_factory = create_session_factory(settings.database_url)
    if session_factory is None:
        raise RuntimeError("DATABASE_URL is required for the worker")
    engine = session_factory.kw["bind"]
    queue = Queue(
        session_factory,
        visibility_timeout_seconds=settings.queue_visibility_timeout_seconds,
    )
    ctx = JobContext(
        bot=bot,
        session_factory=session_factory,
        queue=queue,
        settings=settings,
        redis=redis,
    )
    handlers = load_job_handlers(ctx)

    stop = asyncio.Event()
    _install_signal_handlers(stop)

    worker_task = asyncio.create_task(run_worker(queue, handlers, stop))
    stop_task = asyncio.create_task(stop.wait())
    try:
        done, _ = await asyncio.wait(
            {worker_task, stop_task}, return_when=asyncio.FIRST_COMPLETED
        )
        if worker_task in done:
            await worker_task
        else:
            try:
                await asyncio.wait_for(worker_task, timeout=SHUTDOWN_TIMEOUT_SECONDS)
            except TimeoutError:
                worker_task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await worker_task
    finally:
        stop_task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await stop_task
        await bot.session.close()
        if redis is not None:
            await redis.aclose()
        await engine.dispose()
        shutdown_telemetry()


if __name__ == "__main__":
    asyncio.run(main())
