from __future__ import annotations

import asyncio
import time
from typing import Any

import pytest

from app.runtime.queue import ClaimedJob, Queue
from app.runtime.worker import run_worker

VISIBILITY = 0.15
JOB_SECONDS = 0.6


class FakeQueue:
    """A queue with a reaper that reclaims jobs whose lease went stale."""

    def __init__(self) -> None:
        self.heartbeat_interval_seconds = VISIBILITY / 3
        self.pending = True
        self.running = False
        self.locked_at = 0.0
        self.claims = 0
        self.completed: list[str] = []
        self.heartbeats = 0
        self.failed: list[str] = []

    async def recover_stuck(self) -> int:
        return 0

    async def claim(self, _worker_id: str) -> ClaimedJob | None:
        if not self.pending:
            return None
        self.pending = False
        self.running = True
        self.claims += 1
        self.locked_at = time.monotonic()
        return ClaimedJob(id="j1", kind="slow", payload=None, attempts=1, max_attempts=5)

    async def heartbeat(self, _job_id: str) -> None:
        self.heartbeats += 1
        if self.running:
            self.locked_at = time.monotonic()

    async def complete(self, job_id: str) -> None:
        self.completed.append(job_id)
        self.running = False

    async def fail(self, job_id: str, _error: object) -> None:
        self.failed.append(job_id)
        self.running = False

    def reap(self) -> None:
        if self.running and time.monotonic() - self.locked_at > VISIBILITY:
            self.running = False
            self.pending = True


@pytest.mark.asyncio
async def test_long_job_heartbeat_prevents_reclaim_and_duplicate_run() -> None:
    queue = FakeQueue()
    executions: list[str] = []

    async def handler(job: ClaimedJob, _stop: asyncio.Event) -> None:
        executions.append(job.id)
        deadline = time.monotonic() + JOB_SECONDS
        while time.monotonic() < deadline:
            queue.reap()
            await asyncio.sleep(0.02)

    stop = asyncio.Event()
    worker = asyncio.create_task(
        run_worker(queue, {"slow": handler}, stop, concurrency=2, poll_seconds=0.01)  # type: ignore[arg-type]
    )
    await asyncio.sleep(JOB_SECONDS + 0.2)
    stop.set()
    await asyncio.wait_for(worker, timeout=5)

    assert queue.claims == 1
    assert executions == ["j1"]
    assert queue.completed == ["j1"]
    assert queue.failed == []
    assert queue.heartbeats >= 3


@pytest.mark.asyncio
async def test_heartbeat_stops_when_the_job_finishes() -> None:
    queue = FakeQueue()
    executions: list[str] = []

    async def handler(job: ClaimedJob, _stop: asyncio.Event) -> None:
        executions.append(job.id)
        await asyncio.sleep(0.1)

    stop = asyncio.Event()
    worker = asyncio.create_task(
        run_worker(queue, {"slow": handler}, stop, concurrency=1, poll_seconds=0.01)  # type: ignore[arg-type]
    )
    await asyncio.sleep(0.3)
    stop.set()
    await asyncio.wait_for(worker, timeout=5)

    beats = queue.heartbeats
    await asyncio.sleep(0.15)
    assert queue.heartbeats == beats


def test_heartbeat_cadence_is_a_third_of_visibility() -> None:
    queue = Queue(Any, visibility_timeout_seconds=90)  # type: ignore[arg-type]
    assert queue.heartbeat_interval_seconds == 30