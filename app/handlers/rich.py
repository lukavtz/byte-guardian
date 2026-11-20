"""Rich-message demo: stream a draft, then replace it with the final message."""

from __future__ import annotations

from aiogram import Bot, Dispatcher, Router
from aiogram.filters import Command
from aiogram.methods import SendRichMessage, SendRichMessageDraft
from aiogram.types import InputRichMessage, Message

router = Router(name="rich")

RICH_DRAFT_ID = 1
RICH_DRAFT_HTML = "<b>Generating rich message…</b>"
RICH_FINAL_HTML = "<b>Rich message</b>\nThis message was sent with <code>sendRichMessage</code>."


@router.message(Command("rich"))
async def rich_demo(message: Message, bot: Bot) -> None:
    chat_id = message.chat.id
    await bot(
        SendRichMessageDraft(
            chat_id=chat_id,
            draft_id=RICH_DRAFT_ID,
            rich_message=InputRichMessage(html=RICH_DRAFT_HTML),
        )
    )
    await bot(
        SendRichMessage(
            chat_id=chat_id,
            rich_message=InputRichMessage(html=RICH_FINAL_HTML),
        )
    )


def register(dp: Dispatcher, _bot: Bot) -> None:
    dp.include_router(router)