from __future__ import annotations

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
from app.services.prompt import EMERGENCY_DIRECTIVE
from app.services.settings import set_chat_mode


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