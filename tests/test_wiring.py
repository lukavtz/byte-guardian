"""Offline wiring checks: content repo middleware, startup order, migration imports."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

import app.bot as bot_module
from app.content.repository import ContentRepository
from app.middlewares.repo import RepoMiddleware

ROOT = Path(__file__).resolve().parents[1]


class FakeRepo:
    def __init__(self) -> None:
        self.calls: list[str] = []

    async def load(self) -> None:
        self.calls.append("load")


def _settings() -> SimpleNamespace:
    return SimpleNamespace(
        database_url="",
        admin_id_set=frozenset(),
        queue_visibility_timeout_seconds=60,
    )


def _dispatcher(monkeypatch: pytest.MonkeyPatch, repo: FakeRepo) -> Any:
    monkeypatch.setattr(bot_module, "create_session_factory", lambda url: object())
    monkeypatch.setattr(bot_module, "ContentRepository", lambda *a, **kw: repo)
    monkeypatch.setattr(bot_module, "Queue", lambda *a, **kw: object())
    monkeypatch.setattr(bot_module, "register_handlers", lambda *a, **kw: None)
    monkeypatch.setattr(bot_module, "seed_content", _fake_seed(repo))
    return bot_module.create_dispatcher(_settings(), None, None)


def _fake_seed(repo: FakeRepo):
    async def seed(session_factory: Any, path: Path) -> Any:
        repo.calls.append("seed")
        return SimpleNamespace(errors=[])

    return seed


@pytest.mark.asyncio
async def test_repo_middleware_injects_repository() -> None:
    repo = FakeRepo()
    seen: dict[str, Any] = {}

    async def handler(event: object, data: dict[str, Any]) -> str:
        seen.update(data)
        return "ok"

    result = await RepoMiddleware(repo)(handler, object(), {"bot": None})

    assert result == "ok"
    assert seen["repo"] is repo


def test_create_dispatcher_registers_repo(monkeypatch: pytest.MonkeyPatch) -> None:
    repo = FakeRepo()
    dispatcher = _dispatcher(monkeypatch, repo)

    assert dispatcher.workflow_data["repo"] is repo
    middlewares = dispatcher.update.middleware._middlewares  # type: ignore[attr-defined]
    assert any(middleware._repo is repo for middleware in middlewares if hasattr(middleware, "_repo"))


def test_message_middlewares_are_outer_so_they_run_once(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Inner dispatcher middleware re-runs per router; a 1/s private limit then blocks all."""
    dispatcher = _dispatcher(monkeypatch, FakeRepo())

    outer = dispatcher.message.outer_middleware._middlewares  # type: ignore[attr-defined]
    inner = dispatcher.message.middleware._middlewares  # type: ignore[attr-defined]
    assert any(type(m).__name__ == "RateLimitMiddleware" for m in outer)
    assert any(type(m).__name__ == "AllowlistMiddleware" for m in outer)
    assert not any(
        type(m).__name__ in {"RateLimitMiddleware", "AllowlistMiddleware"} for m in inner
    )


@pytest.mark.asyncio
async def test_startup_seeds_then_loads(monkeypatch: pytest.MonkeyPatch) -> None:
    repo = FakeRepo()
    dispatcher = _dispatcher(monkeypatch, repo)

    await dispatcher.startup.trigger()

    assert repo.calls == ["seed", "load"]


@pytest.mark.asyncio
async def test_startup_seed_error_still_loads(monkeypatch: pytest.MonkeyPatch) -> None:
    repo = FakeRepo()
    dispatcher = _dispatcher(monkeypatch, repo)

    async def boom(session_factory: Any, path: Path) -> Any:
        repo.calls.append("seed")
        raise RuntimeError("db down")

    monkeypatch.setattr(bot_module, "seed_content", boom)
    await dispatcher.startup.trigger()

    assert repo.calls == ["seed", "load"]


@pytest.mark.asyncio
async def test_startup_load_error_propagates(monkeypatch: pytest.MonkeyPatch) -> None:
    class BrokenRepo(FakeRepo):
        async def load(self) -> None:
            raise RuntimeError("no content")

    repo = BrokenRepo()
    dispatcher = _dispatcher(monkeypatch, repo)

    with pytest.raises(RuntimeError, match="no content"):
        await dispatcher.startup.trigger()


def test_content_dir_env_override(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("CONTENT_DIR", str(tmp_path))
    assert bot_module.content_directory() == tmp_path
    monkeypatch.delenv("CONTENT_DIR")
    assert bot_module.content_directory() == ROOT / "content"
    assert (ROOT / "content" / "aliases.json").is_file()


def test_migrations_env_imports_content_tables() -> None:
    source = (ROOT / "migrations" / "env.py").read_text(encoding="utf-8")
    assert "import app.db.content" in source
    import app.db.content  # noqa: F401  registers tables on Base.metadata
    from app.db.models import Base

    tables = Base.metadata.tables
    assert {"category", "threat", "threat_text", "quiz_question", "quiz_answer", "settings"} <= set(tables)


def test_handlers_register_in_name_order(monkeypatch: pytest.MonkeyPatch) -> None:
    """`llm` must claim private text before `search`; directory order is not enough."""
    import app.handlers as package

    seen: list[str] = []

    def fake_iter(path: list[str]) -> Any:
        return iter(
            [
                SimpleNamespace(name="search"),
                SimpleNamespace(name="llm"),
                SimpleNamespace(name="_ignored"),
            ]
        )

    def fake_import(name: str) -> Any:
        short = name.rsplit(".", 1)[-1]
        return SimpleNamespace(register=lambda dp, bot: seen.append(short))

    monkeypatch.setattr(package.pkgutil, "iter_modules", fake_iter)
    monkeypatch.setattr(package.importlib, "import_module", fake_import)

    package.register_handlers(object(), None)  # type: ignore[arg-type]

    assert seen == ["llm", "search"]


def test_repository_accepts_session_factory() -> None:
    repo = ContentRepository(object())  # type: ignore[arg-type]
    assert isinstance(repo, ContentRepository)
