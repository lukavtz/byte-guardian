from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from aiogram.exceptions import TelegramRetryAfter

from app.services import llm as service
from app.ui import draft as ui_draft


class _Clock:
    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now


def _bot() -> SimpleNamespace:
    return SimpleNamespace(
        send_rich_message_draft=AsyncMock(return_value=True),
        send_message_draft=AsyncMock(return_value=True),
        send_message=AsyncMock(return_value=SimpleNamespace(message_id=5)),
        edit_message_text=AsyncMock(return_value=SimpleNamespace(message_id=5)),
    )


def test_draft_html_cuts_a_partial_word_and_unfinished_tag() -> None:
    partial = "<b>Закрой газ и открой</b"
    rendered = service.draft_html(partial, complete=False)
    assert rendered.startswith("<b>Закрой газ")
    assert "<b" not in rendered.replace("<b>", "")


def test_draft_html_keeps_a_complete_answer_and_caps_it() -> None:
    assert service.draft_html("<b>Готово</b>", complete=True) == "<b>Готово</b>"
    capped = service.draft_html("а" * 4000, complete=False)
    assert len(capped) <= 3500
    assert capped.endswith("…")


def test_strip_answer_urls_removes_source_lines_and_bare_urls() -> None:
    raw = "Закрой кран.\n\nИсточник: https://evil.test/x\nСмотри https://x.test/y тоже"
    cleaned = service.strip_answer_urls(raw)
    assert "http" not in cleaned
    assert "Источник" not in cleaned
    assert "Закрой кран." in cleaned


async def test_draft_sink_throttles_and_clears_on_finish(monkeypatch: pytest.MonkeyPatch) -> None:
    clock = _Clock()
    bot = _bot()
    shown = AsyncMock(return_value=None)
    monkeypatch.setattr(ui_draft, "show", shown)
    sink = ui_draft.DraftSink(bot, chat_id=1, draft_id=42, interval=1.0, clock=clock)  # type: ignore[arg-type]

    await sink.push("привет")
    await sink.push("привет мир")
    assert bot.send_rich_message_draft.await_count == 1

    clock.now = 1.5
    await sink.push("привет мир!")
    assert bot.send_rich_message_draft.await_count == 2

    await sink.finish("финал", None)
    shown.assert_awaited_once()
    bot.send_message_draft.assert_awaited()
    assert bot.send_message_draft.await_args.kwargs["text"] == ""


async def test_draft_sink_pauses_on_retry_after_and_disables_after_failures() -> None:
    clock = _Clock()
    bot = _bot()
    sink = ui_draft.DraftSink(bot, chat_id=1, draft_id=42, interval=0.0, clock=clock)  # type: ignore[arg-type]

    bot.send_rich_message_draft.side_effect = TelegramRetryAfter(
        SimpleNamespace(), "slow down", 5  # type: ignore[arg-type]
    )
    await sink.push("раз")
    await sink.push("два")
    assert bot.send_rich_message_draft.await_count == 1

    clock.now = 10.0
    bot.send_rich_message_draft.side_effect = RuntimeError("boom")
    for text in ("три", "четыре", "пять"):
        clock.now += 1.0
        await sink.push(text)
    assert sink.disabled is True


async def test_status_sink_posts_once_then_edits_with_a_gap() -> None:
    clock = _Clock()
    bot = _bot()
    sink = ui_draft.StatusEditSink(
        bot, chat_id=1, interval=1.0, min_edit_gap=10.0, clock=clock  # type: ignore[arg-type]
    )

    await sink.push("Думаю")
    await sink.push("Думаю дальше")
    assert bot.send_message.await_count == 1
    assert bot.edit_message_text.await_count == 0

    clock.now = 11.0
    await sink.push("Почти готово")
    assert bot.edit_message_text.await_count == 1

    await sink.finish("Готово", None)
    assert bot.edit_message_text.await_args.kwargs["text"] == "Готово"


async def test_status_sink_sends_a_new_message_when_the_status_is_gone() -> None:
    bot = _bot()
    bot.edit_message_text.side_effect = RuntimeError("message to edit not found")
    sink = ui_draft.StatusEditSink(bot, chat_id=1, clock=_Clock())  # type: ignore[arg-type]

    await sink.push("Думаю")
    await sink.finish("Готово", None)
    assert bot.send_message.await_count == 2


async def test_status_sink_sanitizes_model_deltas_before_send_and_edit() -> None:
    raw = '<b onclick="x">hi</b><img src=x onerror=alert(1)>'
    expected = "<b>hi</b>"
    assert "onclick" not in expected and "onerror" not in expected and "<img" not in expected

    clock = _Clock()
    bot = _bot()
    sink = ui_draft.StatusEditSink(
        bot, chat_id=1, interval=1.0, min_edit_gap=0.0, clock=clock  # type: ignore[arg-type]
    )

    await sink.push(raw)
    assert bot.send_message.await_args.kwargs["text"] == expected

    clock.now = 1.0
    await sink.push(raw + "!")
    edit_text = bot.edit_message_text.await_args.kwargs["text"]
    assert edit_text == "<b>hi</b>!"
    assert "onclick" not in edit_text and "onerror" not in edit_text and "<img" not in edit_text