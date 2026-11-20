"""Typed raw adapter for Bot API 10.4 methods missing from aiogram 3.31.0."""

from __future__ import annotations

from typing import Any

from aiogram import Bot
from aiogram.methods.base import TelegramMethod
from aiogram.types import (
    ChatIdUnion,
    InputRichMessage,
    Message,
    MessageEntity,
    ReplyMarkupUnion,
    ReplyParameters,
    SuggestedPostParameters,
)
from pydantic import BaseModel, ConfigDict, field_validator, model_validator


class EphemeralMessageParameters(BaseModel):
    model_config = ConfigDict(extra="allow")

    receiver_user_id: int | None = None
    callback_query_id: str | None = None
    replace_callback_query_message: bool | None = None


class SendRichMessage(TelegramMethod[Message]):
    __returning__ = Message
    __api_method__ = "sendRichMessage"

    business_connection_id: str | None = None
    chat_id: ChatIdUnion | None = None
    message_thread_id: int | None = None
    direct_messages_topic_id: int | None = None
    ephemeral_message_parameters: EphemeralMessageParameters | None = None
    rich_message: InputRichMessage
    disable_notification: bool | None = None
    protect_content: bool | None = None
    allow_paid_broadcast: bool | None = None
    message_effect_id: str | None = None
    suggested_post_parameters: SuggestedPostParameters | None = None
    reply_parameters: ReplyParameters | None = None
    reply_markup: ReplyMarkupUnion | None = None

    @model_validator(mode="after")
    def _require_target(self) -> SendRichMessage:
        if self.chat_id is None:
            parameters = self.ephemeral_message_parameters
            if parameters is None or parameters.callback_query_id is None:
                raise ValueError(
                    "chat_id is required unless the message replies to a callback query"
                )
        return self


class SendRichMessageDraft(TelegramMethod[bool]):
    __returning__ = bool
    __api_method__ = "sendRichMessageDraft"

    chat_id: int
    draft_id: int
    rich_message: InputRichMessage
    message_thread_id: int | None = None
    can_stop: bool | None = None
    keep_on_stop: bool | None = None

    @field_validator("draft_id")
    @classmethod
    def _validate_draft_id(cls, value: int) -> int:
        if value == 0:
            raise ValueError("draft_id must be non-zero")
        return value



class GetUniqueGift(TelegramMethod[dict[str, Any]]):
    __returning__ = dict
    __api_method__ = "getUniqueGift"

    gift_id: str


class GetChatShareThemeBackdrops(TelegramMethod[dict[str, Any]]):
    __returning__ = dict
    __api_method__ = "getChatShareThemeBackdrops"

    chat_id: int | str


class DeleteBusinessAccountGift(TelegramMethod[bool]):
    __returning__ = bool
    __api_method__ = "deleteBusinessAccountGift"

    business_connection_id: str
    gift_id: str


class VerifyUser(TelegramMethod[bool]):
    __returning__ = bool
    __api_method__ = "verifyUser"

    user_id: int
    custom_description: str | None = None
    custom_description_parse_mode: str | None = None
    custom_description_entities: list[MessageEntity] | None = None


class VerifyChat(TelegramMethod[bool]):
    __returning__ = bool
    __api_method__ = "verifyChat"

    chat_id: int | str
    custom_description: str | None = None
    custom_description_parse_mode: str | None = None
    custom_description_entities: list[MessageEntity] | None = None


def _build_rich_message(
    rich_message: InputRichMessage | None,
    html: str | None,
    markdown: str | None,
) -> InputRichMessage:
    if rich_message is not None:
        return rich_message
    if (html is None) == (markdown is None):
        raise ValueError("provide exactly one of rich_message, html, or markdown")
    return InputRichMessage(html=html, markdown=markdown)



async def get_unique_gift(bot: Bot, gift_id: str) -> dict[str, Any]:
    return await bot(GetUniqueGift(gift_id=gift_id))


async def get_chat_share_theme_backdrops(bot: Bot, chat_id: int | str) -> dict[str, Any]:
    return await bot(GetChatShareThemeBackdrops(chat_id=chat_id))


async def delete_business_account_gift(
    bot: Bot, business_connection_id: str, gift_id: str
) -> bool:
    return await bot(
        DeleteBusinessAccountGift(
            business_connection_id=business_connection_id, gift_id=gift_id
        )
    )


async def verify_user(
    bot: Bot,
    user_id: int,
    custom_description: str | None = None,
    custom_description_parse_mode: str | None = None,
    custom_description_entities: list[MessageEntity] | None = None,
) -> bool:
    return await bot(
        VerifyUser(
            user_id=user_id,
            custom_description=custom_description,
            custom_description_parse_mode=custom_description_parse_mode,
            custom_description_entities=custom_description_entities,
        )
    )


async def verify_chat(
    bot: Bot,
    chat_id: int | str,
    custom_description: str | None = None,
    custom_description_parse_mode: str | None = None,
    custom_description_entities: list[MessageEntity] | None = None,
) -> bool:
    return await bot(
        VerifyChat(
            chat_id=chat_id,
            custom_description=custom_description,
            custom_description_parse_mode=custom_description_parse_mode,
            custom_description_entities=custom_description_entities,
        )
    )

async def send_rich_message(
    bot: Bot,
    *,
    chat_id: int | str | None = None,
    rich_message: InputRichMessage | None = None,
    html: str | None = None,
    markdown: str | None = None,
    reply_markup: ReplyMarkupUnion | None = None,
    ephemeral_message_parameters: EphemeralMessageParameters | None = None,
) -> Message:
    return await bot(
        SendRichMessage(
            chat_id=chat_id,
            rich_message=_build_rich_message(rich_message, html, markdown),
            reply_markup=reply_markup,
            ephemeral_message_parameters=ephemeral_message_parameters,
        )
    )


async def send_rich_message_draft(
    bot: Bot,
    *,
    chat_id: int,
    draft_id: int,
    rich_message: InputRichMessage | None = None,
    html: str | None = None,
    markdown: str | None = None,
    can_stop: bool = False,
    keep_on_stop: bool = False,
) -> bool:
    return await bot(
        SendRichMessageDraft(
            chat_id=chat_id,
            draft_id=draft_id,
            rich_message=_build_rich_message(rich_message, html, markdown),
            can_stop=can_stop,
            keep_on_stop=keep_on_stop,
        )
    )
