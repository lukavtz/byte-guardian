"""User media in, model input out: one download, one data URL, one transcript.

Bytes never reach the logs. Only the HTTP status and the size class are
reported, and the transcript is returned to the caller without being kept.
"""

from __future__ import annotations

import base64
import logging
from io import BytesIO

import httpx
from aiogram import Bot

from app.config import Settings
from app.runtime.secrets import read_secret

logger = logging.getLogger(__name__)

MAX_BYTES = 20 * 1024 * 1024
TRANSCRIBE_MODEL = "whisper-1"
TRANSCRIBE_TIMEOUT = 60.0
DEFAULT_IMAGE_MIME = "image/jpeg"


class MediaTooLarge(RuntimeError):
    """The file is over the download ceiling; it is never fetched."""


class MediaUnavailable(RuntimeError):
    """The file or its transcript could not be produced; the caller answers."""


async def download(bot: Bot, file_id: str, *, declared_size: int | None = None) -> bytes:
    """Fetch one Telegram file into memory, refusing anything over `MAX_BYTES`."""
    if declared_size is not None and declared_size > MAX_BYTES:
        raise MediaTooLarge(str(declared_size))
    buffer = BytesIO()
    try:
        await bot.download(file_id, destination=buffer)
    except Exception as error:
        logger.warning("media download failed: %s", type(error).__name__)
        raise MediaUnavailable("download") from error
    data = buffer.getvalue()
    if len(data) > MAX_BYTES:
        raise MediaTooLarge(str(len(data)))
    if not data:
        raise MediaUnavailable("empty file")
    return data


def image_data_url(data: bytes, mime: str | None = None) -> str:
    """A self-contained data URL; the model provider never sees a Telegram link."""
    return f"data:{mime or DEFAULT_IMAGE_MIME};base64," + base64.b64encode(data).decode("ascii")


def client_for(settings: Settings) -> httpx.AsyncClient | None:
    base = (settings.llm_base_url or "").rstrip("/")
    key = settings.llm_api_key
    if not key:
        try:
            key = read_secret("LLM_API_KEY") or ""
        except RuntimeError:
            key = ""
    if not base or not key:
        return None
    return httpx.AsyncClient(
        base_url=base + "/",
        timeout=httpx.Timeout(TRANSCRIBE_TIMEOUT),
        headers={"Authorization": "Bearer " + key},
    )


async def transcribe(
    settings: Settings,
    data: bytes,
    *,
    filename: str = "audio.ogg",
    mime: str = "audio/ogg",
) -> str:
    """POST the audio to `/audio/transcriptions` and return the plain text."""
    client = client_for(settings)
    if client is None:
        raise MediaUnavailable("transcription is not configured")
    try:
        async with client:
            response = await client.post(
                "audio/transcriptions",
                data={"model": TRANSCRIBE_MODEL},
                files={"file": (filename, data, mime)},
            )
    except httpx.HTTPError as error:
        logger.warning("transcription failed: %s", type(error).__name__)
        raise MediaUnavailable("transport") from error
    logger.info("transcription status=%s bytes=%s", response.status_code, _size_class(len(data)))
    if response.status_code != 200:
        raise MediaUnavailable(f"status {response.status_code}")
    try:
        payload = response.json()
    except ValueError as error:
        raise MediaUnavailable("bad json") from error
    text = payload.get("text") if isinstance(payload, dict) else None
    return text.strip() if isinstance(text, str) else ""


def _size_class(size: int) -> str:
    """A coarse bucket: enough to debug, not enough to fingerprint a file."""
    if size < 64 * 1024:
        return "small"
    if size < 1024 * 1024:
        return "medium"
    return "large"