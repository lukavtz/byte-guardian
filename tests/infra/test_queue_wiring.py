from __future__ import annotations

from pathlib import Path

APP = Path(__file__).resolve().parents[2] / "app"
WORKER = (APP / "worker.py").read_text(encoding="utf-8")
QUEUE = (APP / "runtime" / "queue.py").read_text(encoding="utf-8")
JOBS = (APP / "jobs" / "__init__.py").read_text(encoding="utf-8")


def test_worker_entrypoint_imports_queue_runtime() -> None:
    assert "from app.runtime.queue import Queue" in WORKER
    assert "from app.runtime.worker import run_worker" in WORKER
    assert "from app.jobs import JobContext, load_job_handlers" in WORKER


def test_worker_entrypoint_never_polls_or_serves_webhooks() -> None:
    for forbidden in ("start_polling", "Dispatcher", "app.webhook"):
        assert forbidden not in WORKER


def test_queue_claims_skip_locked() -> None:
    assert "FOR UPDATE SKIP LOCKED LIMIT 1" in QUEUE


def test_jobs_package_auto_loads_handlers() -> None:
    assert "load_job_handlers" in JOBS
