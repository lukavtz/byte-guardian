"""Offline tests for scripts/gen_quiz.py; no network calls are made."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

import httpx
import pytest

from scripts.gen_quiz import (
    PROMPT_VERSION,
    OpenAICompatibleClient,
    build_parser,
    cache_path,
    fill_card,
    find_gaps,
    main,
    run,
    validate_quiz,
    write_card,
)

QUESTIONS: dict[str, Any] = {
    "questions": [
        {
            "prompt": "Что делать первым?",
            "options": ["Позвонить 112", "Открыть окно", "Ждать", "Снять трубку"],
            "answer": 0,
            "explanation": "Сначала вызов помощи.",
        },
        {
            "prompt": "Чего нельзя делать?",
            "options": ["Выходить", "Лифт", "Звонить", "Бежать"],
            "answer": 1,
            "explanation": "Лифт может отключиться.",
        },
        {
            "prompt": "Куда идти?",
            "options": ["Вверх", "Вниз", "На улицу", "В подвал"],
            "answer": 2,
            "explanation": "Выход на улицу безопаснее.",
        },
    ]
}


class FakeClient:
    def __init__(self, payload: Any = QUESTIONS) -> None:
        self.payload = payload
        self.calls: list[list[dict[str, str]]] = []

    def chat(self, messages: list[dict[str, str]]) -> str:
        self.calls.append(messages)
        return self.payload if isinstance(self.payload, str) else json.dumps(self.payload, ensure_ascii=False)


class ExplodingClient:
    def chat(self, messages: list[dict[str, str]]) -> str:
        raise AssertionError("client must not be called")


def make_threat(slug: str = "fire-home", *, quiz: Any = None) -> dict[str, Any]:
    threat: dict[str, Any] = {
        "slug": slug,
        "severity": 3,
        "texts": {
            "ru": {
                "title": "Пожар в квартире",
                "summary": "Дым и огонь в квартире.",
                "signs": ["Дым"],
                "before": ["Датчик"],
                "during": ["Звонить 112"],
                "after": ["Осмотр"],
                "forbidden": ["Лифт"],
                "contacts": ["112"],
                "source": "МЧС",
            }
        },
    }
    if quiz is not None:
        threat["quiz"] = quiz
    return threat


def make_card(*threats: dict[str, Any]) -> dict[str, Any]:
    return {
        "version": 1,
        "category": {"slug": "fire", "emoji": "🔥", "sort": 1, "title": {"ru": "Пожар"}},
        "threats": list(threats),
    }


def test_parser_defaults_and_flags(tmp_path: Path) -> None:
    args = build_parser().parse_args(["--card", str(tmp_path / "fire.json")])
    assert args.card == tmp_path / "fire.json"
    assert args.locale == "ru"
    assert args.count == 3
    assert args.force is False
    assert args.check is False
    assert args.dry_run is False

    args = build_parser().parse_args(
        ["--card", "a.json", "--locale", "en", "--count", "5", "--force", "--check", "--dry-run"]
    )
    assert (args.locale, args.count, args.force, args.check, args.dry_run) == ("en", 5, True, True, True)


@pytest.mark.parametrize("bad_count", [0, 2, 6, -1])
def test_parser_rejects_out_of_range_count(tmp_path: Path, bad_count: int) -> None:
    with pytest.raises(SystemExit):
        build_parser().parse_args(["--card", str(tmp_path / "fire.json"), "--count", str(bad_count)])


def test_find_gaps_flags_missing_and_empty() -> None:
    card = make_card(make_threat("fire-a"), make_threat("fire-b", quiz={"ru": []}), make_threat("fire-c"))
    card["threats"][2]["quiz"] = {"ru": [{"id": "q1"}]}
    assert [t["slug"] for t in find_gaps(card)] == ["fire-a", "fire-b"]
    assert find_gaps(card, "en") == card["threats"]


def test_fill_card_merges_and_preserves_existing() -> None:
    existing = [{"id": "q1", "prompt": "p", "options": list("abcd"), "answer": 0, "explanation": "e"}]
    card = make_card(make_threat("fire-a", quiz={"ru": []}), make_threat("fire-b", quiz={"ru": existing}))
    client = FakeClient()

    filled = fill_card(card, client)

    assert filled == 1
    assert card["threats"][1]["quiz"]["ru"] == existing
    questions = card["threats"][0]["quiz"]["ru"]
    assert [q["id"] for q in questions] == ["q1", "q2", "q3"]
    assert [q["answer"] for q in questions] == [0, 1, 2]
    assert len(client.calls) == 1
    assert "Пожар в квартире" in client.calls[0][1]["content"]


def test_fill_card_honours_count() -> None:
    five = {"questions": QUESTIONS["questions"] + [
        {"prompt": "Дополнительный 4?", "options": ["a", "b", "c", "d"], "answer": 0, "explanation": "e4"},
        {"prompt": "Дополнительный 5?", "options": ["a", "b", "c", "d"], "answer": 0, "explanation": "e5"},
    ]}
    card = make_card(make_threat("fire-a"))
    fill_card(card, FakeClient(five), count=5)
    assert len(card["threats"][0]["quiz"]["ru"]) == 5


def test_validate_quiz_accepts_bare_list_and_fenced_json() -> None:
    bare = validate_quiz(json.dumps(QUESTIONS["questions"]))
    assert len(bare) == 3
    fenced = validate_quiz("```json\n" + json.dumps(QUESTIONS) + "\n```")
    assert fenced == bare


@pytest.mark.parametrize(
    "payload",
    [
        {"questions": QUESTIONS["questions"][:2]},
        {"questions": [{**QUESTIONS["questions"][0], "options": ["a", "b", "c"]}] * 3},
        {"questions": [{**QUESTIONS["questions"][0], "answer": 4}] * 3},
        {"questions": [{**QUESTIONS["questions"][0], "answer": -1}] * 3},
        {"questions": [{**QUESTIONS["questions"][0], "answer": "1"}] * 3},
        {"questions": [{**QUESTIONS["questions"][0], "options": ["a", "a", "b", "c"]}] * 3},
        {"questions": [{**QUESTIONS["questions"][0], "prompt": "  "}] * 3},
        {"questions": [{**QUESTIONS["questions"][0], "explanation": ""}] * 3},
        "not json at all",
    ],
)
def test_validate_quiz_rejects_bad_model_output(payload: Any) -> None:
    with pytest.raises(ValueError):
        validate_quiz(payload if isinstance(payload, str) else json.dumps(payload))


def test_fill_card_propagates_rejection_without_mutation() -> None:
    card = make_card(make_threat("fire-a"))
    bad = {"questions": [{**QUESTIONS["questions"][0], "options": ["a", "b", "c", "d", "e"]}] * 3}
    with pytest.raises(ValueError, match="4 options"):
        fill_card(card, FakeClient(bad))
    assert "quiz" not in card["threats"][0]


def _cache_key(card: dict[str, Any], *, count: int = 3, model: str = "") -> str:
    payload = json.dumps(
        {"card": card, "prompt_version": PROMPT_VERSION, "count": count, "model": model},
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def test_cache_path_is_deterministic_and_gitignored(tmp_path: Path) -> None:
    card = make_card(make_threat())
    expected = tmp_path / "quiz" / f"{_cache_key(card)}.json"
    assert cache_path(card, tmp_path) == expected
    assert ".cache/" in (Path(__file__).parents[2] / ".gitignore").read_text(encoding="utf-8")
    assert cache_path(card, tmp_path) == cache_path(card, tmp_path)


def test_run_uses_cache_and_force_bypasses(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    card_path = tmp_path / "fire.json"
    original = make_card(make_threat())
    card_path.write_text(json.dumps(original), encoding="utf-8")
    cache_dir = tmp_path / ".cache"
    monkeypatch.setenv("LLM_MODEL", "test-model")

    first = FakeClient()
    assert run(card_path, client=first, cache_dir=cache_dir, model="test-model") == 1
    stored = cache_dir / "quiz" / f"{_cache_key(original, model='test-model')}.json"
    assert stored.exists()
    assert first.calls

    card_path.write_text(json.dumps(original), encoding="utf-8")
    cached = FakeClient()
    assert run(card_path, client=cached, cache_dir=cache_dir, model="test-model") == 1
    assert not cached.calls
    assert json.loads(card_path.read_text(encoding="utf-8"))["version"] == 2

    card_path.write_text(json.dumps(original), encoding="utf-8")
    forced = FakeClient()
    assert run(card_path, client=forced, cache_dir=cache_dir, model="test-model", force=True) == 1
    assert forced.calls


def test_run_cache_invalidates_on_card_change(tmp_path: Path) -> None:
    card_path = tmp_path / "fire.json"
    card_path.write_text(json.dumps(make_card(make_threat())), encoding="utf-8")
    cache_dir = tmp_path / ".cache"

    run(card_path, client=FakeClient(), cache_dir=cache_dir, model="test-model")
    changed = make_card(make_threat())
    changed["threats"][0]["texts"]["ru"]["summary"] = "Новая формулировка"
    card_path.write_text(json.dumps(changed), encoding="utf-8")

    after_change = FakeClient()
    run(card_path, client=after_change, cache_dir=cache_dir, model="test-model")
    assert after_change.calls


def test_run_dry_run_writes_no_card_and_no_cache(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    card_path = tmp_path / "fire.json"
    original = json.dumps(make_card(make_threat()))
    card_path.write_text(original, encoding="utf-8")
    cache_dir = tmp_path / ".cache"

    assert run(card_path, dry_run=True, client=FakeClient(), cache_dir=cache_dir, model="test-model") == 1

    assert card_path.read_text(encoding="utf-8") == original
    assert not cache_dir.exists()
    printed = json.loads(capsys.readouterr().out)
    assert printed["threats"][0]["quiz"]["ru"][0]["id"] == "q1"


def test_run_writes_atomically_to_tmp_path(tmp_path: Path) -> None:
    card_path = tmp_path / "fire.json"
    card_path.write_text(json.dumps(make_card(make_threat())), encoding="utf-8")

    filled = run(card_path, client=FakeClient())

    assert filled == 1
    stored = json.loads(card_path.read_text(encoding="utf-8"))
    assert stored["version"] == 2
    assert [q["id"] for q in stored["threats"][0]["quiz"]["ru"]] == ["q1", "q2", "q3"]
    assert [p.name for p in tmp_path.iterdir()] == ["fire.json"]


def test_run_leaves_file_untouched_on_failure(tmp_path: Path) -> None:
    card_path = tmp_path / "fire.json"
    original = json.dumps(make_card(make_threat()))
    card_path.write_text(original, encoding="utf-8")

    cache_dir = tmp_path / ".cache"
    with pytest.raises(ValueError):
        run(card_path, client=FakeClient({"questions": []}), cache_dir=cache_dir)

    assert card_path.read_text(encoding="utf-8") == original
    assert {p.name for p in tmp_path.iterdir()} == {"fire.json"}


def test_run_check_reports_gaps_without_client(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    card_path = tmp_path / "fire.json"
    card_path.write_text(json.dumps(make_card(make_threat("fire-a"), make_threat("fire-b", quiz={"ru": []}))), "utf-8")

    assert run(card_path, check=True) == 2

    out = capsys.readouterr().out
    assert "fire-a" in out and "fire-b" in out
    assert "2 of 2" in out


def test_run_complete_card_needs_no_client(tmp_path: Path) -> None:
    quiz = {"ru": [{"id": "q1", "prompt": "p", "options": list("abcd"), "answer": 0, "explanation": "e"}]}
    card_path = tmp_path / "fire.json"
    card_path.write_text(json.dumps(make_card(make_threat(quiz=quiz))), "utf-8")

    assert run(card_path, client=ExplodingClient()) == 0
    assert json.loads(card_path.read_text(encoding="utf-8"))["version"] == 1


def test_write_card_replaces_target(tmp_path: Path) -> None:
    path = tmp_path / "card.json"
    path.write_text("old", encoding="utf-8")

    write_card(path, make_card(make_threat()))

    assert json.loads(path.read_text(encoding="utf-8"))["threats"][0]["slug"] == "fire-home"
    assert list(tmp_path.iterdir()) == [path]


def test_http_client_uses_completions_endpoint_and_bearer_token() -> None:
    seen: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        seen["auth"] = request.headers.get("authorization")
        seen["model"] = json.loads(request.content)["model"]
        return httpx.Response(200, json={"choices": [{"message": {"content": json.dumps(QUESTIONS)}}]})

    http = httpx.Client(transport=httpx.MockTransport(handler))
    client = OpenAICompatibleClient("https://llm.example/v1/", "test-model", "secret-key", http=http)

    raw = client.chat([{"role": "user", "content": "hi"}])

    assert json.loads(raw)["questions"]
    assert seen == {
        "url": "https://llm.example/v1/chat/completions",
        "auth": "Bearer secret-key",
        "model": "test-model",
    }


def test_main_check_mode_exit_code(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    card_path = tmp_path / "fire.json"
    card_path.write_text(json.dumps(make_card(make_threat())), encoding="utf-8")

    assert main(["--card", str(card_path), "--check"]) == 1
    assert "fire-home" in capsys.readouterr().out


def test_main_passes_count_force_to_run(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    card_path = tmp_path / "fire.json"
    card_path.write_text(json.dumps(make_card(make_threat())), encoding="utf-8")
    seen: dict[str, Any] = {}

    def fake_run(path: Path, **kwargs: Any) -> int:
        seen.update(kwargs)
        return 0

    monkeypatch.setattr("scripts.gen_quiz.run", fake_run)

    assert main(["--card", str(card_path), "--count", "5", "--force"]) == 0
    assert seen["count"] == 5
    assert seen["force"] is True
