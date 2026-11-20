"""LLM answers over the content cards, plus the small guards around them."""

from __future__ import annotations

import asyncio
import logging
import re
import time
from collections.abc import Callable
from html import escape
from html.parser import HTMLParser
from typing import Any

import httpx
from aiogram.types import Message

from app.config import Settings
from app.content.repository import ContentRepository
from app.content.types import CardView
from app.runtime.secrets import read_secret
from app.services.prompt import SYSTEM_PROMPT
from app.skills.loader import format_skills

logger = logging.getLogger(__name__)

TIMEOUT_SECONDS = 20.0
RETRY_DELAY = 0.5
RETRY_STATUS = frozenset({429, 500, 502, 503, 504})
CARD_LIMIT = 3
FIELD_LIMIT = 300
TEXT_LIMIT = 1200
ALLOWED_TAGS = frozenset({"b", "i", "u", "s", "code", "br"})
VOID_TAGS = frozenset({"br"})

USER_LIMIT = 5
USER_WINDOW = 60.0
CHAT_LIMIT = 20
CHAT_WINDOW = 60.0
GROUP_LIMIT = 1
GROUP_WINDOW = 10.0
MAX_BUCKETS = 10_000

TEMPERATURE = 0.3
MAX_TOKENS = 700
TOPICAL_MIN_TOKENS = 2
SMALLTALK_MAX_WORDS = 6
INSULT_MAX_WORDS = 8

_ASK_RE = re.compile(r"^/ask(?:@[A-Za-z0-9_]+)?(?:\s|$)", re.IGNORECASE)
_WORD_RE = re.compile(r"\w+", re.UNICODE)

# Filler that says nothing about the topic: pleasantries, politeness, small talk.
_VAGUE_STOP = frozenset(
    {
        "что", "делать", "если", "при", "как", "мне", "нужно", "можно", "бот",
        "это", "там", "тут", "вот", "тебе", "тебя", "меня", "есть", "быть",
        "чувак", "брат", "братан", "бро", "побратски", "пожалуйста", "плиз",
        "срочно", "помоги", "помогите", "подскажи", "подскажите", "слушай",
        "please", "help", "the", "and", "for", "you", "what", "how", "can",
        "нужна", "нужен", "надо", "хочу", "хотел", "можешь", "может",
    }
)

_GREETING_RE = re.compile(
    r"\b(?:привет\w*|здравствуй\w*|здорово|хай|ку|салам\w*|hello|hi|hey|yo)\b",
    re.IGNORECASE,
)
_THANKS_RE = re.compile(
    r"\b(?:спасиб\w*|благодар\w*|спс|рахмат|thanks|thank\s+you|thx)\b",
    re.IGNORECASE,
)
_INSULT_RE = re.compile(
    r"\b(?:дурак\w*|туп\w*|идиот\w*|дебил\w*|кретин\w*|придурок\w*|мудак\w*|"
    r"stupid|idiot|moron|dumb)\b",
    re.IGNORECASE,
)
_SMALLTALK_RE = re.compile(
    r"\b(?:как\s+(?:дела|ты|сам|оно)|что\s+делаешь|чем\s+занимаешься|кто\s+ты|"
    r"ты\s+(?:бот|человек|живой)|как\s+тебя\s+зовут|how\s+are\s+you|who\s+are\s+you)\b",
    re.IGNORECASE,
)
_HELP_RE = re.compile(
    r"^\s*(?:эй|слушай|ну|чувак|брат|братан|бро)?[\s,]*"
    r"(?:помоги(?:те)?|подскажи(?:те)?|help)"
    r"(?:\s+(?:побратски|пожалуйста|плиз|срочно|брат|братан|бро|чувак))?[\s!.?]*$",
    re.IGNORECASE,
)


def words_of(text: str) -> list[str]:
    return _WORD_RE.findall(text.casefold())


def content_tokens(text: str) -> list[str]:
    """Words that carry the topic: no politeness, no question scaffolding."""
    return [
        word
        for word in words_of(text)
        if len(word) >= 3 and word not in _VAGUE_STOP
    ]


def detect_smalltalk(text: str) -> str | None:
    """``greeting``/``thanks``/``smalltalk``/``insult`` for chit-chat, else ``None``.

    A short message that matches one of these is answered without a model:
    searching the corpus for «привет» only ever yields an empty card list.
    """
    value = text.strip()
    words = words_of(value)
    if not words:
        return None
    if len(words) <= INSULT_MAX_WORDS and _INSULT_RE.search(value):
        return "insult"
    if len(words) <= SMALLTALK_MAX_WORDS and _THANKS_RE.search(value):
        return "thanks"
    if _HELP_RE.match(value):
        return "greeting"
    if len(words) <= SMALLTALK_MAX_WORDS and _SMALLTALK_RE.search(value):
        return "smalltalk"
    if len(words) <= SMALLTALK_MAX_WORDS and _GREETING_RE.search(value):
        return "greeting"
    return None


def is_vague(question: str, cards: list[CardView], *, indexed: bool = True) -> bool:
    """A message too short or too empty to search: ask one question instead."""
    if len(words_of(question)) < 3:
        return True
    return indexed and not cards and len(content_tokens(question)) < TOPICAL_MIN_TOKENS


_INTRO_GREETING_RE = re.compile(
    r"^\s*(?:привет|здравствуйте|здорово|хай|hello|hi)[!,.…\s]+", re.IGNORECASE
)
_INTRO_PERSONA_RE = re.compile(
    r"^\s*(?:я\s*[—–-]?\s*байт|я\s+помощник\s+по)\b[^.!?\n]*[.!?]\s*",
    re.IGNORECASE,
)


def strip_intro(text: str) -> str:
    """Drop the «Привет! Я Байт…» opening a model repeats on later answers."""
    value = text.strip()
    trimmed = _INTRO_GREETING_RE.sub("", value, count=1)
    if trimmed:
        value = trimmed
    trimmed = _INTRO_PERSONA_RE.sub("", value, count=1)
    return trimmed if trimmed else value


# Models often answer in Markdown even when asked for HTML. Convert the two
# emphasis forms, and leave anything inside code alone.
_CODE_SPAN_RE = re.compile(
    r"```.*?```|`[^`\n]*`|<code>.*?</code>", re.DOTALL | re.IGNORECASE
)
_BOLD_RE = re.compile(r"\*\*(?=\S)(.+?\S)\*\*")
_UNDER_BOLD_RE = re.compile(r"(?<![\w])__(?=\S)(.+?\S)__(?![\w])")
_ITALIC_RE = re.compile(r"(?<![\w*])\*(?=[^\s*])([^*\n]+?)(?<=[^\s*])\*(?![\w*])")
_IDENTIFIER_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]*\Z")


def _emphasis(chunk: str) -> str:
    def wrap(match: re.Match[str], tag: str) -> str:
        inner = match.group(1)
        # ``__init__`` is an identifier, not emphasis.
        return match.group(0) if _IDENTIFIER_RE.match(inner) else f"<{tag}>{inner}</{tag}>"

    chunk = _BOLD_RE.sub(r"<b>\1</b>", chunk)
    chunk = _UNDER_BOLD_RE.sub(lambda m: wrap(m, "b"), chunk)
    return _ITALIC_RE.sub(lambda m: wrap(m, "i"), chunk)


def markdown_to_html(text: str) -> str:
    """``**x**``/``__x__`` become bold and ``*x*`` italic, outside code spans."""
    out: list[str] = []
    position = 0
    for span in _CODE_SPAN_RE.finditer(text):
        out.append(_emphasis(text[position : span.start()]))
        out.append(span.group(0))
        position = span.end()
    out.append(_emphasis(text[position:]))
    return "".join(out)


class LlmUnavailable(RuntimeError):
    """The model could not produce a usable answer; callers fall back to search."""


def client_for(settings: Settings) -> httpx.AsyncClient | None:
    base = (settings.llm_base_url or "").rstrip("/")
    key = settings.llm_api_key
    if not key:
        try:
            key = read_secret("LLM_API_KEY") or ""
        except RuntimeError:
            key = ""
    if not base or not settings.llm_model or not key:
        return None
    return httpx.AsyncClient(
        base_url=base + "/",
        timeout=httpx.Timeout(TIMEOUT_SECONDS),
        headers={"Authorization": "Bearer " + key},
    )


def _clip(value: object, limit: int = FIELD_LIMIT) -> str:
    text = " ".join(str(value).split())
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


def _card_block(number: int, card: CardView) -> str:
    during = _clip("; ".join(card.during))
    forbidden = _clip("; ".join(card.forbidden))
    lines = [
        f"{number}. {_clip(card.title)}",
        f"Кратко: {_clip(card.summary)}",
        f"Во время: {during}",
        f"Нельзя: {forbidden}",
    ]
    if card.contacts:
        lines.append(f"Телефоны: {_clip('; '.join(card.contacts))}")
    return "\n".join(lines)


def build_messages(
    question: str, locale: str, cards: list[CardView], web: str | None = None
) -> list[dict[str, Any]]:
    blocks = [_card_block(number, card) for number, card in enumerate(cards[:CARD_LIMIT], start=1)]
    user = f"Язык ответа: {locale}\nВопрос: {question.strip()}"
    # «Карточек нет» belongs only to a real topical question: a greeting that
    # misses the corpus must not be answered with a catalogue apology.
    if blocks:
        user += "\n\nКарточки:\n" + "\n\n".join(blocks)
    elif len(content_tokens(question)) >= TOPICAL_MIN_TOKENS:
        user += "\n\nКарточки:\nКарточек по теме нет."
    if web:
        user += f"\n\n{web}"
    # Skills ride on the system prompt for topical questions only: greeting and
    # small-talk intents are answered by ChatBrain and never reach the model.
    # `format_skills` caps the block at BUDGET_CHARS, so growth stays ≤ 900 chars.
    system = SYSTEM_PROMPT
    if detect_smalltalk(question) is None:
        system += format_skills(question, locale)
    return [
        {"role": "system", "content": system},
        {"role": "user", "content": user},
    ]


def context_cards(
    repository: ContentRepository | None, question: str, locale: str
) -> list[CardView]:
    if repository is None:
        return []
    cards: list[CardView] = []
    for view in repository.search(question, locale, limit=CARD_LIMIT):
        card = repository.card(view.slug, locale)
        if card is not None:
            cards.append(card)
        if len(cards) == CARD_LIMIT:
            break
    return cards


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


def _token_count(payload: Any) -> int | None:
    if not isinstance(payload, dict):
        return None
    usage = payload.get("usage")
    if not isinstance(usage, dict):
        return None
    total = usage.get("total_tokens")
    return total if isinstance(total, int) and not isinstance(total, bool) else None


async def answer(client: httpx.AsyncClient, messages: list[dict[str, Any]], model: str) -> str:
    """POST one chat completion. One retry on 429/5xx, never on a timeout.

    The prompt and the answer stay out of the logs on purpose: only status,
    latency and token counts are reported.
    """
    body = {
        "model": model,
        "messages": messages,
        "temperature": TEMPERATURE,
        "max_tokens": MAX_TOKENS,
    }
    for attempt in (0, 1):
        started = time.perf_counter()
        try:
            response = await client.post("chat/completions", json=body)
        except httpx.TimeoutException as error:
            logger.warning("llm timeout after %d ms", int((time.perf_counter() - started) * 1000))
            raise LlmUnavailable("timeout") from error
        except httpx.HTTPError as error:
            logger.warning("llm transport error after %d ms", int((time.perf_counter() - started) * 1000))
            raise LlmUnavailable("transport") from error
        latency_ms = int((time.perf_counter() - started) * 1000)
        payload: Any
        try:
            payload = response.json()
        except ValueError:
            payload = None
        logger.info(
            "llm status=%s latency_ms=%d tokens=%s",
            response.status_code,
            latency_ms,
            _token_count(payload),
        )
        if response.status_code in RETRY_STATUS and attempt == 0:
            await asyncio.sleep(RETRY_DELAY)
            continue
        if response.status_code != 200:
            raise LlmUnavailable(f"status {response.status_code}")
        text = _read_content(payload)
        if text is None:
            raise LlmUnavailable("empty answer")
        return text
    raise AssertionError("unreachable")


def is_addressed(message: Message, bot_username: str | None, bot_id: int) -> bool:
    """True when the text is aimed at the bot: /ask, @mention, or a reply to it."""
    text = message.text or ""
    ask = _ASK_RE.match(text)
    if ask:
        target = re.match(r"^/ask@([A-Za-z0-9_]+)", text, re.IGNORECASE)
        if target and bot_username and target.group(1).lower() != bot_username.lower():
            return False
        return True
    if text.startswith("/"):
        return False
    if bot_username:
        mention = re.compile(r"(?<!\w)@" + re.escape(bot_username) + r"(?!\w)", re.IGNORECASE)
        if mention.search(text):
            return True
    reply = message.reply_to_message
    sender = getattr(reply, "from_user", None) if reply is not None else None
    return sender is not None and sender.id == bot_id


def strip_mention(text: str, bot_username: str | None) -> str:
    if not bot_username:
        return text.strip()
    mention = re.compile(r"(?<!\w)@" + re.escape(bot_username) + r"(?!\w)", re.IGNORECASE)
    return mention.sub(" ", text).strip()


class _Cleaner(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self.open: list[str] = []
        self.visible = 0
        self.done = False
        self.cut = False

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if self.done:
            return
        if tag in VOID_TAGS:
            if self.visible < TEXT_LIMIT:
                self.parts.append("\n")
                self.visible += 1
        elif tag in ALLOWED_TAGS:
            self.parts.append(f"<{tag}>")
            self.open.append(tag)

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self.handle_starttag(tag, attrs)

    def handle_endtag(self, tag: str) -> None:
        if self.done or tag not in self.open:
            return
        while self.open:
            current = self.open.pop()
            self.parts.append(f"</{current}>")
            if current == tag:
                return

    def _ellipsis(self) -> None:
        # Only an answer the model actually overran carries the mark; a cut
        # that lands exactly on the cap is still the whole answer.
        if not self.cut:
            self.cut = True
            self.parts.append("…")

    def handle_data(self, data: str) -> None:
        if self.done or not data:
            return
        room = TEXT_LIMIT - self.visible
        if room <= 0:
            self._ellipsis()
            self.done = True
            return
        chunk = data[:room]
        self.parts.append(escape(chunk, quote=False))
        self.visible += len(chunk)
        if len(data) > room:
            self._ellipsis()
            self.done = True

    def close(self) -> None:
        super().close()
        while self.open:
            self.parts.append(f"</{self.open.pop()}>")


def strip_html(text: str) -> str:
    """Keep b/i/u/s/code, turn br into a newline, escape the rest, cap 1200 chars."""
    cleaner = _Cleaner()
    cleaner.feed(markdown_to_html(text or ""))
    cleaner.close()
    return "".join(cleaner.parts).strip()


class _Bucket:
    __slots__ = ("capacity", "window", "tokens", "updated")

    def __init__(self, capacity: int, window: float, now: float) -> None:
        self.capacity = float(capacity)
        self.window = window
        self.tokens = float(capacity)
        self.updated = now

    def _refill(self, now: float) -> None:
        self.tokens = min(self.capacity, self.tokens + (now - self.updated) * self.capacity / self.window)
        self.updated = now

    def available(self, now: float) -> bool:
        self._refill(now)
        return self.tokens >= 1.0

    def take(self, now: float) -> None:
        self._refill(now)
        self.tokens -= 1.0


class RateLimiter:
    """In-memory token buckets: 5/min user, 20/min chat, 1/10 s in a group."""

    def __init__(self, clock: Callable[[], float] = time.monotonic) -> None:
        self._clock = clock
        self._users: dict[int, _Bucket] = {}
        self._chats: dict[int, _Bucket] = {}
        self._groups: dict[int, _Bucket] = {}

    def allow(self, *, user_id: int, chat_id: int, group: bool) -> bool:
        now = self._clock()
        buckets = [
            self._users.setdefault(user_id, _Bucket(USER_LIMIT, USER_WINDOW, now)),
            self._chats.setdefault(chat_id, _Bucket(CHAT_LIMIT, CHAT_WINDOW, now)),
        ]
        if group:
            buckets.append(self._groups.setdefault(chat_id, _Bucket(GROUP_LIMIT, GROUP_WINDOW, now)))
        if not all(bucket.available(now) for bucket in buckets):
            return False
        for bucket in buckets:
            bucket.take(now)
        if len(self._users) > MAX_BUCKETS:
            self._prune()
        return True

    def _prune(self) -> None:
        # ponytail: drop only fully refilled buckets; a hot chat keeps its state.
        self._users = {key: bucket for key, bucket in self._users.items() if bucket.tokens < bucket.capacity}
        self._chats = {key: bucket for key, bucket in self._chats.items() if bucket.tokens < bucket.capacity}
        self._groups = {key: bucket for key, bucket in self._groups.items() if bucket.tokens < bucket.capacity}