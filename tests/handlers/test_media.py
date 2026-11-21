"""Photos and voice notes: download, vision payload, transcription, fallbacks."""

from __future__ import annotations

import base64
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest
from test_llm import _card, _client, _Repo, _settings
from test_nav import FakeDB

from app.handlers import media
from app.services import llm as service
from app.services import media as media_service

IMAGE = b"image-bytes"


class FakeBot:
    """Just enough Bot: a download that fills the caller's buffer."""

    def __init__(self, data: bytes = IMAGE) -> None:
        self.id = 999
        self.data = data
        self.downloads: list[str] = []

    async def send_chat_action(self, **_kwargs: object) -> bool:
        return True

    async def download(self, file: str, destination=None, **_kwargs: object):
        self.downloads.append(file)
        assert destination is not None
        destination.write(self.data)
        return destination


def _chat(text: str = "ответ") -> httpx.AsyncClient:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"choices": [{"message": {"content": text}}]})

    return _client(handler)


def _message(*, caption: str | None = None, chat_type: str = "private", message_id: int = 11):
    message = MagicMock()
    message.text = None
    message.caption = caption
    message.message_id = message_id
    message.chat = SimpleNamespace(id=7, type=chat_type)
    message.from_user = SimpleNamespace(id=42, language_code="ru")
    message.reply_to_message = None
    message.answer = AsyncMock()
    message.reply = AsyncMock()
    return message


def _photo(*, caption: str | None = None, size: int = 1024, **kwargs):
    message = _message(caption=caption, **kwargs)
    message.photo = [SimpleNamespace(file_id="photo-1", file_size=size, width=90, height=90)]
    message.document = None
    message.voice = None
    message.audio = None
    return message


def _image_document(*, size: int = 1024, mime: str = "image/png"):
    message = _message()
    message.photo = []
    message.document = SimpleNamespace(
        file_id="doc-1", file_size=size, mime_type=mime, file_name="scan.png"
    )
    message.voice = None
    message.audio = None
    return message


def _voice(*, size: int = 4096):
    message = _message()
    message.photo = []
    message.document = None
    message.voice = SimpleNamespace(file_id="voice-1", file_size=size, mime_type="audio/ogg")
    message.audio = None
    return message


@pytest.fixture(autouse=True)
def _fresh(monkeypatch: pytest.MonkeyPatch) -> None:
    now = [0.0]
    from app.handlers import llm as handlers

    monkeypatch.setattr(handlers, "LIMITER", service.RateLimiter(lambda: now[0]))
    from app.services.conversation import ConversationStore

    monkeypatch.setattr(handlers, "HISTORY", ConversationStore())


async def _run_photo(message, bot: FakeBot, db: FakeDB) -> None:
    await media.on_photo(
        message,
        bot=bot,  # type: ignore[arg-type]
        settings=_settings(),
        repo=_Repo([_card()]),
        session_factory=db.factory(),  # type: ignore[arg-type]
    )


async def _run_audio(message, bot: FakeBot, db: FakeDB) -> None:
    await media.on_audio(
        message,
        bot=bot,  # type: ignore[arg-type]
        settings=_settings(),
        repo=_Repo([_card()]),
        session_factory=db.factory(),  # type: ignore[arg-type]
    )


# --- photos ---------------------------------------------------------------


async def test_photo_goes_to_the_model_as_image_url(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        content = body["messages"][-1]["content"]
        seen["content"] = content
        return httpx.Response(200, json={"choices": [{"message": {"content": "Похоже на знак выхода."}}]})

    monkeypatch.setattr(service, "client_for", lambda settings: _client(handler))
    bot = FakeBot(IMAGE)
    message = _photo()

    await _run_photo(message, bot, FakeDB())

    content = seen["content"]
    assert content[0]["type"] == "text"
    assert media.DEFAULT_IMAGE_PROMPT in content[0]["text"]
    assert content[1]["type"] == "image_url"
    url = content[1]["image_url"]["url"]
    assert url.startswith("data:image/jpeg;base64,")
    assert base64.b64decode(url.split(",", 1)[1]) == IMAGE
    assert bot.downloads == ["photo-1"]
    message.answer.assert_awaited_once()
    assert message.answer.await_args.args[0] == "Похоже на знак выхода."


async def test_photo_caption_is_the_question(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["body"] = json.loads(request.content)
        return httpx.Response(200, json={"choices": [{"message": {"content": "Это огнетушитель."}}]})

    monkeypatch.setattr(service, "client_for", lambda settings: _client(handler))
    await _run_photo(_photo(caption="что за штука на стене?"), FakeBot(), FakeDB())

    content = seen["body"]["messages"][-1]["content"]
    assert content[0]["text"].startswith("Язык ответа: ru")
    assert "что за штука на стене?" in content[0]["text"]


async def test_image_document_is_accepted(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(service, "client_for", lambda settings: _chat("Понял схему."))
    bot = FakeBot()
    await _run_photo(_image_document(), bot, FakeDB())
    assert bot.downloads == ["doc-1"]


async def test_photo_over_twenty_megabytes_is_refused(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return httpx.Response(200, json={"choices": [{"message": {"content": "нет"}}]})

    monkeypatch.setattr(service, "client_for", lambda settings: _client(handler))
    bot = FakeBot()
    message = _photo(size=media_service.MAX_BYTES + 1)

    await _run_photo(message, bot, FakeDB())

    assert bot.downloads == []
    assert calls == []
    assert message.answer.await_args.args[0] == media.TOO_BIG["ru"]


async def test_vision_failure_sends_a_human_fallback(monkeypatch: pytest.MonkeyPatch) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, json={})

    monkeypatch.setattr(service, "client_for", lambda settings: _client(handler))
    message = _photo()

    await _run_photo(message, FakeBot(), FakeDB())

    sent = message.answer.await_args.args[0]
    assert sent == media.IMAGE_FAIL["ru"]
    assert "Traceback" not in sent


async def test_group_photo_without_mention_is_skipped() -> None:
    from aiogram.dispatcher.event.bases import SkipHandler

    message = _photo(chat_type="group")
    with pytest.raises(SkipHandler):
        await _run_photo(message, FakeBot(), FakeDB())
    message.reply.assert_not_awaited()


async def test_group_photo_with_mention_is_answered(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.handlers import llm as llm_handler

    monkeypatch.setattr(llm_handler, "BOT_USERNAME", "bytebot")
    monkeypatch.setattr(service, "client_for", lambda settings: _chat("Вижу огонь."))
    message = _photo(caption="@bytebot что тут?", chat_type="group")

    await _run_photo(message, FakeBot(), FakeDB())

    message.reply.assert_awaited_once()
    message.answer.assert_not_awaited()


# --- voice ----------------------------------------------------------------


def _transcriber(text: str, seen: dict[str, object] | None = None):
    def handler(request: httpx.Request) -> httpx.Response:
        if seen is not None:
            seen["content_type"] = request.headers.get("content-type", "")
            seen["body"] = request.content
            seen["path"] = request.url.path
        return httpx.Response(200, json={"text": text})

    return httpx.AsyncClient(
        transport=httpx.MockTransport(handler), base_url="https://llm.test/v1/"
    )


async def test_voice_is_transcribed_then_answered(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: dict[str, object] = {}
    questions: list[str] = []

    def chat(request: httpx.Request) -> httpx.Response:
        questions.append(json.loads(request.content)["messages"][-1]["content"])
        return httpx.Response(200, json={"choices": [{"message": {"content": "Закрой газ."}}]})

    monkeypatch.setattr(media_service, "client_for", lambda settings: _transcriber("запах газа", seen))
    monkeypatch.setattr(service, "client_for", lambda settings: _client(chat))
    bot = FakeBot(b"voice-bytes")
    message = _voice()

    await _run_audio(message, bot, FakeDB())

    assert seen["path"] == "/v1/audio/transcriptions"
    assert str(seen["content_type"]).startswith("multipart/form-data")
    assert b'name="model"' in seen["body"] and b"whisper-1" in seen["body"]
    assert b"voice-bytes" in seen["body"]
    assert bot.downloads == ["voice-1"]
    assert any("запах газа" in question for question in questions)
    assert message.answer.await_args.args[0] == "Закрой газ."


async def test_empty_transcript_asks_to_repeat(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[httpx.Request] = []

    def chat(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return httpx.Response(200, json={"choices": [{"message": {"content": "нет"}}]})

    monkeypatch.setattr(media_service, "client_for", lambda settings: _transcriber("   "))
    monkeypatch.setattr(service, "client_for", lambda settings: _client(chat))
    message = _voice()

    await _run_audio(message, FakeBot(), FakeDB())

    assert message.answer.await_args.args[0] == media.EMPTY_VOICE["ru"]
    assert calls == []


async def test_transcription_failure_is_human(monkeypatch: pytest.MonkeyPatch) -> None:
    def broken(request: httpx.Request) -> httpx.Response:
        return httpx.Response(503, json={})

    monkeypatch.setattr(
        media_service,
        "client_for",
        lambda settings: httpx.AsyncClient(
            transport=httpx.MockTransport(broken), base_url="https://llm.test/v1/"
        ),
    )
    message = _voice()

    await _run_audio(message, FakeBot(), FakeDB())

    assert message.answer.await_args.args[0] == media.AUDIO_FAIL["ru"]


async def test_transcript_is_never_logged(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level("DEBUG")
    monkeypatch.setattr(media_service, "client_for", lambda settings: _transcriber("секретная фраза"))
    monkeypatch.setattr(service, "client_for", lambda settings: _chat("ок"))
    message = _voice()

    await _run_audio(message, FakeBot(), FakeDB())

    assert "секретная фраза" not in caplog.text