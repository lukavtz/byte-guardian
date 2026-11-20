"""Ask Byte: one RAG answer per question, with search as the fallback."""

from __future__ import annotations

import logging

from aiogram import Bot, Dispatcher, F, Router
from aiogram.dispatcher.event.bases import SkipHandler
from aiogram.enums import ChatType
from aiogram.filters import Command, CommandObject
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup, Message
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.config import Settings
from app.content.repository import ContentRepository
from app.content.types import CardView
from app.services import llm as llm_service
from app.services.callbacks import cb_chat_exit, cb_menu
from app.services.i18n import resolve_locale, t
from app.services.llm import (
    RateLimiter,
    build_messages,
    context_cards,
    is_addressed,
    strip_html,
    strip_mention,
)
from app.services.settings import chat_mode_on
from app.services.user_locale import get_locale
from app.ui.delivery import show
from app.ui.emoji import custom_id
from app.ui.keyboards import button
from app.ui.render import search_screen

logger = logging.getLogger(__name__)

BOT_USERNAME: str | None = None

LIMITER = RateLimiter()

USAGE = {
    "ru": "Напишите вопрос: /ask &lt;вопрос&gt;.",
    "en": "Ask a question: /ask &lt;question&gt;.",
    "zh": "请提问：/ask &lt;问题&gt;。",
    "tt": "Сорау языгыз: /ask &lt;сорау&gt;.",
}
RATE = {
    "ru": "Слишком часто. Подождите минуту и спросите снова.",
    "en": "Too many questions. Wait a minute and ask again.",
    "zh": "提问太频繁。请稍后再试。",
    "tt": "Бик еш. Бер минут көтеп, яңадан сорагыз.",
}
FALLBACK = {
    "ru": "ИИ сейчас недоступен — вот результаты поиска.",
    "en": "The assistant is unavailable right now — here are search results.",
    "zh": "助手暂不可用——这是搜索结果。",
    "tt": "ИИ хәзер эшләми — эзләү нәтиҗәләре.",
}

router = Router(name="llm")


async def locale_of(
    message: Message,
    session_factory: async_sessionmaker[AsyncSession] | None,
) -> str:
    user = message.from_user
    code = user.language_code if user else None
    if session_factory is None:
        return resolve_locale(code)
    return await get_locale(session_factory, user.id if user else 0, code)


def _is_group(message: Message) -> bool:
    return str(message.chat.type) in {"group", "supergroup"}


def _question(text: str, bot_username: str | None) -> str:
    value = text.strip()
    if value.lower().startswith("/ask"):
        value = value[4:]
        head, sep, tail = value.partition(" ")
        if not sep:
            value = ""
        else:
            value = tail
    return strip_mention(value, bot_username)


def _card_keyboard(card: CardView | None, bot_username: str | None, locale: str) -> InlineKeyboardMarkup | None:
    if card is None or not bot_username:
        return None
    url = f"https://t.me/{bot_username}?start=card_{card.slug}"
    return InlineKeyboardMarkup(
        inline_keyboard=[[InlineKeyboardButton(text=t(locale, "button.card"), url=url)]]
    )


def _chat_keyboard(locale: str) -> InlineKeyboardMarkup:
    """The chat answer keeps both ways out within one tap."""
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                button(
                    t(locale, "button.menu"),
                    cb_menu(),
                    icon_custom_emoji_id=custom_id("menu"),
                ),
                button(
                    t(locale, "button.chat_exit"),
                    cb_chat_exit(),
                    icon_custom_emoji_id=custom_id("cancel"),
                ),
            ]
        ]
    )


async def _send(message: Message, text: str, *, group: bool, markup: InlineKeyboardMarkup | None = None) -> None:
    if group:
        await message.reply(text, reply_markup=markup)
    else:
        await message.answer(text, reply_markup=markup)


async def _fallback(
    message: Message,
    question: str,
    *,
    bot: Bot,
    repository: ContentRepository | None,
    locale: str,
    group: bool,
) -> None:
    results = repository.search(question, locale, limit=5) if repository is not None else []
    try:
        await show(bot, message.chat.id, search_screen(question, results, locale))
    except Exception:
        logger.exception("failed to show search fallback")
    await _send(message, FALLBACK[locale], group=group)


async def respond(
    message: Message,
    question: str,
    *,
    bot: Bot,
    settings: Settings,
    repository: ContentRepository | None = None,
    bot_username: str | None = None,
    session_factory: async_sessionmaker[AsyncSession] | None = None,
    chat: bool = False,
) -> None:
    locale = await locale_of(message, session_factory)
    group = _is_group(message)
    question = question.strip()
    if not question:
        await _send(message, USAGE[locale], group=group, markup=_chat_keyboard(locale) if chat else None)
        return
    user = message.from_user
    if not LIMITER.allow(
        user_id=user.id if user else 0,
        chat_id=message.chat.id,
        group=group,
    ):
        await _send(message, RATE[locale], group=group, markup=_chat_keyboard(locale) if chat else None)
        return
    cards = context_cards(repository, question, locale)
    client = llm_service.client_for(settings)
    if client is None:
        await _fallback(message, question, bot=bot, repository=repository, locale=locale, group=group)
        return
    messages = build_messages(question, locale, cards)
    try:
        async with client:
            raw = await llm_service.answer(client, messages, settings.llm_model)
    except llm_service.LlmUnavailable:
        await _fallback(message, question, bot=bot, repository=repository, locale=locale, group=group)
        return
    text = strip_html(raw)
    if not text:
        await _fallback(message, question, bot=bot, repository=repository, locale=locale, group=group)
        return
    markup = (
        _chat_keyboard(locale)
        if chat
        else _card_keyboard(cards[0] if cards else None, bot_username or BOT_USERNAME, locale)
    )
    await _send(message, text, group=group, markup=markup)


@router.message(Command("ask"))
async def ask_command(
    message: Message,
    command: CommandObject,
    bot: Bot,
    settings: Settings,
    repo: ContentRepository | None = None,
    session_factory: async_sessionmaker[AsyncSession] | None = None,
) -> None:
    await respond(
        message,
        command.args or "",
        bot=bot,
        settings=settings,
        repository=repo,
        bot_username=BOT_USERNAME,
        session_factory=session_factory,
    )


@router.message(F.chat.type == ChatType.PRIVATE, F.text, ~F.text.startswith("/"))
async def chat_text(
    message: Message,
    bot: Bot,
    settings: Settings,
    repo: ContentRepository | None = None,
    session_factory: async_sessionmaker[AsyncSession] | None = None,
) -> None:
    """Plain text in a private chat is a question only while chat mode is on.

    This router is registered before `app.handlers.search`, so an idle chat
    falls through to the search screen unchanged.
    """
    user = message.from_user
    if user is None or session_factory is None:
        raise SkipHandler()
    if not await chat_mode_on(session_factory, user.id):
        raise SkipHandler()
    await respond(
        message,
        strip_mention(message.text or "", BOT_USERNAME),
        bot=bot,
        settings=settings,
        repository=repo,
        bot_username=BOT_USERNAME,
        session_factory=session_factory,
        chat=True,
    )


@router.message(F.chat.type.in_({"group", "supergroup"}), F.text)
async def group_question(
    message: Message,
    bot: Bot,
    settings: Settings,
    repo: ContentRepository | None = None,
    session_factory: async_sessionmaker[AsyncSession] | None = None,
) -> None:
    username = BOT_USERNAME
    if not is_addressed(message, username, bot.id):
        raise SkipHandler()
    await respond(
        message,
        _question(message.text or "", username),
        bot=bot,
        settings=settings,
        repository=repo,
        bot_username=username,
        session_factory=session_factory,
    )


async def remember_username(bot: Bot) -> None:
    global BOT_USERNAME
    try:
        me = await bot.get_me()
    except Exception:
        logger.warning("could not resolve bot username; mention triggers stay off")
        return
    BOT_USERNAME = me.username


def register(dp: Dispatcher, _bot: Bot) -> None:
    dp.include_router(router)
    dp.startup.register(remember_username)