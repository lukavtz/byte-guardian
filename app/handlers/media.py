"""Photos and voice notes become the same assistant answer as plain text.

Downloads are bounded, failures are answered in words, and neither the bytes
nor the transcript ever reach the logs.
"""

from __future__ import annotations

import logging
import mimetypes

from aiogram import Bot, Dispatcher, F, Router
from aiogram.dispatcher.event.bases import SkipHandler
from aiogram.enums import ChatType
from aiogram.types import Message
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.config import Settings
from app.content.repository import ContentRepository
from app.handlers import llm as llm_handler
from app.handlers.llm import locale_of, respond
from app.services import media as media_service
from app.services.llm import is_addressed, strip_mention
from app.services.settings import set_chat_mode

logger = logging.getLogger(__name__)

DEFAULT_IMAGE_PROMPT = "Опиши, что важно по безопасности на этом изображении."

TOO_BIG = {
    "ru": "Файл больше 20 МБ — пришлите покороче или напишите текстом.",
    "en": "That file is over 20 MB — send a smaller one or just type.",
    "zh": "文件超过 20 MB，请发送更小的文件或直接输入文字。",
    "tt": "Файл 20 МБтан зур — кыскарак җибәрегез яки текст белән языгыз.",
}
IMAGE_FAIL = {
    "ru": "Не получилось разобрать изображение. Опишите словами или пришлите фото ещё раз.",
    "en": "I could not read that image. Describe it in words or send it again.",
    "zh": "无法识别这张图片。请用文字描述或重新发送。",
    "tt": "Рәсемне таный алмадым. Сүзләр белән языгыз яки яңадан җибәрегез.",
}
AUDIO_FAIL = {
    "ru": "Не получилось разобрать голосовое. Напишите, пожалуйста, текстом.",
    "en": "I could not process that voice message. Please type it instead.",
    "zh": "无法处理这条语音。请改用文字。",
    "tt": "Тавыш хәбәрен эшкәртә алмадым. Текст белән языгыз, зинһар.",
}
EMPTY_VOICE = {
    "ru": "Голосовое пустое — повторите или напишите текстом.",
    "en": "That voice message came through empty — repeat it or type instead.",
    "zh": "语音是空的——请重复或改用文字。",
    "tt": "Тавыш хәбәре буш — кабатлагыз яки текст белән языгыз.",
}

router = Router(name="media")


def _private(message: Message) -> bool:
    return message.chat.type == ChatType.PRIVATE


async def _enter_chat(
    session_factory: async_sessionmaker[AsyncSession] | None, message: Message
) -> None:
    """Media starts the conversation too, so «Выйти» always has a state to leave."""
    if session_factory is None or message.from_user is None:
        return
    await set_chat_mode(session_factory, message.from_user.id, True)


async def _send(message: Message, text: str, private: bool) -> None:
    if private:
        await message.answer(text)
    else:
        await message.reply(text)


def _image_file(message: Message) -> tuple[str, int | None, str | None] | None:
    """The largest photo, or an image document; anything else is not ours."""
    if message.photo:
        best = message.photo[-1]
        return best.file_id, best.file_size, None
    document = message.document
    if document is None:
        return None
    mime = document.mime_type or mimetypes.guess_type(document.file_name or "")[0]
    if not (mime or "").startswith("image/"):
        return None
    return document.file_id, document.file_size, mime


def _audio_file(message: Message) -> tuple[str, int | None, str, str] | None:
    if message.voice is not None:
        voice = message.voice
        return voice.file_id, voice.file_size, "voice.ogg", voice.mime_type or "audio/ogg"
    if message.audio is not None:
        audio = message.audio
        return (
            audio.file_id,
            audio.file_size,
            audio.file_name or "audio.ogg",
            audio.mime_type or "audio/mpeg",
        )
    return None


@router.message(F.photo | F.document)
async def on_photo(
    message: Message,
    bot: Bot,
    settings: Settings,
    repo: ContentRepository | None = None,
    session_factory: async_sessionmaker[AsyncSession] | None = None,
) -> None:
    found = _image_file(message)
    if found is None:
        raise SkipHandler()
    private = _private(message)
    if not private and not is_addressed(message, llm_handler.BOT_USERNAME, bot.id):
        raise SkipHandler()
    file_id, size, mime = found
    locale = await locale_of(message, session_factory)
    if private:
        await _enter_chat(session_factory, message)
    try:
        data = await media_service.download(bot, file_id, declared_size=size)
    except media_service.MediaTooLarge:
        await _send(message, TOO_BIG[locale], private)
        return
    except media_service.MediaUnavailable:
        await _send(message, IMAGE_FAIL[locale], private)
        return
    question = strip_mention((message.caption or "").strip(), llm_handler.BOT_USERNAME)
    await respond(
        message,
        question or DEFAULT_IMAGE_PROMPT,
        bot=bot,
        settings=settings,
        repository=repo,
        bot_username=llm_handler.BOT_USERNAME,
        session_factory=session_factory,
        chat=private,
        image=media_service.image_data_url(data, mime),
        failure_text=IMAGE_FAIL[locale],
    )


@router.message(F.voice | F.audio)
async def on_audio(
    message: Message,
    bot: Bot,
    settings: Settings,
    repo: ContentRepository | None = None,
    session_factory: async_sessionmaker[AsyncSession] | None = None,
) -> None:
    found = _audio_file(message)
    if found is None:
        raise SkipHandler()
    private = _private(message)
    if not private and not is_addressed(message, llm_handler.BOT_USERNAME, bot.id):
        raise SkipHandler()
    file_id, size, filename, mime = found
    locale = await locale_of(message, session_factory)
    if private:
        await _enter_chat(session_factory, message)
    try:
        data = await media_service.download(bot, file_id, declared_size=size)
        transcript = await media_service.transcribe(settings, data, filename=filename, mime=mime)
    except media_service.MediaTooLarge:
        await _send(message, TOO_BIG[locale], private)
        return
    except media_service.MediaUnavailable:
        await _send(message, AUDIO_FAIL[locale], private)
        return
    transcript = strip_mention(transcript, llm_handler.BOT_USERNAME)
    if not transcript:
        await _send(message, EMPTY_VOICE[locale], private)
        return
    await respond(
        message,
        transcript,
        bot=bot,
        settings=settings,
        repository=repo,
        bot_username=llm_handler.BOT_USERNAME,
        session_factory=session_factory,
        chat=private,
    )


def register(dp: Dispatcher, _bot: Bot) -> None:
    dp.include_router(router)