from __future__ import annotations

from aiogram import Bot, Dispatcher, Router
from aiogram.filters import Command
from aiogram.types import Message

from app.services.api104 import send_rich_message, send_rich_message_draft

router = Router(name="rich")

RICH_DRAFT_ID = 1
RICH_DRAFT_HTML = "<b>Generating rich message…</b>"
RICH_FINAL_HTML = "<b>Rich message</b>\nThis message was sent with <code>sendRichMessage</code>."


@router.message(Command("rich"))
async def rich_demo(message: Message, bot: Bot) -> None:
    chat_id = message.chat.id
    await send_rich_message_draft(
        bot,
        chat_id=chat_id,
        draft_id=RICH_DRAFT_ID,
        html=RICH_DRAFT_HTML,
    )
    await send_rich_message(bot, chat_id=chat_id, html=RICH_FINAL_HTML)


def register(dp: Dispatcher, _bot: Bot) -> None:
    dp.include_router(router)
