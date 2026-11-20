from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest
from test_llm import _card, _client, _message, _Repo, _settings

from app.handlers import llm as handlers
from app.services import llm as service
from app.services import web_search
from app.services.web_search import WebResult, format_web_context, search_web

DDG_HTML = """
<html><body>
<div class="result results_links">
  <h2><a rel="nofollow" class="result__a"
      href="//duckduckgo.com/l/?uddg=https%3A%2F%2Fexample.org%2Fnews%3Fid%3D1&amp;rut=abc">Сводка
      <b>новостей</b></a></h2>
  <a class="result__snippet" href="#">Пожарная обстановка за <b>сегодня</b>.</a>
</div>
<div class="result results_links">
  <h2><a rel="nofollow" class="result__a" href="https://mchs.gov.ru/deyatelnost/novosti">Новости МЧС</a></h2>
  <a class="result__snippet" href="#">Официальные сообщения ведомства.</a>
</div>
<div class="result result--ad">
  <a class="result__a" href="https://duckduckgo.com/y.js?ad_provider=1">Реклама</a>
</div>
</body></html>
"""

WIKI_JSON = {
    "query": {
        "search": [
            {
                "title": "Пожар",
                "snippet": '<span class="searchmatch">Пожар</span> — неконтролируемый процесс горения.',
            }
        ]
    }
}

JINA_MARKDOWN = """Title: пожар мчс at DuckDuckGo

Markdown Content:
## [МЧС России](https://duckduckgo.com/l/?uddg=https%3A%2F%2Fmchs.gov.ru%2F&rut=abc)

[![Image 1](https://external-content.duckduckgo.com/ip3/mchs.gov.ru.ico)](https://duckduckgo.com/l/?uddg=https%3A%2F%2Fmchs.gov.ru%2F&rut=abc)[mchs.gov.ru](https://duckduckgo.com/l/?uddg=https%3A%2F%2Fmchs.gov.ru%2F&rut=abc)

[Министерство по делам гражданской обороны, чрезвычайным ситуациям и ликвидации последствий стихийных бедствий.](https://duckduckgo.com/l/?uddg=https%3A%2F%2Fmchs.gov.ru%2F&rut=abc)

## [Пожарная безопасность](https://duckduckgo.com/l/?uddg=https%3A%2F%2Fexample.org%2Ffire&rut=def)

[Первое описание, которое должно быть проигнорировано как слишком короткое.](https://duckduckgo.com/l/?uddg=https%3A%2F%2Fexample.org%2Ffire&rut=def)

[Правила поведения при пожаре: звоните 101 и покиньте помещение.](https://duckduckgo.com/l/?uddg=https%3A%2F%2Fexample.org%2Ffire&rut=def)
"""


def _client_with(
    ddg: httpx.Response,
    wiki: httpx.Response | None = None,
    calls: list[str] | None = None,
    relay: httpx.Response | None = None,
) -> httpx.AsyncClient:
    def handler(request: httpx.Request) -> httpx.Response:
        if calls is not None:
            calls.append(str(request.url))
        if "duckduckgo.com" in request.url.host:
            return ddg
        if "jina.ai" in request.url.host:
            return relay if relay is not None else httpx.Response(200, text="Markdown Content:\n")
        return wiki if wiki is not None else httpx.Response(200, json={"query": {"search": []}})

    return httpx.AsyncClient(transport=httpx.MockTransport(handler), timeout=2.0)


@pytest.fixture(autouse=True)
def _clear_cache() -> None:
    web_search._CACHE.clear()


# --- search_web -----------------------------------------------------------


async def test_parses_duckduckgo_links_and_resolves_redirects() -> None:
    async with _client_with(httpx.Response(200, text=DDG_HTML)) as client:
        results = await search_web("новости мчс", limit=3, client=client)

    assert [item.url for item in results] == [
        "https://mchs.gov.ru/deyatelnost/novosti",
        "https://example.org/news?id=1",
    ]
    assert results[0].title == "Новости МЧС"
    assert results[1].title == "Сводка новостей"
    assert results[1].snippet == "Пожарная обстановка за сегодня."


async def test_official_domains_move_ahead_of_other_hits() -> None:
    html = """
    <a class="result__a" href="https://example.org/1">Первое</a>
    <a class="result__a" href="https://www.mchs.gov.ru/2">МЧС</a>
    <a class="result__a" href="https://gosuslugi.ru/3">Госуслуги</a>
    """
    async with _client_with(httpx.Response(200, text=html)) as client:
        results = await search_web("пожар", limit=3, client=client)

    assert [item.url for item in results] == [
        "https://www.mchs.gov.ru/2",
        "https://gosuslugi.ru/3",
        "https://example.org/1",
    ]


async def test_cache_hit_avoids_the_second_request() -> None:
    calls: list[str] = []
    async with _client_with(httpx.Response(200, text=DDG_HTML), calls=calls) as client:
        first = await search_web("пожар сегодня", client=client)
        second = await search_web("пожар сегодня", client=client)

    assert len(calls) == 1
    assert first == second


async def test_falls_back_to_wikipedia_when_ddg_is_empty() -> None:
    async with _client_with(
        httpx.Response(200, text="<html><body>нет результатов</body></html>"),
        httpx.Response(200, json=WIKI_JSON),
    ) as client:
        results = await search_web("пожар", limit=3, client=client)

    assert len(results) == 1
    assert results[0].title == "Пожар"
    assert results[0].url == "https://ru.wikipedia.org/wiki/%D0%9F%D0%BE%D0%B6%D0%B0%D1%80"
    assert "<span" not in results[0].snippet
    assert results[0].snippet.startswith("Пожар — неконтролируемый")


async def test_falls_back_to_jina_relay_before_wikipedia() -> None:
    calls: list[str] = []
    async with _client_with(
        httpx.Response(202, text="anomaly"),
        calls=calls,
        relay=httpx.Response(200, text=JINA_MARKDOWN),
    ) as client:
        results = await search_web("пожар мчс", limit=3, client=client)

    assert [item.url for item in results] == ["https://mchs.gov.ru/", "https://example.org/fire"]
    assert results[0].title == "МЧС России"
    assert results[1].snippet == "Правила поведения при пожаре: звоните 101 и покиньте помещение."
    assert [url for url in calls if "wikipedia" in url] == []


async def test_malformed_html_and_broken_fallback_return_nothing() -> None:
    async with _client_with(
        httpx.Response(200, text='<div class="result__a" href="//bad'),
        httpx.Response(500, text="boom"),
    ) as client:
        results = await search_web("пожар", limit=3, client=client)

    assert results == []


async def test_blank_query_skips_the_network() -> None:
    calls: list[str] = []
    async with _client_with(httpx.Response(200, text=DDG_HTML), calls=calls) as client:
        assert await search_web("   ", client=client) == []
    assert calls == []


# --- prompt context -------------------------------------------------------


def test_web_context_is_marked_untrusted_and_clipped() -> None:
    block = format_web_context(
        [WebResult(title="Т", url="https://mchs.gov.ru/x", snippet="а" * 400)]
    )
    assert "UNTRUSTED" in block
    assert "https://mchs.gov.ru/x" in block
    assert "а" * 300 in block
    assert "а" * 301 not in block


def test_build_messages_appends_the_untrusted_block() -> None:
    block = format_web_context([WebResult(title="Т", url="https://cbr.ru/x", snippet="к")])
    messages = service.build_messages("какой курс?", "ru", [_card()], block)
    user = messages[1]["content"]

    assert "Карточки:" in user
    assert user.index("Карточки:") < user.index("UNTRUSTED")
    assert "Источник: <url>" in messages[0]["content"]


# --- handler flow ---------------------------------------------------------


async def test_respond_adds_source_buttons_next_to_menu(monkeypatch: pytest.MonkeyPatch) -> None:
    url = "https://mchs.gov.ru/deyatelnost/novosti"

    async def fake_search(query: str, **kwargs: object) -> list[WebResult]:
        return [WebResult(title="Новости МЧС", url=url, snippet="Сводка")]

    def completion(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={"choices": [{"message": {"content": f"Коротко о обстановке.\nИсточник: {url}"}}]},
        )

    monkeypatch.setattr(handlers, "search_web", fake_search)
    monkeypatch.setattr(service, "client_for", lambda settings: _client(completion))
    message = _message("что нового?")

    await handlers.respond(
        message,
        "что сейчас с пожарами?",
        bot=SimpleNamespace(id=999),
        settings=_settings(),
        repository=None,
        bot_username="bytebot",
        chat=True,
    )

    markup = message.answer.await_args.kwargs["reply_markup"]
    assert markup.inline_keyboard[0][0].text == "mchs.gov.ru"
    assert markup.inline_keyboard[0][0].url == url
    assert markup.inline_keyboard[1][0].callback_data is not None
    assert len(markup.inline_keyboard) == 2


async def test_respond_skips_search_for_strong_card_matches(monkeypatch: pytest.MonkeyPatch) -> None:
    search = AsyncMock(return_value=[])

    def completion(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"choices": [{"message": {"content": "Ответ по карточке"}}]})

    monkeypatch.setattr(handlers, "search_web", search)
    monkeypatch.setattr(service, "client_for", lambda settings: _client(completion))
    message = _message("вопрос")

    await handlers.respond(
        message,
        "что делать при пожаре?",
        bot=SimpleNamespace(id=999),
        settings=_settings(),
        repository=_Repo([_card("fire-home"), _card("fire-office")]),
        bot_username="bytebot",
    )

    search.assert_not_awaited()
    markup = message.answer.await_args.kwargs["reply_markup"]
    assert len(markup.inline_keyboard) == 1
    assert markup.inline_keyboard[0][0].url.startswith("https://t.me/bytebot?start=card_")


async def test_invented_source_url_gets_no_button(monkeypatch: pytest.MonkeyPatch) -> None:
    async def fake_search(query: str, **kwargs: object) -> list[WebResult]:
        return [WebResult(title="Т", url="https://mchs.gov.ru/real", snippet="с")]

    def completion(request: httpx.Request) -> httpx.Response:
        content = "Текст\nИсточник: https://invented.example/x"
        return httpx.Response(200, json={"choices": [{"message": {"content": content}}]})

    monkeypatch.setattr(handlers, "search_web", fake_search)
    monkeypatch.setattr(service, "client_for", lambda settings: _client(completion))
    message = _message("вопрос")

    await handlers.respond(
        message,
        "где ближайший пункт?",
        bot=SimpleNamespace(id=999),
        settings=_settings(),
        repository=None,
        bot_username="bytebot",
        chat=True,
    )

    markup = message.answer.await_args.kwargs["reply_markup"]
    assert markup.inline_keyboard[0][0].callback_data is not None