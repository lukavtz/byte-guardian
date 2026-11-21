from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest
from test_nav import FakeDB

from app.content.types import CardView, ThreatView
from app.handlers import llm as handlers
from app.services import llm as service
from app.services.conversation import ConversationStore, Turn
from app.services.prompt import EMERGENCY_DIRECTIVE, SYSTEM_PROMPT
from app.services.settings import chat_mode_on, set_chat_mode
from app.services.web_search import WebResult


def _card(slug: str = "chem-gas-leak", **overrides) -> CardView:
    fields = {
        "slug": slug,
        "category_slug": "chem",
        "category_title": "Химия",
        "severity": 3,
        "locale": "ru",
        "fallback": False,
        "title": "Утечка газа в квартире",
        "summary": "Запах газа означает риск взрыва.",
        "signs": ("Запах газа",),
        "before": ("Проверяйте плиту",),
        "during": ("Перекройте газ", "Откройте окна"),
        "after": ("Не входите",),
        "forbidden": ("Не зажигайте спички",),
        "contacts": ("104", "112"),
        "source": "МЧС",
        "icon": "🧪",
    }
    fields.update(overrides)
    return CardView(**fields)


class _Repo:
    def __init__(self, cards: list[CardView]) -> None:
        self.cards = {card.slug: card for card in cards}

    def search(self, query: str, locale: str, limit: int = 10) -> list[ThreatView]:
        return [
            ThreatView(
                slug=card.slug,
                category_slug=card.category_slug,
                category_title=card.category_title,
                title=card.title,
                summary=card.summary,
                severity=card.severity,
                locale=card.locale,
                fallback=False,
            )
            for card in list(self.cards.values())[:limit]
        ]

    def card(self, slug: str, locale: str) -> CardView | None:
        return self.cards.get(slug)

    def categories(self):
        return []


def _message(text: str = "", *, chat_type: str = "private", message_id: int = 11):
    message = MagicMock()
    message.text = text
    message.message_id = message_id
    message.chat = SimpleNamespace(id=7, type=chat_type)
    message.from_user = SimpleNamespace(id=42, language_code="ru")
    message.reply_to_message = None
    message.answer = AsyncMock()
    message.reply = AsyncMock()
    return message


def _settings(**overrides) -> SimpleNamespace:
    values = {
        "llm_base_url": "https://llm.test/v1",
        "llm_model": "byte",
        "llm_api_key": "secret-key",
        "llm_stream_enabled": False,
        "chat_memory_enabled": True,
        "chat_memory_group_enabled": False,
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def _client(handler, *, base_url: str = "https://llm.test/v1/") -> httpx.AsyncClient:
    return httpx.AsyncClient(
        transport=httpx.MockTransport(handler),
        base_url=base_url,
        headers={"Authorization": "Bearer secret-key"},
    )


def _ok(text: str = "ответ") -> httpx.AsyncClient:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"choices": [{"message": {"content": text}}]})

    return _client(handler)


@pytest.fixture(autouse=True)
def _fresh_limiter(monkeypatch: pytest.MonkeyPatch) -> None:
    now = [0.0]
    monkeypatch.setattr(handlers, "LIMITER", service.RateLimiter(lambda: now[0]))
    monkeypatch.setattr(handlers, "HISTORY", ConversationStore())
    _RecordingSink.instances.clear()
    _RecordingSink.fail_finish = False


class _RecordingSink:
    """Stand-in for both answer sinks: records the one final delivery."""

    instances: list[_RecordingSink] = []
    fail_finish = False

    def __init__(self, *_args: object, **_kwargs: object) -> None:
        self.pushed: list[str] = []
        self.finished: list[tuple[str, object]] = []
        self.closed = False
        _RecordingSink.instances.append(self)

    async def push(self, text: str) -> None:
        self.pushed.append(text)

    async def finish(self, text: str, markup: object) -> None:
        if _RecordingSink.fail_finish:
            raise RuntimeError("finish refused")
        self.finished.append((text, markup))

    async def close(self) -> None:
        self.closed = True


async def _respond(message, question: str, *, settings=None, repository=None, bot=None):
    await handlers.respond(
        message,
        question,
        bot=bot or SimpleNamespace(id=999, send_chat_action=AsyncMock()),
        settings=settings or _settings(),
        repository=repository if repository is not None else _Repo([_card()]),
        bot_username="bytebot",
    )


# --- prompt wiring --------------------------------------------------------


def test_build_messages_carries_history_skills_and_emergency_rule() -> None:
    history = (
        Turn(role="user", text="привет", at=0.0),
        Turn(role="assistant", text="привет", at=0.0),
    )
    messages = service.build_messages("дым в комнате", "ru", [], history=history)
    system = messages[0]["content"]
    assert "свой человек в ТИСБИ" in system
    assert '<skill name="formatting">' in system
    assert EMERGENCY_DIRECTIVE in system
    assert messages[1]["role"] == "system" and "<history>" in messages[1]["content"]
    assert messages[2]["content"] == "<history>привет</history>"
    assert messages[4]["content"].startswith("Язык ответа: ru")


def test_build_messages_injects_skills_for_smalltalk() -> None:
    messages = service.build_messages("привет", "ru", [])
    assert '<skill name="formatting">' in messages[0]["content"]


def test_no_cards_is_not_stated_to_the_model() -> None:
    messages = service.build_messages("запах газа в квартире", "ru", [])
    assert "Карточек по теме нет" not in messages[-1]["content"]


# --- output guards --------------------------------------------------------


def test_strip_html_and_intro_still_work() -> None:
    assert service.strip_html("**112**") == "<b>112</b>"
    assert service.strip_intro("Привет! Я Байт — помощник. Закрой газ.") == "Закрой газ."


def test_strip_answer_urls_and_draft_cut() -> None:
    assert "http" not in service.strip_answer_urls("Шаг.\nИсточник: https://evil.test/x")
    rendered = service.draft_html("<b>Закрой газ и открой</b", complete=False)
    assert rendered.startswith("<b>Закрой газ")


def test_paragraph_break_survives_the_sanitizer() -> None:
    assert service.strip_html("Один.\n\nДва.") == "Один.\n\nДва."
    assert service.strip_html("Шаг.<br>Ещё шаг.") == "Шаг.\nЕщё шаг."
    assert "<br" not in service.strip_html("Шаг.<br/>Ещё\nшаг.")


def test_rich_html_keeps_breaks_for_rich_messages() -> None:
    assert service.rich_html("Один.\n\nДва.") == "Один.<br/><br/>Два."


def test_answer_cap_is_eighteen_hundred_chars() -> None:
    rendered = service.strip_html("я" * 2500)
    assert rendered.endswith("…")
    assert len(rendered) == service.TEXT_LIMIT + 1


def test_system_prompt_asks_for_paragraphs_and_plain_steps() -> None:
    assert "2–5 коротких абзацев" in SYSTEM_PROMPT
    assert "пустая строка" in SYSTEM_PROMPT
    assert "1. 2. 3." in SYSTEM_PROMPT
    assert "•" in SYSTEM_PROMPT
    assert "markdown-таблиц" in SYSTEM_PROMPT
    assert "без вступления" in SYSTEM_PROMPT


# --- handler: model path --------------------------------------------------


async def test_greeting_goes_to_the_model(monkeypatch: pytest.MonkeyPatch) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        assert "привет" in body["messages"][-1]["content"].lower()
        return httpx.Response(200, json={"choices": [{"message": {"content": "Привет! Я на связи."}}]})

    monkeypatch.setattr(service, "client_for", lambda settings: _client(handler))
    message = _message("Привет!")
    await _respond(message, "Привет!")

    message.answer.assert_awaited_once()
    assert message.answer.await_args.args[0] == "Привет! Я на связи."


async def test_second_greeting_is_rate_limited(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(service, "client_for", lambda settings: _ok())
    message = _message("Привет!")
    for _ in range(6):
        await _respond(message, "Привет!")
    assert "Слишком часто" in message.answer.await_args.args[0]


async def test_vague_request_reaches_the_model() -> None:
    message = _message("Что-то странное")
    await _respond(message, "Что-то странное")
    assert "Уточни" not in message.answer.await_args.args[0]


async def test_topical_question_uses_cards(monkeypatch: pytest.MonkeyPatch) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        assert "Перекройте газ" in body["messages"][-1]["content"]
        return httpx.Response(200, json={"choices": [{"message": {"content": "Перекрой газ."}}]})

    monkeypatch.setattr(service, "client_for", lambda settings: _client(handler))
    message = _message("что делать при запахе газа?")
    await _respond(message, "что делать при запахе газа?")
    assert message.answer.await_args.args[0] == "Перекрой газ."


async def test_answer_has_no_raw_urls_or_emoji(monkeypatch: pytest.MonkeyPatch) -> None:
    text = "Шаг один 😀\nИсточник: https://evil.test/x"
    monkeypatch.setattr(service, "client_for", lambda settings: _ok(text))
    message = _message("что делать при запахе газа?")
    await _respond(message, "что делать при запахе газа?")
    assert "http" not in message.answer.await_args.args[0]
    assert "Источник" not in message.answer.await_args.args[0]


async def test_history_is_appended_after_success(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(service, "client_for", lambda settings: _ok("ответ"))
    message = _message("что делать при запахе газа?")
    await _respond(message, "что делать при запахе газа?")
    turns = handlers.HISTORY.recent(chat_id=7, user_id=42, group=False)
    assert [turn.role for turn in turns] == ["user", "assistant"]


async def test_history_is_not_appended_on_fallback(monkeypatch: pytest.MonkeyPatch) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, json={})

    monkeypatch.setattr(service, "client_for", lambda settings: _client(handler))
    monkeypatch.setattr(handlers, "show", AsyncMock())
    message = _message("что делать при запахе газа?")
    await _respond(message, "что делать при запахе газа?")
    assert handlers.HISTORY.recent(chat_id=7, user_id=42, group=False) == ()


async def test_missing_key_falls_back(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(service, "client_for", lambda settings: None)
    monkeypatch.setattr(handlers, "show", AsyncMock())
    message = _message("что делать при запахе газа?")
    await _respond(message, "что делать при запахе газа?", settings=_settings(llm_api_key=""))
    assert "поиска" in message.answer.await_args.args[0]


async def test_private_text_without_chat_mode_goes_to_the_model(monkeypatch: pytest.MonkeyPatch) -> None:
    db = FakeDB()
    monkeypatch.setattr(service, "client_for", lambda settings: _ok("Привет! Я на связи."))
    message = _message("Привет!")

    await handlers.chat_text(
        message,
        bot=SimpleNamespace(id=999, send_chat_action=AsyncMock()),
        settings=_settings(),
        repo=_Repo([_card()]),
        session_factory=db.factory(),  # type: ignore[arg-type]
    )

    message.answer.assert_awaited_once()
    assert message.answer.await_args.args[0] == "Привет! Я на связи."
    assert await chat_mode_on(db.factory(), 42) is True


async def test_paragraphs_survive_the_delivery(monkeypatch: pytest.MonkeyPatch) -> None:
    body = "Один. Не паникуй.\n\nДва. Звони 112."
    monkeypatch.setattr(service, "client_for", lambda settings: _ok(body))
    message = _message("что делать при запахе газа?")

    await _respond(message, "что делать при запахе газа?")

    sent = message.answer.await_args.args[0]
    assert "Не паникуй.\n\nДва." in sent
    assert "<br" not in sent


async def test_send_gets_html_the_bot_api_accepts(monkeypatch: pytest.MonkeyPatch) -> None:
    # `sendMessage` HTML has no `<br/>`; a literal newline is the only break.
    body = "<b>Шаг.</b>\n\n• Перекройте газ"
    monkeypatch.setattr(service, "client_for", lambda settings: _ok(body))
    sent: list[str] = []

    async def fake_send(message, text, *, group, markup=None):
        sent.append(text)

    monkeypatch.setattr(handlers, "_send", fake_send)
    message = _message("что делать при запахе газа?")
    await _respond(message, "что делать при запахе газа?")

    (text,) = sent
    assert text == "<b>Шаг.</b>\n\n• Перекройте газ"
    assert "<br" not in text


async def test_chat_mode_replies_with_one_styled_exit_button(monkeypatch: pytest.MonkeyPatch) -> None:
    db = FakeDB()
    await set_chat_mode(db.factory(), 42, True)
    monkeypatch.setattr(service, "client_for", lambda settings: _ok())
    message = _message("что делать при запахе газа?")
    await handlers.chat_text(
        message,
        bot=SimpleNamespace(id=999, send_chat_action=AsyncMock()),
        settings=_settings(),
        repo=_Repo([_card()]),
        session_factory=db.factory(),  # type: ignore[arg-type]
    )
    markup = message.answer.await_args.kwargs["reply_markup"]
    buttons = [item for row in markup.inline_keyboard for item in row]
    assert len(buttons) == 1
    assert buttons[0].callback_data == "b1:chat_exit"
    assert buttons[0].style == "danger"
    assert buttons[0].icon_custom_emoji_id is not None


async def test_group_reply_still_replies(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(service, "client_for", lambda settings: _ok())
    message = _message("вопрос", chat_type="group")
    await _respond(message, "что делать при запахе газа?", settings=_settings(chat_memory_group_enabled=False))
    message.reply.assert_awaited_once()
    message.answer.assert_not_awaited()


async def test_question_never_logged(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level("INFO")
    monkeypatch.setattr(service, "client_for", lambda settings: _ok())
    message = _message("секретный вопрос про газ")
    await _respond(message, "секретный вопрос про газ")
    assert "секретный" not in caplog.text


# --- sources: https and official hosts only -------------------------------


def test_source_buttons_accept_only_official_https() -> None:
    markup = handlers._with_sources(
        None,
        [
            ("evil.test", "https://evil.test/x"),
            ("mchs.gov.ru", "http://mchs.gov.ru/x"),
            ("mchs.gov.ru.evil.test", "https://mchs.gov.ru.evil.test/x"),
            ("mchs.gov.ru", "https://mchs.gov.ru/news/1"),
        ],
    )
    assert markup is not None
    buttons = [item for row in markup.inline_keyboard for item in row]
    assert [(item.text, item.url) for item in buttons] == [
        ("mchs.gov.ru", "https://mchs.gov.ru/news/1")
    ]


def test_untrusted_citations_never_become_buttons() -> None:
    results = [
        WebResult(title="evil", url="https://evil.test/a", snippet=""),
        WebResult(title="plain http", url="http://mchs.gov.ru/b", snippet=""),
        WebResult(title="official", url="https://mchs.gov.ru/c", snippet=""),
    ]
    answer = (
        "Источник: https://evil.test/a\n"
        "Источник: http://mchs.gov.ru/b\n"
        "Источник: https://mchs.gov.ru/c"
    )
    assert handlers._cited_sources(answer, results) == [
        ("mchs.gov.ru", "https://mchs.gov.ru/c")
    ]


# --- stream robustness: one final message, one cleanup path ---------------


def _no_web(monkeypatch: pytest.MonkeyPatch) -> None:
    async def empty(*_args: object, **_kwargs: object) -> list[WebResult]:
        return []

    monkeypatch.setattr(handlers, "search_web", empty)


def _stream_settings(**overrides: object) -> SimpleNamespace:
    return _settings(llm_stream_enabled=True, **overrides)


async def test_first_token_timeout_still_delivers_one_answer(monkeypatch: pytest.MonkeyPatch) -> None:
    async def hang():
        await asyncio.sleep(60)
        yield "никогда"

    _no_web(monkeypatch)
    monkeypatch.setattr(handlers, "FIRST_TOKEN_TIMEOUT", 0.01)
    monkeypatch.setattr(service, "stream_answer", lambda *_a, **_k: hang())
    monkeypatch.setattr(service, "client_for", lambda settings: _ok("ответ"))
    monkeypatch.setattr(handlers, "DraftSink", _RecordingSink)
    message = _message()

    await _respond(message, "вопрос", settings=_stream_settings())

    sink = _RecordingSink.instances[0]
    assert [text for text, _markup in sink.finished] == ["ответ"]
    assert sink.closed
    message.answer.assert_not_awaited()
    message.reply.assert_not_awaited()


async def test_empty_stream_falls_back_to_one_full_answer(monkeypatch: pytest.MonkeyPatch) -> None:
    async def empty():
        if False:
            yield ""

    _no_web(monkeypatch)
    monkeypatch.setattr(service, "stream_answer", lambda *_a, **_k: empty())
    monkeypatch.setattr(service, "client_for", lambda settings: _ok("ответ"))
    monkeypatch.setattr(handlers, "DraftSink", _RecordingSink)
    message = _message()

    await _respond(message, "вопрос", settings=_stream_settings())

    sink = _RecordingSink.instances[0]
    assert [text for text, _markup in sink.finished] == ["ответ"]
    assert sink.pushed == []
    message.answer.assert_not_awaited()


async def test_interrupted_stream_reuses_the_same_message(monkeypatch: pytest.MonkeyPatch) -> None:
    async def cut():
        yield "нача"
        raise service.LlmUnavailable("cut")

    _no_web(monkeypatch)
    monkeypatch.setattr(service, "stream_answer", lambda *_a, **_k: cut())
    monkeypatch.setattr(service, "client_for", lambda settings: _ok("ответ"))
    monkeypatch.setattr(handlers, "DraftSink", _RecordingSink)
    message = _message()

    await _respond(message, "вопрос", settings=_stream_settings())

    sink = _RecordingSink.instances[0]
    assert sink.pushed == ["нача"]
    assert [text for text, _markup in sink.finished] == ["ответ"]
    message.answer.assert_not_awaited()


async def test_finish_error_never_sends_a_second_answer(monkeypatch: pytest.MonkeyPatch) -> None:
    async def stream():
        yield "Готово"

    _no_web(monkeypatch)
    monkeypatch.setattr(service, "stream_answer", lambda *_a, **_k: stream())
    monkeypatch.setattr(handlers, "DraftSink", _RecordingSink)
    _RecordingSink.fail_finish = True
    message = _message()

    await _respond(message, "вопрос", settings=_stream_settings())

    assert _RecordingSink.instances[0].closed
    message.answer.assert_not_awaited()
    message.reply.assert_not_awaited()


async def test_stream_success_finishes_with_the_full_text(monkeypatch: pytest.MonkeyPatch) -> None:
    async def stream():
        yield "Пол"
        yield "ный"

    _no_web(monkeypatch)
    monkeypatch.setattr(service, "stream_answer", lambda *_a, **_k: stream())
    monkeypatch.setattr(handlers, "DraftSink", _RecordingSink)
    message = _message()

    await _respond(message, "вопрос", settings=_stream_settings())

    sink = _RecordingSink.instances[0]
    assert sink.pushed == ["Пол", "Полный"]
    assert [text for text, _markup in sink.finished] == ["Полный"]
    message.answer.assert_not_awaited()