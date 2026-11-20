from __future__ import annotations

import asyncio
import json
import re
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest
from test_nav import FakeDB

from app.content.types import CardView, ThreatView
from app.handlers import llm as handlers
from app.services import llm as service
from app.services.prompt import SYSTEM_PROMPT
from app.services.settings import chat_mode_on, set_chat_mode


def _card(slug: str = "fire-home", **overrides) -> CardView:
    fields = {
        "slug": slug,
        "category_slug": "fire",
        "category_title": "Пожар",
        "severity": 3,
        "locale": "ru",
        "fallback": False,
        "title": "Пожар в квартире",
        "summary": "При пожаре звоните 101 и выводите людей.",
        "signs": ("Дым",),
        "before": ("Датчик",),
        "during": ("Звоните 101", "Закройте дверь"),
        "after": ("Не входите",),
        "forbidden": ("Не открывайте окна",),
        "contacts": ("101", "112"),
        "source": "МЧС России",
        "icon": "🔥",
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


def _message(text: str = "", *, chat_type: str = "private", reply_to=None):
    message = MagicMock()
    message.text = text
    message.chat = SimpleNamespace(id=7, type=chat_type)
    message.from_user = SimpleNamespace(id=42, language_code="ru")
    message.reply_to_message = reply_to
    message.answer = AsyncMock()
    message.reply = AsyncMock()
    return message


def _settings(**overrides) -> SimpleNamespace:
    values = {
        "llm_base_url": "https://llm.test/v1",
        "llm_model": "byte",
        "llm_api_key": "secret-key",
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def _client(handler, *, base_url: str = "https://llm.test/v1/") -> httpx.AsyncClient:
    return httpx.AsyncClient(
        transport=httpx.MockTransport(handler),
        base_url=base_url,
        headers={"Authorization": "Bearer secret-key"},
    )


@pytest.fixture(autouse=True)
def _fresh_limiter(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(handlers, "LIMITER", service.RateLimiter())


# --- build_messages -------------------------------------------------------


def test_build_messages_carries_persona_locale_and_three_cards() -> None:
    cards = [_card(f"fire-{index}", title=f"Пожар {index}") for index in range(4)]
    messages = service.build_messages("что делать?", "en", cards)

    assert [item["role"] for item in messages] == ["system", "user"]
    assert messages[0]["content"] == SYSTEM_PROMPT
    assert "Звоните 112" in SYSTEM_PROMPT
    assert "максимум 700 знаков" in SYSTEM_PROMPT
    user = messages[1]["content"]
    assert "Язык ответа: en" in user
    assert "что делать?" in user
    assert "Пожар 0" in user and "Пожар 2" in user
    assert "Пожар 3" not in user
    assert "Звоните 101" in user
    assert "Не открывайте окна" in user
    assert "Телефоны: 101; 112" in user


def test_build_messages_clips_each_field() -> None:
    long = "а" * 900
    messages = service.build_messages("пожар", "ru", [_card(summary=long, during=(long,), forbidden=(long,))])
    user = messages[1]["content"]
    for line in user.splitlines():
        if line.startswith(("Кратко:", "Во время:", "Нельзя:")):
            assert len(line) <= service.FIELD_LIMIT + len("Во время: ")


def test_build_messages_without_cards() -> None:
    messages = service.build_messages("пожар", "ru", [])
    assert "Карточек по теме нет" in messages[1]["content"]


# --- strip_html -----------------------------------------------------------


def test_strip_html_keeps_allowed_tags_and_drops_attributes() -> None:
    raw = '<b onclick="bad()">ok</b> <i>i</i> <a href="http://e">link</a><br/>'
    assert service.strip_html(raw) == "<b>ok</b> <i>i</i> link"


def test_strip_html_turns_br_into_newline() -> None:
    assert service.strip_html("a<br>b<br/>c") == "a\nb\nc"
    assert "<br" not in service.strip_html("a<br/>b")


def test_strip_html_escapes_plain_text_and_removes_script() -> None:
    cleaned = service.strip_html('<script>alert(1)</script> 1 < 2 & 3')
    assert "<script" not in cleaned
    assert "&lt;" in cleaned and "&amp;" in cleaned


def test_strip_html_caps_at_1200_visible_chars_with_an_ellipsis() -> None:
    assert service.TEXT_LIMIT == 1200
    cleaned = service.strip_html("<b>" + "а" * 1400 + "</b>")
    visible = re.sub(r"<[^>]+>", "", cleaned)
    assert visible == "а" * service.TEXT_LIMIT + "…"
    assert cleaned.startswith("<b>") and cleaned.endswith("…</b>")


def test_strip_html_leaves_an_answer_inside_the_cap_untouched() -> None:
    exact = "а" * service.TEXT_LIMIT
    assert service.strip_html(exact) == exact
    assert "…" not in service.strip_html(exact)


def test_strip_html_marks_an_overflow_that_spans_nodes() -> None:
    cleaned = service.strip_html("<b>" + "а" * service.TEXT_LIMIT + "</b> хвост")
    assert cleaned == "<b>" + "а" * service.TEXT_LIMIT + "</b>…"


def test_strip_html_rejects_unknown_tags_but_keeps_text() -> None:
    assert service.strip_html("<div><s>gone</s></div>") == "<s>gone</s>"


def test_strip_html_turns_bold_markdown_into_bold() -> None:
    assert service.strip_html("**112**") == "<b>112</b>"
    assert service.strip_html("Звоните **112** сразу") == "Звоните <b>112</b> сразу"
    assert service.strip_html("__важно__ тут") == "<b>важно</b> тут"


def test_strip_html_turns_single_asterisks_into_italic() -> None:
    assert service.strip_html("*важно* знать") == "<i>важно</i> знать"


def test_strip_html_leaves_identifiers_and_arithmetic_alone() -> None:
    text = "obj.__init__() и snake_case_name, 2*3*4"
    assert service.strip_html(text) == text


def test_strip_html_leaves_unmatched_markers_literal() -> None:
    assert service.strip_html("**непарный") == "**непарный"
    assert service.strip_html("непарный *тут") == "непарный *тут"


def test_strip_html_keeps_markdown_inside_code_untouched() -> None:
    assert service.strip_html("`a**b**c`") == "`a**b**c`"
    assert service.strip_html("<code>**x**</code> и **да**") == "<code>**x**</code> и <b>да</b>"


# --- is_addressed ---------------------------------------------------------


def test_is_addressed_for_ask_mention_and_reply() -> None:
    assert service.is_addressed(_message("/ask что делать?"), "bytebot", 999) is True
    assert service.is_addressed(_message("@ByteBot что делать?"), "bytebot", 999) is True
    reply = SimpleNamespace(from_user=SimpleNamespace(id=999))
    assert service.is_addressed(_message("а если дым?", reply_to=reply), "bytebot", 999) is True


def test_is_addressed_for_ordinary_text_and_other_commands() -> None:
    assert service.is_addressed(_message("просто текст"), "bytebot", 999) is False
    assert service.is_addressed(_message("/help"), "bytebot", 999) is False
    reply = SimpleNamespace(from_user=SimpleNamespace(id=1))
    assert service.is_addressed(_message("ответ человеку", reply_to=reply), "bytebot", 999) is False


# --- answer ---------------------------------------------------------------


async def test_answer_returns_content(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["auth"] = request.headers["authorization"]
        seen["body"] = json.loads(request.content)
        return httpx.Response(200, json={"choices": [{"message": {"content": "Ответ"}}], "usage": {"total_tokens": 7}})

    async with _client(handler) as client:
        result = await service.answer(client, [{"role": "user", "content": "вопрос"}], "byte")
    assert result == "Ответ"
    assert seen["auth"] == "Bearer secret-key"
    assert seen["body"]["model"] == "byte"


async def test_answer_retries_once_on_5xx(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(service, "RETRY_DELAY", 0)
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        if calls["n"] == 1:
            return httpx.Response(503)
        return httpx.Response(200, json={"choices": [{"message": {"content": "ok"}}]})

    async with _client(handler) as client:
        assert await service.answer(client, [], "byte") == "ok"
    assert calls["n"] == 2


async def test_answer_retries_once_on_429(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(service, "RETRY_DELAY", 0)
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        if calls["n"] == 1:
            return httpx.Response(429)
        return httpx.Response(200, json={"choices": [{"message": {"content": "ok"}}]})

    async with _client(handler) as client:
        assert await service.answer(client, [], "byte") == "ok"
    assert calls["n"] == 2


async def test_answer_does_not_retry_400(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(service, "RETRY_DELAY", 0)
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        return httpx.Response(400)

    async with _client(handler) as client:
        with pytest.raises(service.LlmUnavailable):
            await service.answer(client, [], "byte")
    assert calls["n"] == 1


async def test_answer_raises_after_second_5xx(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(service, "RETRY_DELAY", 0)
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        return httpx.Response(500)

    async with _client(handler) as client:
        with pytest.raises(service.LlmUnavailable):
            await service.answer(client, [], "byte")
    assert calls["n"] == 2


async def test_answer_timeout_raises_without_retry() -> None:
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        raise httpx.ReadTimeout("slow", request=request)

    async with _client(handler) as client:
        with pytest.raises(service.LlmUnavailable):
            await service.answer(client, [], "byte")
    assert calls["n"] == 1


async def test_answer_empty_choice_raises() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"choices": []})

    async with _client(handler) as client:
        with pytest.raises(service.LlmUnavailable):
            await service.answer(client, [], "byte")


async def test_answer_never_logs_prompt_or_answer(caplog: pytest.LogCaptureFixture) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"choices": [{"message": {"content": "секретный ответ"}}]})

    caplog.set_level("INFO")
    async with _client(handler) as client:
        await service.answer(client, [{"role": "user", "content": "секретный вопрос"}], "byte")
    assert "секретный" not in caplog.text
    assert "status=200" in caplog.text


# --- client_for -----------------------------------------------------------


async def test_client_for_requires_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("LLM_API_KEY", raising=False)
    monkeypatch.delenv("LLM_API_KEY_FILE", raising=False)
    assert service.client_for(_settings(llm_api_key="")) is None
    client = service.client_for(_settings())
    assert client is not None
    assert client.headers["authorization"] == "Bearer secret-key"
    await client.aclose()


# --- rate limiter ---------------------------------------------------------


def test_user_limit_is_five_per_minute() -> None:
    now = [0.0]
    limiter = service.RateLimiter(lambda: now[0])
    results = [limiter.allow(user_id=1, chat_id=1, group=False) for _ in range(6)]
    assert results == [True] * 5 + [False]
    now[0] = 61.0
    assert limiter.allow(user_id=1, chat_id=1, group=False) is True


def test_chat_limit_is_twenty_per_minute() -> None:
    now = [0.0]
    limiter = service.RateLimiter(lambda: now[0])
    results = [limiter.allow(user_id=user, chat_id=1, group=False) for user in range(20)]
    assert all(results)
    assert limiter.allow(user_id=99, chat_id=1, group=False) is False


def test_group_limit_is_one_per_ten_seconds() -> None:
    now = [0.0]
    limiter = service.RateLimiter(lambda: now[0])
    assert limiter.allow(user_id=1, chat_id=1, group=True) is True
    assert limiter.allow(user_id=2, chat_id=1, group=True) is False
    now[0] = 10.1
    assert limiter.allow(user_id=2, chat_id=1, group=True) is True


# --- handler flow ---------------------------------------------------------


async def test_respond_sends_html_answer_with_card_button(monkeypatch: pytest.MonkeyPatch) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"choices": [{"message": {"content": "<b>Ответ</b><script>x</script>"}}]})

    client = _client(handler)
    monkeypatch.setattr(service, "client_for", lambda settings: client)
    message = _message("/ask что делать при пожаре?")
    bot = SimpleNamespace(id=999)

    await handlers.respond(
        message,
        "что делать при пожаре?",
        bot=bot,
        settings=_settings(),
        repository=_Repo([_card()]),
        bot_username="bytebot",
    )

    message.answer.assert_awaited_once()
    text = message.answer.await_args.args[0]
    assert text == "<b>Ответ</b>x"
    markup = message.answer.await_args.kwargs["reply_markup"]
    assert markup.inline_keyboard[0][0].url == "https://t.me/bytebot?start=card_fire-home"


async def test_respond_shows_typing_and_cancels_the_repeat(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"choices": [{"message": {"content": "ok"}}]})

    monkeypatch.setattr(service, "client_for", lambda settings: _client(handler))
    message = _message("/ask что делать?")
    bot = SimpleNamespace(id=999, send_chat_action=AsyncMock())

    await handlers.respond(
        message,
        "что делать?",
        bot=bot,
        settings=_settings(),
        repository=_Repo([_card()]),
        bot_username="bytebot",
    )

    assert bot.send_chat_action.await_count >= 1
    assert bot.send_chat_action.await_args.kwargs["chat_id"] == 7
    assert bot.send_chat_action.await_args.kwargs["action"] == "typing"
    pending = [
        task
        for task in asyncio.all_tasks()
        if getattr(task.get_coro(), "__name__", "") == "_keep_typing"
    ]
    assert pending == []
    message.answer.assert_awaited_once()


async def test_respond_replies_in_group(monkeypatch: pytest.MonkeyPatch) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"choices": [{"message": {"content": "ok"}}]})

    monkeypatch.setattr(service, "client_for", lambda settings: _client(handler))
    message = _message("ok", chat_type="group")

    await handlers.respond(
        message,
        "вопрос",
        bot=SimpleNamespace(id=999),
        settings=_settings(),
        repository=None,
        bot_username="bytebot",
    )

    message.reply.assert_awaited_once()
    message.answer.assert_not_awaited()


async def test_respond_falls_back_on_missing_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(service, "client_for", lambda settings: None)
    screen = AsyncMock()
    monkeypatch.setattr(handlers, "show", screen)
    message = _message("text")

    await handlers.respond(
        message,
        "пожар",
        bot=SimpleNamespace(id=999),
        settings=_settings(llm_api_key=""),
        repository=_Repo([_card()]),
        bot_username="bytebot",
    )

    screen.assert_awaited_once()
    html = screen.await_args.args[2].html
    assert "Поиск" in html
    message.answer.assert_awaited_once()
    assert "поиска" in message.answer.await_args.args[0]


async def test_respond_falls_back_on_timeout(monkeypatch: pytest.MonkeyPatch) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("slow", request=request)

    monkeypatch.setattr(service, "client_for", lambda settings: _client(handler))
    monkeypatch.setattr(handlers, "show", AsyncMock())
    message = _message("text")

    await handlers.respond(
        message,
        "пожар",
        bot=SimpleNamespace(id=999),
        settings=_settings(),
        repository=_Repo([_card()]),
        bot_username="bytebot",
    )

    handlers.show.assert_awaited_once()
    assert "поиска" in message.answer.await_args.args[0]


async def test_respond_falls_back_on_empty_answer(monkeypatch: pytest.MonkeyPatch) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"choices": []})

    monkeypatch.setattr(service, "client_for", lambda settings: _client(handler))
    monkeypatch.setattr(handlers, "show", AsyncMock())
    message = _message("text")

    await handlers.respond(
        message,
        "пожар",
        bot=SimpleNamespace(id=999),
        settings=_settings(),
        repository=None,
        bot_username="bytebot",
    )

    handlers.show.assert_awaited_once()


async def test_respond_empty_question_sends_html_safe_usage(monkeypatch: pytest.MonkeyPatch) -> None:
    def boom(settings):
        raise AssertionError("LLM must not be called for an empty question")

    monkeypatch.setattr(service, "client_for", boom)
    message = _message("")
    await handlers.respond(
        message,
        "",
        bot=SimpleNamespace(id=999),
        settings=_settings(),
        repository=None,
    )
    text = message.answer.await_args.args[0]
    assert "&lt;вопрос&gt;" in text
    assert "<вопрос>" not in text


async def test_group_bare_mention_replies_html_safe_usage(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(handlers, "BOT_USERNAME", "bytebot")
    message = _message("@bytebot", chat_type="group")
    await handlers.group_question(
        message,
        bot=SimpleNamespace(id=999),
        settings=_settings(),
        repo=None,
    )
    message.reply.assert_awaited_once()
    text = message.reply.await_args.args[0]
    assert "&lt;вопрос&gt;" in text
    assert "<вопрос>" not in text


async def test_respond_refuses_politely_over_rate_limit() -> None:
    message = _message("text")
    message.from_user.id = 5
    monkeypatch_limiter = service.RateLimiter(lambda: 100.0)
    handlers.LIMITER = monkeypatch_limiter
    for _ in range(5):
        assert handlers.LIMITER.allow(user_id=5, chat_id=7, group=False)

    await handlers.respond(
        message,
        "пожар",
        bot=SimpleNamespace(id=999),
        settings=_settings(),
        repository=None,
        bot_username="bytebot",
    )

    message.answer.assert_awaited_once()
    assert "Слишком часто" in message.answer.await_args.args[0]


async def test_group_question_skips_ordinary_text() -> None:
    message = _message("просто болтаем", chat_type="group")
    with pytest.raises(Exception) as error:
        await handlers.group_question(
            message,
            bot=SimpleNamespace(id=999, username="bytebot"),
            settings=_settings(),
            repo=None,
        )
    assert error.type.__name__ == "SkipHandler"


def test_question_strips_ask_and_mention() -> None:
    assert handlers._question("/ask@bytebot что делать?", "bytebot") == "что делать?"
    assert handlers._question("@ByteBot что делать?", "bytebot") == "что делать?"
    assert handlers._question("/ask", "bytebot") == ""


# --- private chat mode ----------------------------------------------------


def _ok_client(text: str = "<b>ok</b>") -> httpx.AsyncClient:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"choices": [{"message": {"content": text}}]})

    return _client(handler)


async def test_chat_text_answers_with_the_menu_and_exit_buttons(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    db = FakeDB()
    await set_chat_mode(db.factory(), 42, True)
    monkeypatch.setattr(service, "client_for", lambda settings: _ok_client())
    message = _message("что делать при пожаре?")

    await handlers.chat_text(
        message,
        bot=SimpleNamespace(id=999),
        settings=_settings(),
        repo=_Repo([_card()]),
        session_factory=db.factory(),  # type: ignore[arg-type]
    )

    message.answer.assert_awaited_once()
    assert message.answer.await_args.args[0] == "<b>ok</b>"
    markup = message.answer.await_args.kwargs["reply_markup"]
    assert [
        item.callback_data for row in markup.inline_keyboard for item in row
    ] == ["b1:menu", "b1:chat_exit"]


async def test_chat_text_outside_chat_mode_falls_through_to_search() -> None:
    message = _message("пожар")

    with pytest.raises(Exception) as error:
        await handlers.chat_text(
            message,
            bot=SimpleNamespace(id=999),
            settings=_settings(),
            repo=_Repo([_card()]),
            session_factory=FakeDB().factory(),  # type: ignore[arg-type]
        )

    assert error.type.__name__ == "SkipHandler"
    message.answer.assert_not_awaited()


async def test_chat_text_fallback_keeps_the_mode_and_shows_search(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    db = FakeDB()
    await set_chat_mode(db.factory(), 42, True)
    monkeypatch.setattr(service, "client_for", lambda settings: None)
    monkeypatch.setattr(handlers, "show", AsyncMock())
    message = _message("пожар")

    await handlers.chat_text(
        message,
        bot=SimpleNamespace(id=999),
        settings=_settings(),
        repo=_Repo([_card()]),
        session_factory=db.factory(),  # type: ignore[arg-type]
    )

    handlers.show.assert_awaited_once()
    assert "поиска" in message.answer.await_args.args[0]
    assert await chat_mode_on(db.factory(), 42) is True


async def test_ask_answers_while_chat_mode_is_on(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(service, "client_for", lambda settings: _ok_client("ответ"))
    monkeypatch.setattr(handlers, "BOT_USERNAME", "bytebot")
    message = _message("/ask что делать?")

    await handlers.ask_command(
        message,
        SimpleNamespace(args="что делать?"),
        bot=SimpleNamespace(id=999),
        settings=_settings(),
        repo=_Repo([_card()]),
        session_factory=FakeDB().factory(),  # type: ignore[arg-type]
    )

    message.answer.assert_awaited_once()
    assert message.answer.await_args.args[0] == "ответ"
    markup = message.answer.await_args.kwargs["reply_markup"]
    assert markup.inline_keyboard[0][0].url == "https://t.me/bytebot?start=card_fire-home"


async def test_chat_text_never_logs_the_question(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    monkeypatch.setattr(service, "client_for", lambda settings: _ok_client("ответ"))
    db = FakeDB()
    await set_chat_mode(db.factory(), 42, True)

    caplog.set_level("INFO")
    await handlers.chat_text(
        _message("секретный вопрос про пожар"),
        bot=SimpleNamespace(id=999),
        settings=_settings(),
        repo=_Repo([_card()]),
        session_factory=db.factory(),  # type: ignore[arg-type]
    )

    assert "секретный" not in caplog.text