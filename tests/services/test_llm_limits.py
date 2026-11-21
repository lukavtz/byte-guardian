"""RateLimiter: shared Redis budgets, TTL and memory fallback."""

from __future__ import annotations

import logging

import pytest
from redis.exceptions import RedisError

from app.services.llm import (
    CHAT_LIMIT,
    CHAT_WINDOW,
    GROUP_WINDOW,
    USER_LIMIT,
    RateLimiter,
)

USER = 7
CHAT = -100123


class FakePipeline:
    def __init__(self, redis: FakeRedis) -> None:
        self._redis = redis
        self._ops: list[tuple[str, str, int]] = []

    async def __aenter__(self) -> FakePipeline:
        return self

    async def __aexit__(self, *_exc: object) -> None:
        return None

    def incr(self, key: str) -> FakePipeline:
        self._ops.append(("incr", key, 1))
        return self

    def decr(self, key: str) -> FakePipeline:
        self._ops.append(("incr", key, -1))
        return self

    def expire(self, key: str, ttl: int) -> FakePipeline:
        self._ops.append(("expire", key, ttl))
        return self

    async def execute(self) -> list[object]:
        results: list[object] = []
        for op, key, value in self._ops:
            if op == "incr":
                self._redis.data[key] = self._redis.data.get(key, 0) + value
                results.append(self._redis.data[key])
            else:
                self._redis.expires[key] = value
                results.append(True)
        return results


class FakeRedis:
    def __init__(self) -> None:
        self.data: dict[str, int] = {}
        self.expires: dict[str, int] = {}

    def pipeline(self, transaction: bool = True) -> FakePipeline:
        assert transaction is True
        return FakePipeline(self)


class BrokenPipeline(FakePipeline):
    async def __aenter__(self) -> FakePipeline:
        raise RedisError("connection refused")


class BrokenRedis(FakeRedis):
    def pipeline(self, transaction: bool = True) -> BrokenPipeline:
        return BrokenPipeline(self)


def _limiter(
    redis: FakeRedis | None,
    now: list[float],
    *,
    memory_now: list[float] | None = None,
) -> RateLimiter:
    return RateLimiter(
        (lambda: (memory_now or now)[0]),
        redis=redis,  # type: ignore[arg-type]
        wall_clock=lambda: now[0],
    )


async def test_two_instances_share_one_budget() -> None:
    redis = FakeRedis()
    now = [0.0]
    first = _limiter(redis, now)
    second = _limiter(redis, now)

    allowed = [await first.allow(user_id=USER, chat_id=CHAT, group=False) for _ in range(3)]
    allowed += [await second.allow(user_id=USER, chat_id=CHAT, group=False) for _ in range(3)]

    assert allowed == [True] * USER_LIMIT + [False]
    assert sum(redis.data.values()) == 2 * USER_LIMIT  # user + chat keys


async def test_restart_does_not_reset_budget() -> None:
    redis = FakeRedis()
    now = [0.0]
    for _ in range(USER_LIMIT):
        assert await _limiter(redis, now).allow(user_id=USER, chat_id=CHAT, group=False)

    restarted = _limiter(redis, now)
    assert not await restarted.allow(user_id=USER, chat_id=CHAT, group=False)


async def test_chat_budget_is_shared_across_users() -> None:
    redis = FakeRedis()
    now = [0.0]
    for index in range(CHAT_LIMIT):
        assert await _limiter(redis, now).allow(user_id=1000 + index, chat_id=CHAT, group=False)
    assert not await _limiter(redis, now).allow(user_id=9999, chat_id=CHAT, group=False)


async def test_group_window_resets_after_ten_seconds() -> None:
    redis = FakeRedis()
    now = [0.0]
    limiter = _limiter(redis, now)

    assert await limiter.allow(user_id=USER, chat_id=CHAT, group=True)
    assert not await limiter.allow(user_id=USER, chat_id=CHAT, group=True)
    now[0] = GROUP_WINDOW + 0.1
    assert await limiter.allow(user_id=USER, chat_id=CHAT, group=True)


async def test_keys_carry_namespace_and_ttl() -> None:
    redis = FakeRedis()
    now = [0.0]
    assert await _limiter(redis, now).allow(user_id=USER, chat_id=CHAT, group=True)

    assert redis.expires
    assert all(key.startswith("rl:ask:") for key in redis.expires)
    assert set(redis.expires.values()) == {int(CHAT_WINDOW), int(GROUP_WINDOW)}
    assert any(f"user:{USER}" in key for key in redis.expires)
    assert any(f"chat:{CHAT}" in key for key in redis.expires)
    assert any(f"group:{CHAT}" in key for key in redis.expires)


async def test_rejected_call_does_not_consume_other_budgets() -> None:
    redis = FakeRedis()
    now = [0.0]
    limiter = _limiter(redis, now)
    for _ in range(USER_LIMIT):
        assert await limiter.allow(user_id=USER, chat_id=CHAT, group=False)

    assert not await limiter.allow(user_id=USER, chat_id=CHAT, group=False)
    user_key = next(key for key in redis.data if f"user:{USER}" in key)
    chat_key = next(key for key in redis.data if f"chat:{CHAT}" in key)
    assert redis.data[user_key] == USER_LIMIT
    assert redis.data[chat_key] == USER_LIMIT


async def test_redis_failure_falls_back_to_memory(caplog: pytest.LogCaptureFixture) -> None:
    now = [0.0]
    limiter = _limiter(BrokenRedis(), now)

    with caplog.at_level(logging.WARNING):
        allowed = [await limiter.allow(user_id=USER, chat_id=CHAT, group=False) for _ in range(6)]

    assert allowed == [True] * USER_LIMIT + [False]
    logged = "\n".join(record.getMessage() for record in caplog.records)
    assert "RATE_REDIS_ERROR" in logged
    assert sum("RATE_REDIS_ERROR" in record.getMessage() for record in caplog.records) == 1
    assert str(USER) not in logged and str(CHAT) not in logged


async def test_memory_limiter_keeps_group_limit() -> None:
    now = [0.0]
    limiter = RateLimiter(lambda: now[0])

    assert await limiter.allow(user_id=USER, chat_id=CHAT, group=True)
    assert not await limiter.allow(user_id=USER, chat_id=CHAT, group=True)
    now[0] = GROUP_WINDOW
    assert await limiter.allow(user_id=USER, chat_id=CHAT, group=True)