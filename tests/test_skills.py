from __future__ import annotations

from pathlib import Path

import pytest

from app.services import llm as service
from app.services.prompt import SYSTEM_PROMPT
from app.skills import loader

NAMES = ["first-aid", "formatting", "group-chat", "persona", "sources", "triage"]


# --- loader ---------------------------------------------------------------


def test_load_skills_returns_the_six_committed_skills() -> None:
    skills = loader.load_skills()
    assert [skill.name for skill in skills] == NAMES
    assert all(skill.triggers and skill.body for skill in skills)


def test_load_skills_is_cached_and_sorted() -> None:
    assert loader.load_skills() is loader.load_skills()
    assert [skill.name for skill in loader.load_skills()] == sorted(NAMES)


def test_malformed_frontmatter_is_skipped_without_raising(tmp_path: Path) -> None:
    (tmp_path / "no-fence.md").write_text("just a body\n", encoding="utf-8")
    (tmp_path / "bad-json.md").write_text(
        "---\nname: bad-json\ndescription: \"x\"\ntriggers: [oops\n---\nbody\n",
        encoding="utf-8",
    )
    (tmp_path / "wrong-name.md").write_text(
        "---\nname: other\ndescription: \"x\"\ntriggers: [\"a\"]\n---\nbody\n",
        encoding="utf-8",
    )
    (tmp_path / "good.md").write_text(
        '---\nname: good\ndescription: "does a thing"\ntriggers: ["пожар"]\n---\n\n# Body\n',
        encoding="utf-8",
    )
    (tmp_path / "README.md").write_text("---\nname: README\n---\n", encoding="utf-8")

    skills = loader.load_skills(tmp_path)

    assert [skill.name for skill in skills] == ["good"]
    assert skills[0].triggers == ("пожар",)


# --- selection ------------------------------------------------------------


def test_selection_matches_triggers() -> None:
    assert loader.select_skills("человек без сознания", "ru")[0].name == "triage"
    assert loader.select_skills("привет", "ru")[0].name == "persona"
    assert loader.select_skills("дай ссылку", "ru")[0].name == "sources"
    assert loader.select_skills("Пожар в общаге", "ru")[0].name == "triage"


def test_unknown_question_returns_only_the_baseline() -> None:
    assert [skill.name for skill in loader.select_skills("квазар", "ru")] == ["formatting"]


def test_selection_clamps_to_two_skills() -> None:
    chosen = loader.select_skills("пожар кровотечение ссылку привет", "ru", limit=10)
    assert 1 <= len(chosen) <= loader.MAX_SKILLS
    assert chosen[0].name != "formatting"


def test_baseline_is_always_present() -> None:
    for question in ("пожар", "привет", "дай ссылку", "квазар"):
        assert "formatting" in [skill.name for skill in loader.select_skills(question, "ru")]


# --- rendering ------------------------------------------------------------


def test_render_wraps_bodies_within_per_skill_limit() -> None:
    skills = loader.load_skills()[:2]
    text = loader.render(skills)
    for skill in skills:
        assert f'<skill name="{skill.name}">' in text
    for body in (skill.body for skill in skills):
        assert len(body) <= 600


def test_render_respects_the_global_budget() -> None:
    skills = loader.load_skills()
    assert len(loader.render(skills)) <= loader.BUDGET_CHARS
    assert len(loader.render(skills, budget_chars=120)) <= 120
    assert loader.render(skills, budget_chars=1) == ""


# --- prompt wiring --------------------------------------------------------


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