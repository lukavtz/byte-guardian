from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol

import httpx
from aiogram import Bot
from aiogram.types import ReplyParameters
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.config import Settings
from app.runtime.queue import ClaimedJob, Queue

KIND = "llm.request"
ERROR_TEXT = "The assistant is unavailable right now. Please try again later."
TIMEOUT_SECONDS = 8.0
MAX_TOKENS = 800


@dataclass(frozen=True)
class LlmConfig:
    base_url: str
    model: str
    api_key: str


class JobContext(Protocol):
    bot: Bot
    session_factory: async_sessionmaker[AsyncSession]
    queue: Queue
    settings: Settings
    redis: Redis | None


def llm_config(settings: Settings) -> LlmConfig | None:
    base_url = settings.llm_base_url.rstrip("/")
    if not base_url or not settings.llm_model or not settings.llm_api_key:
        return None
    return LlmConfig(
        base_url=base_url,
        model=settings.llm_model,
        api_key=settings.llm_api_key,
    )


def _read_content(payload: Any) -> str | None:
    if not isinstance(payload, dict):
        return None
    choices = payload.get("choices")
    if not isinstance(choices, list) or not choices:
        return None
    first = choices[0]
    if not isinstance(first, dict):
        return None
    message = first.get("message")
    if not isinstance(message, dict):
        return None
    content = message.get("content")
    if isinstance(content, str) and content.strip():
        return content.strip()
    return None


# The prompt and the answer stay in memory only; no log sink is used.
async def ask(prompt: str, config: LlmConfig | None) -> str:
    if config is None:
        return ERROR_TEXT
    try:
        async with httpx.AsyncClient(timeout=8.0) as client:
            response = await client.post(
                config.base_url + "/chat/completions",
                headers={"authorization": "Bearer " + config.api_key},
                json={
                    "model": config.model,
                    "messages": [{"role": "user", "content": prompt}],
                    "max_tokens": MAX_TOKENS,
                },
            )
        if response.status_code != 200:
            return ERROR_TEXT
        return _read_content(response.json()) or ERROR_TEXT
    except Exception:  # noqa: BLE001
        return ERROR_TEXT


def _parse_payload(payload: Any) -> dict[str, Any]:
    if not isinstance(payload, dict):
        raise ValueError("invalid llm payload")
    chat_id = payload.get("chatId")
    prompt = payload.get("prompt")
    update_id = payload.get("updateId")
    reply_to = payload.get("replyToMessageId")
    if not isinstance(chat_id, int) or isinstance(chat_id, bool):
        raise ValueError("invalid llm payload")
    if not isinstance(prompt, str) or not isinstance(update_id, int) or isinstance(update_id, bool):
        raise ValueError("invalid llm payload")
    if reply_to is not None and (not isinstance(reply_to, int) or isinstance(reply_to, bool)):
        raise ValueError("invalid llm payload")
    parsed: dict[str, Any] = {"chatId": chat_id, "prompt": prompt, "updateId": update_id}
    if reply_to is not None:
        parsed["replyToMessageId"] = reply_to
    return parsed


async def handle(job: ClaimedJob, ctx: JobContext) -> None:
    payload = _parse_payload(job.payload)
    answer = await ask(str(payload["prompt"]), llm_config(ctx.settings))
    reply_to = payload.get("replyToMessageId")
    if reply_to is None:
        await ctx.bot.send_message(payload["chatId"], answer)
    else:
        await ctx.bot.send_message(
            payload["chatId"], answer, reply_parameters=ReplyParameters(message_id=reply_to)
        )
