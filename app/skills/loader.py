"""Load prompt skills from ``*.md`` files and render them into the system prompt.

Frontmatter is JSON-compatible YAML, so ``json.loads`` parses it with no PyYAML
dependency. A malformed file is skipped with one warning: a bad skill must never
break an answer.
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

logger = logging.getLogger(__name__)

MAX_SKILLS = 2
BODY_CHARS = 600
BUDGET_CHARS = 1200
BASELINE_SKILL = "formatting"

_NAME_RE = re.compile(r"[a-z0-9-]+")
_TOKEN_RE = re.compile(r"[^\W_]+", re.UNICODE)
_FENCE = "---"


@dataclass(frozen=True, slots=True)
class Skill:
    name: str
    description: str
    triggers: tuple[str, ...]
    body: str
    source: Path


def _parse_frontmatter(lines: list[str]) -> dict[str, str | tuple[str, ...]]:
    meta: dict[str, str | tuple[str, ...]] = {}
    for line in lines:
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        key, sep, raw = line.partition(":")
        key, raw = key.strip(), raw.strip()
        if not sep or not key or not raw:
            raise ValueError(f"bad frontmatter line: {line!r}")
        if raw.startswith("["):
            parsed = json.loads(raw)
            if not isinstance(parsed, list):
                raise ValueError(f"{key} is not a list")
            meta[key] = tuple(str(item) for item in parsed)
        elif raw.startswith('"'):
            meta[key] = str(json.loads(raw))
        else:
            meta[key] = raw
    return meta


def _parse_file(path: Path, stem: str) -> Skill | None:
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as error:
        logger.warning("skill unreadable, skipped: %s (%s)", path.name, error)
        return None
    lines = text.splitlines()
    if not lines or lines[0].strip() != _FENCE:
        logger.warning("skill without frontmatter, skipped: %s", path.name)
        return None
    try:
        end = next(index for index, line in enumerate(lines[1:], start=1) if line.strip() == _FENCE)
    except StopIteration:
        logger.warning("skill frontmatter never closed, skipped: %s", path.name)
        return None
    try:
        meta = _parse_frontmatter(lines[1:end])
    except ValueError as error:
        logger.warning("skill frontmatter invalid, skipped: %s (%s)", path.name, error)
        return None
    body = "\n".join(lines[end + 1 :]).strip()
    name = meta.get("name")
    description = meta.get("description")
    triggers = meta.get("triggers")
    if not isinstance(name, str) or not isinstance(description, str):
        logger.warning("skill lacks name or description, skipped: %s", path.name)
        return None
    if not isinstance(triggers, tuple) or not triggers or not body:
        logger.warning("skill lacks triggers or body, skipped: %s", path.name)
        return None
    if name != stem or not _NAME_RE.fullmatch(name):
        logger.warning("skill name %r does not match its file, skipped: %s", name, path.name)
        return None
    return Skill(
        name=name,
        description=description,
        triggers=triggers,
        body=body,
        source=path,
    )


@lru_cache(maxsize=4)
def load_skills(directory: Path | None = None) -> list[Skill]:
    """Every valid skill in ``directory``, sorted by name. Cached until deploy."""
    root = directory or Path(__file__).parent
    skills: dict[str, Skill] = {}
    for path in sorted(root.glob("*.md")):
        if path.stem.casefold() == "readme":
            continue
        skill = _parse_file(path, path.stem)
        if skill is not None:
            skills.setdefault(skill.name, skill)
    return [skills[name] for name in sorted(skills)]


def _normalise(text: str) -> tuple[str, list[str]]:
    tokens = _TOKEN_RE.findall(text.casefold().replace("ё", "е"))
    return " ".join(tokens), tokens


def _hits(trigger: str, normalised: str, tokens: list[str]) -> bool:
    trigger_norm, trigger_tokens = _normalise(trigger)
    if not trigger_tokens:
        return False
    if len(trigger_tokens) == 1:
        return any(token.startswith(trigger_norm) for token in tokens)
    return trigger_norm in normalised


def select_skills(question: str, locale: str, limit: int = 2) -> list[Skill]:
    """Skills whose triggers fit the question; ``formatting`` is always included.

    ``locale`` is reserved for localized triggers: matching is language-agnostic
    because every skill already carries both Russian and English triggers.
    """
    del locale  # reserved: matching is language-agnostic today
    skills = load_skills()
    normalised, tokens = _normalise(question)
    scored: list[tuple[int, Skill]] = []
    for skill in skills:
        score = sum(1 for trigger in skill.triggers if _hits(trigger, normalised, tokens))
        if skill.name == BASELINE_SKILL:
            score += 1
        if score:
            scored.append((score, skill))
    if not scored:
        return [skill for skill in skills if skill.name == BASELINE_SKILL]
    # Ties end with the baseline skill so a topical skill leads the block.
    scored.sort(key=lambda pair: (-pair[0], pair[1].name == BASELINE_SKILL, pair[1].name))
    return [skill for _, skill in scored[: max(1, min(limit, MAX_SKILLS))]]


def _clip_body(body: str, limit: int) -> str:
    if len(body) <= limit:
        return body
    if limit <= 1:
        return "…"[:limit]
    cut = body[: limit - 1].rstrip()
    head, space, _ = cut.rpartition(" ")
    if space and len(head) >= limit // 2:
        cut = head.rstrip()
    return cut + "…"


def _block(skill: Skill, body: str) -> str:
    return f'<skill name="{skill.name}">\n{body}\n</skill>'


def render(skills: list[Skill], budget_chars: int = BUDGET_CHARS) -> str:
    """Wrap up to two skills in ``<skill>`` blocks within ``budget_chars`` total."""
    blocks: list[str] = []
    used = 0
    for skill in skills[:MAX_SKILLS]:
        room = budget_chars - used - (2 if blocks else 0)
        wrapper = len(_block(skill, ""))
        if room <= wrapper + 1:
            break
        body = _clip_body(skill.body, min(BODY_CHARS, room - wrapper))
        block = _block(skill, body)
        blocks.append(block)
        used += len(block) + (2 if len(blocks) > 1 else 0)
    return "\n\n".join(blocks)