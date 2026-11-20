from __future__ import annotations

import logging
from pathlib import Path

import pytest

from app.services import llm as service
from app.services.prompt import SYSTEM_PROMPT
from app.skills import loader

NAMES = [
    "admin-help",
    "clarify",
    "content-gap",
    "cyber-fraud",
    "emergency-call",
    "evacuation",
    "fire-smoke",
    "first-aid",
    "formatting",
    "gas-leak",
    "group-chat",
    "mental-support",
    "off-topic",
    "persona",
    "radiation",
    "sources",
    "study-help",
    "triage",
]


def _write_skill(root: Path, name: str, body: str = "# Body\n", extra: str = "") -> Path:
    folder = root / name
    folder.mkdir(parents=True)
    (folder / "SKILL.md").write_text(
        f'---\nname: {name}\ndescription: "does a thing"\ntriggers: ["пожар"]\npriority: 10\n{extra}---\n\n{body}',
        encoding="utf-8",
    )
    return folder


# --- loading ---------------------------------------------------------------


def test_load_skills_returns_every_committed_skill() -> None:
    skills = loader.load_skills()
    assert [skill.name for skill in skills] == NAMES
    assert all(skill.triggers and skill.body for skill in skills)
    assert all(len(skill.body.encode()) <= loader.SKILL_MAX_BYTES for skill in skills)


def test_load_skills_is_cached_and_sorted() -> None:
    assert loader.load_skills() is loader.load_skills()
    assert [skill.name for skill in loader.load_skills()] == sorted(NAMES)


def test_frontmatter_fields_are_parsed() -> None:
    skills = {skill.name: skill for skill in loader.load_skills()}
    assert skills["triage"].priority == 85
    assert "ru" in skills["triage"].locales
    assert skills["study-help"].locales == ("ru", "en", "tt")
    assert "жизнедеятельности" in " ".join(skills["study-help"].triggers)
    assert skills["emergency-call"].references


def test_malformed_skills_are_skipped_without_raising(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    (tmp_path / "no-fence").mkdir()
    (tmp_path / "no-fence" / "SKILL.md").write_text("just a body\n", encoding="utf-8")
    (tmp_path / "bad-json").mkdir()
    (tmp_path / "bad-json" / "SKILL.md").write_text(
        '---\nname: bad-json\ndescription: "x"\ntriggers: [oops\n---\nbody\n', encoding="utf-8"
    )
    (tmp_path / "wrong-name").mkdir()
    (tmp_path / "wrong-name" / "SKILL.md").write_text(
        '---\nname: other\ndescription: "x"\ntriggers: ["a"]\n---\nbody\n', encoding="utf-8"
    )
    (tmp_path / "too-big").mkdir()
    (tmp_path / "too-big" / "SKILL.md").write_text(
        '---\nname: too-big\ndescription: "x"\ntriggers: ["a"]\n---\n' + "я" * 3000,
        encoding="utf-8",
    )
    _write_skill(tmp_path, "good")

    with caplog.at_level(logging.WARNING, logger=loader.__name__):
        skills = loader.load_skills(tmp_path)
        loader.load_skills(tmp_path)

    assert [skill.name for skill in skills] == ["good"]
    assert skills[0].triggers == ("пожар",)
    assert sum("no-fence" in record.message for record in caplog.records) == 1


def test_reference_without_triggers_is_ignored(tmp_path: Path) -> None:
    folder = _write_skill(tmp_path, "good")
    references = folder / "references"
    references.mkdir()
    (references / "orphan.md").write_text("---\nsummary: \"x\"\n---\nbody", encoding="utf-8")
    (references / "bound.md").write_text(
        '---\ntriggers: ["пожар"]\nsummary: "short index"\n---\nbody', encoding="utf-8"
    )

    skills = loader.load_skills(tmp_path)

    assert [reference.path for reference in skills[0].references] == ["bound.md"]


# --- selection -------------------------------------------------------------


@pytest.mark.parametrize(
    ("question", "expected"),
    [
        ("Пожар в общежитии", "fire-smoke"),
        ("человек без сознания", "triage"),
        ("дай ссылку", "sources"),
        ("Помоги", "clarify"),
        ("привет", "persona"),
    ],
)
def test_first_skill_matches_the_question(question: str, expected: str) -> None:
    assert loader.select_skills(question, "ru")[0].name == expected


def test_morphology_matches_ru_and_tt_without_substrings() -> None:
    assert loader.select_skills("в пожаре застрял", "ru")[0].name == "fire-smoke"
    assert loader.select_skills("газовый баллон", "ru")[0].name == "gas-leak"
    assert loader.select_skills("обжёг руку", "ru")[0].name == "first-aid"
    assert loader.select_skills("янгыннан чыгарга", "tt")[0].name == "fire-smoke"
    assert loader.select_skills("кошка", "ru") == loader.select_skills("кошка", "ru")
    assert [skill.name for skill in loader.select_skills("кошка", "ru")] == ["formatting"]


@pytest.mark.parametrize(
    ("question", "locale", "expected"),
    [
        ("有人昏迷，不呼吸，打急救", "zh", "triage"),
        ("Ашыгыч ярдәм, кеше аңсыз", "tt", "triage"),
        ("这是来源", "zh", "sources"),
        ("чыганак кайда", "tt", "sources"),
        ("群聊里怎么回答", "zh", "group-chat"),
        ("төркемдә ничек җавап бирәм", "tt", "group-chat"),
    ],
)
def test_localized_triggers_select_skills(question: str, locale: str, expected: str) -> None:
    assert expected in [skill.name for skill in loader.select_skills(question, locale)]


def test_cjk_trigger_inside_sentence_matches() -> None:
    assert "sources" in [skill.name for skill in loader.select_skills("这是来源问题", "zh")]
    assert "group-chat" in [skill.name for skill in loader.select_skills("群聊怎么回答", "zh")]


def test_unknown_question_returns_only_the_baseline() -> None:
    assert [skill.name for skill in loader.select_skills("квазар", "ru")] == ["formatting"]


def test_selection_clamps_to_three_skills() -> None:
    chosen = loader.select_skills("пожар кровотечение ссылку привет", "ru", limit=10)
    assert 1 <= len(chosen) <= loader.MAX_SKILLS
    assert chosen[0].name != "formatting"


def test_baseline_is_always_present() -> None:
    for question in ("пожар", "привет", "дай ссылку", "квазар"):
        assert "formatting" in [skill.name for skill in loader.select_skills(question, "ru")]


def test_fallbacks_yield_to_a_topical_skill() -> None:
    names = [skill.name for skill in loader.select_skills("Помоги при пожаре", "ru")]
    assert "fire-smoke" in names
    assert "clarify" not in names


def test_priority_breaks_equal_scores() -> None:
    names = [skill.name for skill in loader.select_skills("пожар", "ru")]
    assert names[:2] == ["fire-smoke", "emergency-call"]


def test_locale_filter_drops_unavailable_skills(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _write_skill(tmp_path, "ru-only", extra='locales: ["ru"]\n')
    skill = loader.load_skills(tmp_path)[0]
    monkeypatch.setattr(loader, "load_skills", lambda directory=None: (skill,))

    assert skill.locales == ("ru",)
    assert loader.select_skills("пожар", "en") == ()
    assert loader.select_skills("пожар", "ru") == (skill,)


def test_selection_is_deterministic() -> None:
    first = [skill.name for skill in loader.select_skills("пожар в общежитии", "ru")]
    for _ in range(5):
        assert [skill.name for skill in loader.select_skills("пожар в общежитии", "ru")] == first


# --- references ------------------------------------------------------------


def test_reference_index_appears_only_on_a_trigger_match() -> None:
    fire = loader.format_skills("пожар в общежитии", "ru")
    quiet = loader.format_skills("обжёг руку кипятком", "ru")

    assert '<ref skill="fire-smoke" path="extinguishers.md">' in fire
    assert "Огнетушители" in fire
    assert "<ref" not in quiet


def test_reference_body_is_not_injected() -> None:
    fire = loader.format_skills("пожар", "ru")
    assert "ОП — порошковый" not in fire
    excerpt = fire.split('<ref skill="fire-smoke" path="extinguishers.md">', 1)[1].split("</ref>", 1)[0]
    assert 0 < len(excerpt) <= loader.REF_INDEX_CHARS


# --- rendering -------------------------------------------------------------


def test_format_wraps_bodies_within_per_skill_limit() -> None:
    text = loader.format_skills("человек без сознания, не дышит", "ru")
    assert '<skill name="triage">' in text
    assert '<skill name="first-aid">' in text
    for body in ("Триаж", "Первая помощь"):
        assert body in text


def test_format_respects_the_global_budget() -> None:
    assert len(loader.format_skills("пожар кровотечение ссылку привет", "ru")) <= loader.BUDGET_CHARS
    assert len(loader.format_skills("пожар", "ru", budget_chars=120)) <= 120
    assert loader.format_skills("пожар", "ru", budget_chars=1) == ""


def test_format_never_exceeds_three_skills() -> None:
    text = loader.format_skills("пожар кровотечение ссылку привет", "ru", limit=10)
    assert text.count("<skill name=") <= loader.MAX_SKILLS <= 3


# --- prompt wiring ---------------------------------------------------------


def test_build_messages_injects_selected_skills_after_the_prompt() -> None:
    messages = service.build_messages("человек без сознания", "ru", [])
    system = messages[0]["content"]

    assert system.startswith(SYSTEM_PROMPT)
    assert '<skill name="triage">' in system
    assert len(system) - len(SYSTEM_PROMPT) <= loader.BUDGET_CHARS


@pytest.mark.parametrize("question", ["привет", "спасибо", "как дела?"])
def test_build_messages_skips_skills_for_smalltalk(question: str) -> None:
    messages = service.build_messages(question, "ru", [])
    assert messages[0]["content"] == SYSTEM_PROMPT


# --- golden eval -----------------------------------------------------------

GOLDEN: list[tuple[str, set[str]]] = [
    ("Человек без сознания, не дышит", {"triage", "first-aid", "formatting"}),
    ("Пожар в общежитии, что делать", {"fire-smoke", "emergency-call", "formatting"}),
    ("Пахнет газом в квартире", {"gas-leak", "emergency-call", "formatting"}),
    ("Как вызвать скорую", {"emergency-call", "formatting"}),
    ("Ребёнок проглотил таблетки", {"triage", "first-aid", "formatting"}),
    ("Сильное кровотечение из ноги", {"triage", "first-aid", "formatting"}),
    ("Обжёг руку кипятком", {"first-aid", "formatting"}),
    ("Как выйти из горящего здания", {"evacuation", "fire-smoke", "formatting"}),
    ("На улице радиация, что делать", {"radiation", "formatting"}),
    ("Мошенники выманили код из смс", {"cyber-fraud", "formatting"}),
    ("Паническая атака, не могу дышать", {"mental-support", "formatting"}),
    ("Умер близкий, как справиться", {"mental-support", "formatting"}),
    ("Какие вопросы на экзамене по БЖД", {"study-help", "formatting"}),
    ("Помоги с билетом по безопасности жизнедеятельности", {"study-help", "formatting"}),
    ("Привет", {"persona", "formatting"}),
    ("Какая погода завтра", {"off-topic", "formatting"}),
    ("Помоги", {"clarify", "formatting"}),
    ("Как пользоваться админкой", {"admin-help", "formatting"}),
    ("Ты не знаешь ответ?", {"content-gap", "formatting"}),
    ("Где почитать про хлор", {"sources", "formatting"}),
]


def test_golden_selection_scores_at_least_eighteen_of_twenty() -> None:
    passed = 0
    failures: list[str] = []
    for question, expected in GOLDEN:
        actual = {skill.name for skill in loader.select_skills(question, "ru")}
        if actual == expected:
            passed += 1
        else:
            failures.append(f"{question!r}: want {sorted(expected)}, got {sorted(actual)}")
    assert passed >= 18, "\n".join(failures)
    assert len(GOLDEN) == 20