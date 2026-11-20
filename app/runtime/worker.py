from __future__ import annotations

import asyncio
import logging
import os
import time
from collections.abc import Awaitable, Callable, Mapping

from app.runtime.queue import ClaimedJob, Queue

logger = logging.getLogger(__name__)

CONCURRENCY = 5
POLL_SECONDS = 0.5

JobHandler = Callable[[ClaimedJob, asyncio.Event], Awaitable[None]]


async def _sleep(stop: asyncio.Event, seconds: float) -> None:
    try:
        await asyncio.wait_for(stop.wait(), timeout=seconds)
    except TimeoutError:
        pass


async def run_worker(
    queue: Queue,
    handlers: Mapping[str, JobHandler],
    stop: asyncio.Event,
    *,
    concurrency: int = CONCURRENCY,
    poll_seconds: float = POLL_SECONDS,
) -> None:
    worker_id = os.environ.get("WORKER_ID") or str(os.getpid())
    await queue.recover_stuck()
    semaphore = asyncio.Semaphore(concurrency)
    tasks: set[asyncio.Task[None]] = set()

    async def run_job(job: ClaimedJob) -> None:
        started = time.monotonic()
        try:
            handler = handlers.get(job.kind)
            if handler is None:
                raise RuntimeError("no handler for job kind: " + job.kind)
            await handler(job, stop)
            await queue.complete(job.id)
        except Exception as error:  # noqa: BLE001
            try:
                await queue.fail(job.id, error)
            except Exception:  # noqa: BLE001
                logger.exception("failed to record job failure", extra={"job_id": job.id})
            logger.exception(
                "job failed", extra={"job_id": job.id, "kind": job.kind}
            )
        finally:
            logger.info(
                "job finished",
                extra={
                    "job_id": job.id,
                    "kind": job.kind,
                    "attempt": job.attempts,
                    "duration_ms": int((time.monotonic() - started) * 1000),
                },
            )
            semaphore.release()

    try:
        while not stop.is_set():
            await semaphore.acquire()
            if stop.is_set():
                semaphore.release()
                break
            try:
                job = await queue.claim(worker_id)
            except Exception:  # noqa: BLE001
                semaphore.release()
                logger.exception("queue claim failed")
                await _sleep(stop, poll_seconds)
                continue
            if job is None:
                semaphore.release()
                await _sleep(stop, poll_seconds)
                continue
            task = asyncio.create_task(run_job(job))
            tasks.add(task)
            task.add_done_callback(tasks.discard)
    finally:
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
