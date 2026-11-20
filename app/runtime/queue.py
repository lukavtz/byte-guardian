from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

logger = logging.getLogger(__name__)

ENQUEUE_SQL = text(
    """
    INSERT INTO jobs (kind, payload, idempotency_key, run_at, max_attempts)
    VALUES (:kind, CAST(:payload AS jsonb), :idempotency_key,
            COALESCE(CAST(:run_at AS timestamptz), now()),
            COALESCE(CAST(:max_attempts AS integer), 5))
    ON CONFLICT (idempotency_key) DO NOTHING
    RETURNING id
    """
)

SELECT_ID_SQL = text("SELECT id FROM jobs WHERE idempotency_key = :idempotency_key")

CLAIM_SQL = text(
    """
    WITH next AS (
      SELECT id FROM jobs
      WHERE status = 'pending' AND run_at <= now()
      ORDER BY run_at, id
      FOR UPDATE SKIP LOCKED LIMIT 1
    )
    UPDATE jobs j SET status = 'running', locked_at = now(), locked_by = :worker_id,
      attempts = j.attempts + 1
    FROM next WHERE j.id = next.id
    RETURNING j.id, j.kind, j.payload, j.attempts, j.max_attempts
    """
)

COMPLETE_SQL = text(
    """
    UPDATE jobs SET status = 'done', finished_at = now(),
      locked_at = NULL, locked_by = NULL
    WHERE id = :job_id AND status = 'running'
    """
)

FAIL_SQL = text(
    """
    UPDATE jobs
    SET status = CASE WHEN attempts >= max_attempts THEN 'dead' ELSE 'pending' END,
      last_error = :last_error,
      finished_at = CASE WHEN attempts >= max_attempts THEN now() ELSE NULL END,
      locked_at = NULL,
      locked_by = NULL,
      run_at = CASE
        WHEN attempts >= max_attempts THEN run_at
        ELSE now() + make_interval(secs => least(300, 5 * power(2, attempts)))
      END
    WHERE id = :job_id AND status = 'running'
    """
)

HEARTBEAT_SQL = text(
    """
    UPDATE jobs SET locked_at = now()
    WHERE id = :job_id AND status = 'running'
    """
)

RECOVER_SQL = text(
    """
    UPDATE jobs SET status = 'pending', locked_at = NULL, locked_by = NULL
    WHERE status = 'running'
      AND locked_at < now() - make_interval(secs => :visibility_timeout)
    """
)


@dataclass(frozen=True)
class ClaimedJob:
    id: str
    kind: str
    payload: Any
    attempts: int
    max_attempts: int


def clip_error(error: object) -> str:
    return str(error).encode("utf-8")[:2048].decode("utf-8", errors="ignore")


class Queue:
    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        *,
        visibility_timeout_seconds: int = 60,
    ) -> None:
        self._session_factory = session_factory
        self._visibility_timeout_seconds = visibility_timeout_seconds

    @property
    def heartbeat_interval_seconds(self) -> float:
        """How often a running job proves it is alive, before the reaper looks."""
        return self._visibility_timeout_seconds / 3

    async def enqueue(
        self,
        kind: str,
        payload: Any,
        *,
        idempotency_key: str | None = None,
        run_at: datetime | None = None,
        max_attempts: int = 5,
    ) -> str:
        async with self._session_factory() as session:
            async with session.begin():
                result = await session.execute(
                    ENQUEUE_SQL,
                    {
                        "kind": kind,
                        "payload": json.dumps(payload),
                        "idempotency_key": idempotency_key,
                        "run_at": run_at,
                        "max_attempts": max_attempts,
                    },
                )
                row = result.first()
                if row is not None:
                    return str(row[0])
                if idempotency_key is None:
                    raise RuntimeError("enqueue failed")
                existing = await session.execute(
                    SELECT_ID_SQL, {"idempotency_key": idempotency_key}
                )
                existing_row = existing.first()
                if existing_row is None:
                    raise RuntimeError("enqueue failed")
                return str(existing_row[0])

    async def claim(self, worker_id: str) -> ClaimedJob | None:
        async with self._session_factory() as session:
            async with session.begin():
                result = await session.execute(CLAIM_SQL, {"worker_id": worker_id})
                row = result.mappings().first()
                if row is None:
                    return None
                payload = row["payload"]
                if isinstance(payload, str):
                    payload = json.loads(payload)
                return ClaimedJob(
                    id=str(row["id"]),
                    kind=str(row["kind"]),
                    payload=payload,
                    attempts=int(row["attempts"]),
                    max_attempts=int(row["max_attempts"]),
                )

    async def complete(self, job_id: str) -> None:
        async with self._session_factory() as session:
            async with session.begin():
                await session.execute(COMPLETE_SQL, {"job_id": job_id})

    async def fail(self, job_id: str, error: object) -> None:
        async with self._session_factory() as session:
            async with session.begin():
                await session.execute(
                    FAIL_SQL, {"job_id": job_id, "last_error": clip_error(error)}
                )

    async def heartbeat(self, job_id: str) -> None:
        """Extend the lease of a running job so the reaper leaves it alone."""
        async with self._session_factory() as session:
            async with session.begin():
                await session.execute(HEARTBEAT_SQL, {"job_id": job_id})

    async def recover_stuck(self) -> int:
        async with self._session_factory() as session:
            async with session.begin():
                result = await session.execute(
                    RECOVER_SQL,
                    {"visibility_timeout": self._visibility_timeout_seconds},
                )
                return getattr(result, "rowcount", 0) or 0
