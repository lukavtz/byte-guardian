"""Polling mode must serve /healthz, /readyz, /metrics and close them on exit."""

from __future__ import annotations

import socket
from types import SimpleNamespace
from typing import Any

import pytest
from aiohttp import ClientConnectorError, ClientSession

import app.main as main_module


def free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


class FakeSession:
    async def close(self) -> None:
        return None


class FakeBot:
    def __init__(self) -> None:
        self.session = FakeSession()


class FakeDispatcher:
    """Pretends to poll: probes the health app, then returns to simulate shutdown."""

    def __init__(self, base: str) -> None:
        self.workflow_data: dict[str, Any] = {}
        self.base = base
        self.started = False
        self.stopped = False
        self.seen: dict[str, tuple[int, Any]] = {}

    def resolve_used_update_types(self) -> list[str]:
        return []

    async def start_polling(self, _bot: Any, **kwargs: Any) -> None:
        assert kwargs["drop_pending_updates"] is False
        self.started = True
        try:
            async with ClientSession() as session:
                async with session.get(f"{self.base}/healthz") as response:
                    self.seen["/healthz"] = (response.status, await response.json())
                async with session.get(f"{self.base}/readyz") as response:
                    self.seen["/readyz"] = (response.status, await response.json())
                async with session.get(f"{self.base}/metrics") as response:
                    self.seen["/metrics"] = (response.status, await response.text())
        finally:
            self.stopped = True


def _patch(monkeypatch: pytest.MonkeyPatch, dispatcher: FakeDispatcher) -> None:
    monkeypatch.setattr(main_module, "create_bot", lambda settings: FakeBot())
    monkeypatch.setattr(main_module, "create_redis", lambda settings: None)
    monkeypatch.setattr(main_module, "create_dispatcher", lambda *a, **kw: dispatcher)
    monkeypatch.setattr(main_module, "shutdown_telemetry", lambda: None)


async def test_polling_serves_health_and_shuts_it_down(monkeypatch: pytest.MonkeyPatch) -> None:
    port = free_port()
    dispatcher = FakeDispatcher(f"http://127.0.0.1:{port}")
    _patch(monkeypatch, dispatcher)

    await main_module.run_polling(SimpleNamespace(port=port, metrics_enabled=True))  # type: ignore[arg-type]

    assert dispatcher.started and dispatcher.stopped
    assert dispatcher.seen["/healthz"] == (200, {"ok": True})
    assert dispatcher.seen["/readyz"] == (200, {"ok": True, "checks": {"postgres": "skip", "redis": "skip"}})
    assert dispatcher.seen["/metrics"][0] == 200

    async with ClientSession() as session:
        with pytest.raises(ClientConnectorError):
            await session.get(f"{dispatcher.base}/healthz")


async def test_polling_omits_metrics_when_disabled(monkeypatch: pytest.MonkeyPatch) -> None:
    port = free_port()
    dispatcher = FakeDispatcher(f"http://127.0.0.1:{port}")
    _patch(monkeypatch, dispatcher)

    await main_module.run_polling(SimpleNamespace(port=port, metrics_enabled=False))  # type: ignore[arg-type]

    assert dispatcher.seen["/healthz"][0] == 200
    assert dispatcher.seen["/metrics"][0] == 404