"""Bounded in-process conversation history; one scratchpad per (chat, user).

No persistence by design: the bot runs as one process, and this is a 6-turn
short-term memory, not a profile. Text is scrubbed before it is stored, and
nothing here is ever logged.
"""

from __future__ import annotations

import re
import time
from collections import OrderedDict, deque
from collections.abc import Callable
from dataclasses import dataclass
from typing import Literal

from app.runtime.secrets import read_secret

TURN_CHARS = 500
MAX_TURNS = 6
MAX_CONVERSATIONS = 5_000

_BEARER_RE = re.compile(r"(?i)\bbearer\s+\S+")
_TOKEN_RE = re.compile(r"\b\d{6,}:[A-Za-z0-9_-]{30,}\b")
_SK_RE = re.compile(r"\bsk-[A-Za-z0-9_-]{16,}\b")
_HTTP_RE = re.compile(r"https?://\S+")


@dataclass(frozen=True, slots=True)
class Turn:
    role: Literal["user", "assistant"]
    text: str
    at: float


def _scrub(text: str) -> str:
    scrubbed = _BEARER_RE.sub(" ", text)
    scrubbed = _TOKEN_RE.sub(" ", scrubbed)
    scrubbed = _SK_RE.sub(" ", scrubbed)
    for name in ("LLM_API_KEY", "BOT_TOKEN"):
        try:
            value = read_secret(name)
        except RuntimeError:
            value = None
        if value:
            scrubbed = scrubbed.replace(value, " ")
    scrubbed = _HTTP_RE.sub(" ", scrubbed)
    return " ".join(scrubbed.split())[:TURN_CHARS]


class ConversationStore:
    """Last `turns` messages per user, expired by monotonic time."""

    def __init__(
        self,
        *,
        turns: int = MAX_TURNS,
        ttl: float = 1_800.0,
        max_conversations: int = MAX_CONVERSATIONS,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._turns = max(1, turns)
        self._ttl = max(0.0, ttl)
        self._max = max(1, max_conversations)
        self._clock = clock
        # Most recently active key last: eviction order follows the last Turn.at.
        self._data: OrderedDict[tuple[int, int], deque[Turn]] = OrderedDict()

    def recent(self, *, chat_id: int, user_id: int, group: bool) -> tuple[Turn, ...]:
        key = (chat_id, user_id)
        bucket = self._data.get(key)
        if not bucket:
            return ()
        now = self._clock()
        alive = [turn for turn in bucket if now - turn.at <= self._ttl]
        if not alive:
            self._data.pop(key, None)
            return ()
        bucket.clear()
        bucket.extend(alive)
        return tuple(alive)

    def append(
        self,
        *,
        chat_id: int,
        user_id: int,
        group: bool,
        role: Literal["user", "assistant"],
        text: str,
    ) -> None:
        cleaned = _scrub(text)
        if not cleaned:
            return
        key = (chat_id, user_id)
        bucket = self._data.get(key)
        if bucket is None:
            bucket = deque(maxlen=self._turns)
            self._data[key] = bucket
        bucket.append(Turn(role=role, text=cleaned, at=self._clock()))
        # Activity is what keeps a conversation: the least recently active one
        # goes first, even when it was the first ever inserted.
        self._data.move_to_end(key)
        while len(self._data) > self._max:
            self._data.popitem(last=False)

    def clear(self, *, chat_id: int, user_id: int) -> None:
        self._data.pop((chat_id, user_id), None)