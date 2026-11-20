from __future__ import annotations

from typing import Any

from aiogram import Bot, Dispatcher, Router
from aiogram.filters import Command, CommandObject
from aiogram.types import Message, Update

from app.runtime.queue import Queue

KIND = "llm.request"
USAGE_TEXT = "Usage: /llm <prompt>"
WORKING_TEXT = "Working on it."

router = Router(name="llm")


async def enqueue_llm(
    queue: Queue,
    *,
    chat_id: int,
    prompt: str,
    reply_to_message_id: int | None,
    update_id: int,
) -> str:
    payload: dict[str, Any] = {"chatId": chat_id, "prompt": prompt, "updateId": update_id}
    if reply_to_message_id is not None:
        payload["replyToMessageId"] = reply_to_message_id
    return await queue.enqueue(KIND, payload, idempotency_key="llm:" + str(update_id))


@router.message(Command("llm"))
async def llm_command(
    message: Message,
    command: CommandObject,
    queue: Queue,
    event_update: Update,
) -> None:
    prompt = (command.args or "").strip()
    if not prompt:
        await message.answer(USAGE_TEXT)
        return
    await enqueue_llm(
        queue,
        chat_id=message.chat.id,
        prompt=prompt,
        reply_to_message_id=message.message_id,
        update_id=event_update.update_id,
    )
    await message.answer(WORKING_TEXT)


def register(dp: Dispatcher, _bot: Bot) -> None:
    dp.include_router(router)
