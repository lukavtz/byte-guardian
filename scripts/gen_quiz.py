#!/usr/bin/env python3
"""Fill missing ``quiz`` blocks in a content card through an OpenAI-compatible API.

Usage:
    LLM_BASE_URL=https://api.example/v1 LLM_MODEL=model LLM_API_KEY=... \
        uv run python scripts/gen_quiz.py --card content/fire.json
    uv run python scripts/gen_quiz.py --card content/fire.json --check
    uv run python scripts/gen_quiz.py --card content/fire.json --dry-run

``--check`` only reports threats without a quiz. ``--dry-run`` prints the merged
card without touching the file. The API key is read from the environment and is
never printed.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
from pathlib import Path
from typing import Any, Protocol

import httpx

QUESTION_COUNT = 3
OPTION_COUNT = 4
TIMEOUT_SECONDS = 60.0
ENV_BASE_URL = "LLM_BASE_URL"
ENV_MODEL = "LLM_MODEL"
ENV_API_KEY = "LLM_API_KEY"

SYSTEM_PROMPT = (
    "Ты методист по безопасности жизнедеятельности. "
    "Пишешь короткие проверочные вопросы на русском языке по карточке угрозы. "
    "Отвечай строго одним JSON-объектом без пояснений и без markdown."
)


class ChatClient(Protocol):
    def chat(self, messages: list[dict[str, str]]) -> str: ...


class OpenAICompatibleClient:
    """Minimal blocking client for ``{base_url}/chat/completions``."""

    def __init__(self, base_url: str, model: str, api_key: str | None, http: httpx.Client | None = None) -> None:
        self._url = base_url.rstrip("/") + "/chat/completions"
        self._model = model
        self._api_key = api_key
        self._http = http or httpx.Client(timeout=TIMEOUT_SECONDS)

    def chat(self, messages: list[dict[str, str]]) -> str:
        headers = {"Content-Type": "application/json"}
        if self._api_key:
            headers["Authorization"] = f"Bearer {self._api_key}"
        payload = {
            "model": self._model,
            "messages": messages,
            "temperature": 0.2,
            "response_format": {"type": "json_object"},
        }
        response = self._http.post(self._url, json=payload, headers=headers)
        response.raise_for_status()
        body = response.json()
        try:
            content = body["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError) as exc:
            raise ValueError("LLM response has no choices[0].message.content") from exc
        if not isinstance(content, str):
            raise ValueError("LLM response content is not a string")
        return content


def build_client() -> ChatClient:
    """Build the HTTP client from the environment without echoing secrets."""
    base_url = os.environ.get(ENV_BASE_URL, "").strip()
    model = os.environ.get(ENV_MODEL, "").strip()
    missing = [name for name, value in ((ENV_BASE_URL, base_url), (ENV_MODEL, model)) if not value]
    if missing:
        raise SystemExit(f"set {', '.join(missing)} to call the LLM endpoint")
    return OpenAICompatibleClient(base_url, model, os.environ.get(ENV_API_KEY) or None)


def load_card(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def find_gaps(card: dict[str, Any], locale: str = "ru") -> list[dict[str, Any]]:
    """Threats whose quiz block for ``locale`` is missing or empty."""
    gaps = []
    for threat in card.get("threats", []):
        quiz = threat.get("quiz") or {}
        if not quiz.get(locale):
            gaps.append(threat)
    return gaps


def _strip_fences(raw: str) -> str:
    text = raw.strip()
    if text.startswith("```"):
        text = text.split("\n", 1)[1] if "\n" in text else ""
        if text.rstrip().endswith("```"):
            text = text.rstrip()[:-3]
    return text.strip()


def validate_quiz(raw: str) -> list[dict[str, Any]]:
    """Parse and validate model output; returns normalised questions."""
    try:
        data = json.loads(_strip_fences(raw))
    except json.JSONDecodeError as exc:
        raise ValueError(f"model output is not JSON: {exc}") from exc
    questions = data.get("questions") if isinstance(data, dict) else data
    if not isinstance(questions, list):
        raise ValueError("model output has no questions list")
    if len(questions) != QUESTION_COUNT:
        raise ValueError(f"expected {QUESTION_COUNT} questions, got {len(questions)}")

    normalised: list[dict[str, Any]] = []
    for index, question in enumerate(questions, start=1):
        qid = f"q{index}"
        if not isinstance(question, dict):
            raise ValueError(f"{qid}: question is not an object")
        prompt = question.get("prompt")
        explanation = question.get("explanation")
        options = question.get("options")
        answer = question.get("answer")
        if not isinstance(prompt, str) or not prompt.strip():
            raise ValueError(f"{qid}: empty prompt")
        if not isinstance(explanation, str) or not explanation.strip():
            raise ValueError(f"{qid}: empty explanation")
        if not isinstance(options, list) or len(options) != OPTION_COUNT:
            raise ValueError(f"{qid}: expected {OPTION_COUNT} options")
        if any(not isinstance(option, str) or not option.strip() for option in options):
            raise ValueError(f"{qid}: blank option")
        if len(set(options)) != OPTION_COUNT:
            raise ValueError(f"{qid}: duplicate options")
        if isinstance(answer, bool) or not isinstance(answer, int) or not 0 <= answer < OPTION_COUNT:
            raise ValueError(f"{qid}: answer index {answer!r} out of range")
        normalised.append(
            {
                "id": qid,
                "prompt": prompt.strip(),
                "options": [option.strip() for option in options],
                "answer": answer,
                "explanation": explanation.strip(),
            }
        )
    return normalised


def _messages(threat: dict[str, Any], locale: str) -> list[dict[str, str]]:
    text = threat["texts"][locale]
    user = (
        f"Карточка: {text['title']}\n"
        f"Кратко: {text['summary']}\n"
        f"Признаки: {'; '.join(text['signs'])}\n"
        f"Действия: {'; '.join(text['during'])}\n"
        f"Нельзя: {'; '.join(text['forbidden'])}\n\n"
        f"Составь {QUESTION_COUNT} вопроса с четырьмя вариантами ответа. "
        "Верни JSON вида "
        '{"questions":[{"prompt":"...","options":["...","...","...","..."],"answer":0,"explanation":"..."}]}. '
        f"answer — индекс правильного варианта от 0 до {OPTION_COUNT - 1}. "
        "Один верный вариант, остальные правдоподобно неверные. Не выдумывай номера телефонов."
    )
    return [{"role": "system", "content": SYSTEM_PROMPT}, {"role": "user", "content": user}]


def fill_card(card: dict[str, Any], client: ChatClient, locale: str = "ru") -> int:
    """Fill every missing quiz block in place; returns the number of threats filled."""
    filled = 0
    for threat in find_gaps(card, locale):
        questions = validate_quiz(client.chat(_messages(threat, locale)))
        threat.setdefault("quiz", {})[locale] = questions
        filled += 1
    return filled


def write_card(path: Path, card: dict[str, Any]) -> None:
    """Write the card atomically: temp file in the same directory, then replace."""
    payload = json.dumps(card, ensure_ascii=False, indent=2) + "\n"
    path.parent.mkdir(parents=True, exist_ok=True)
    handle = tempfile.NamedTemporaryFile(
        "w", encoding="utf-8", dir=path.parent, prefix=f".{path.name}.", suffix=".tmp", delete=False
    )
    try:
        with handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(handle.name, path)
    except BaseException:
        Path(handle.name).unlink(missing_ok=True)
        raise


def run(
    card_path: Path,
    *,
    locale: str = "ru",
    check: bool = False,
    dry_run: bool = False,
    client: ChatClient | None = None,
) -> int:
    """Execute the requested mode; returns the number of gaps or filled threats."""
    card = load_card(card_path)
    gaps = find_gaps(card, locale)
    if check:
        for threat in gaps:
            print(threat["slug"])
        print(f"{len(gaps)} of {len(card['threats'])} threats need quiz.{locale}")
        return len(gaps)
    if not gaps:
        print(f"nothing to do: quiz.{locale} is complete in {card_path}")
        return 0

    chat_client = client or build_client()
    filled = fill_card(card, chat_client, locale)
    if filled:
        card["version"] = int(card.get("version", 1)) + 1
    if dry_run:
        print(json.dumps(card, ensure_ascii=False, indent=2))
        return filled
    write_card(card_path, card)
    print(f"filled {filled} threats, updated {card_path} at version {card['version']}")
    return filled


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--card", type=Path, required=True, help="path to content/<category>.json")
    parser.add_argument("--locale", default="ru", help="quiz locale to fill (default: ru)")
    parser.add_argument("--check", action="store_true", help="only report threats without a quiz")
    parser.add_argument("--dry-run", action="store_true", help="print the merged card without writing")
    return parser


def main(argv: list[str] | None = None, client: ChatClient | None = None) -> int:
    args = build_parser().parse_args(argv)
    return run(args.card, locale=args.locale, check=args.check, dry_run=args.dry_run, client=client)


if __name__ == "__main__":
    sys.exit(main())
