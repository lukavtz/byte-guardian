"""Load prompt skills from ``<name>/SKILL.md`` folders and render them.

Each skill is a directory with a ``SKILL.md`` and optional ``references/*.md``.
Only the frontmatter of references is read at load time; a reference body is
read when the skill is selected and the question matches one of its triggers.
A malformed skill is skipped with one warning and never breaks an answer.
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

logger = logging.getLogger(__name__)

MAX_SKILLS = 3
BODY_CHARS = 240
BUDGET_CHARS = 900
REF_INDEX_CHARS = 120
REF_RESERVE = 110
BASELINE_SKILL = "formatting"
FALLBACK_KEEP = 40
SKILL_MAX_BYTES = 2048
MAX_SUFFIX = 8

_NAME_RE = re.compile(r"[a-z0-9-]+")
_TOKEN_RE = re.compile(r"[^\W_]+", re.UNICODE)
_FENCE = "---"


@dataclass(frozen=True, slots=True)
class Reference:
    path: str
    triggers: tuple[str, ...]
    summary: str
    source: Path


@dataclass(frozen=True, slots=True)
class Skill:
    name: str
    description: str
    triggers: tuple[str, ...]
    priority: int
    locales: tuple[str, ...]
    body: str
    references: tuple[Reference, ...]
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


def _split_frontmatter(path: Path, text: str) -> tuple[dict[str, str | tuple[str, ...]], str] | None:
    lines = text.splitlines()
    if not lines or lines[0].strip() != _FENCE:
        logger.warning("skill without frontmatter, skipped: %s", path)
        return None
    try:
        end = next(index for index, line in enumerate(lines[1:], start=1) if line.strip() == _FENCE)
    except StopIteration:
        logger.warning("skill frontmatter never closed, skipped: %s", path)
        return None
    try:
        meta = _parse_frontmatter(lines[1:end])
    except ValueError as error:
        logger.warning("skill frontmatter invalid, skipped: %s (%s)", path, error)
        return None
    return meta, "\n".join(lines[end + 1 :]).strip()


def _parse_reference(path: Path) -> Reference | None:
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as error:
        logger.warning("reference unreadable, skipped: %s (%s)", path, error)
        return None
    meta, _ = _split_frontmatter(path, text) or ({}, "")
    triggers = meta.get("triggers") or meta.get("trigger") or ()
    if not isinstance(triggers, tuple) or not triggers:
        logger.warning("reference without triggers, skipped: %s", path)
        return None
    summary = meta.get("summary") or meta.get("description") or ""
    return Reference(
        path=path.name,
        triggers=triggers,
        summary=summary if isinstance(summary, str) else "",
        source=path,
    )


def _parse_skill(directory: Path) -> Skill | None:
    path = directory / "SKILL.md"
    try:
        raw = path.read_bytes()
    except OSError as error:
        logger.warning("skill unreadable, skipped: %s (%s)", path, error)
        return None
    if len(raw) > SKILL_MAX_BYTES:
        logger.warning("skill body over %d bytes, skipped: %s", SKILL_MAX_BYTES, path)
        return None
    split = _split_frontmatter(path, raw.decode("utf-8", "replace"))
    if split is None:
        return None
    meta, body = split
    name = meta.get("name")
    description = meta.get("description")
    triggers = meta.get("triggers")
    if not isinstance(name, str) or not isinstance(description, str):
        logger.warning("skill lacks name or description, skipped: %s", path)
        return None
    if not isinstance(triggers, tuple) or not triggers or not body:
        logger.warning("skill lacks triggers or body, skipped: %s", path)
        return None
    if name != directory.name or not _NAME_RE.fullmatch(name):
        logger.warning("skill name %r does not match its folder, skipped: %s", name, path)
        return None
    try:
        priority = int(str(meta.get("priority", "0")))
    except ValueError:
        logger.warning("skill priority is not an integer, skipped: %s", path)
        return None
    locales = meta.get("locales", ())
    if not isinstance(locales, tuple):
        locales = ()
    references: list[Reference] = []
    for reference_path in sorted((directory / "references").glob("*.md")):
        reference = _parse_reference(reference_path)
        if reference is not None:
            references.append(reference)
    return Skill(
        name=name,
        description=description,
        triggers=triggers,
        priority=priority,
        locales=locales,
        body=body,
        references=tuple(references),
        source=path,
    )


@lru_cache(maxsize=4)
def load_skills(directory: Path | None = None) -> tuple[Skill, ...]:
    """Every valid skill, sorted by name. Cached until deploy."""
    root = directory or Path(__file__).parent
    skills: dict[str, Skill] = {}
    for folder in sorted(root.iterdir() if root.is_dir() else ()):
        if not folder.is_dir() or folder.name == "references" or folder.name.startswith(("__", ".")):
            continue
        skill = _parse_skill(folder)
        if skill is not None:
            skills.setdefault(skill.name, skill)
    return tuple(skills[name] for name in sorted(skills))


def _normalise(text: str) -> tuple[str, list[str]]:
    tokens = _TOKEN_RE.findall(text.casefold().replace("ё", "е"))
    return " ".join(tokens), tokens


def _stem_hit(stem: str, tokens: list[str]) -> bool:
    if len(stem) < 3:
        return any(token == stem for token in tokens)
    return any(token.startswith(stem) and len(token) - len(stem) <= MAX_SUFFIX for token in tokens)


def _hits(trigger: str, normalised: str, tokens: list[str]) -> bool:
    trigger_norm, trigger_tokens = _normalise(trigger)
    if any("\u3400" <= char <= "\u9fff" for char in trigger_norm):
        return trigger_norm in normalised
    if not trigger_tokens:
        return False
    if len(trigger_tokens) == 1:
        return _stem_hit(trigger_tokens[0], tokens)
    if f" {trigger_norm} " in f" {normalised} ":
        return True
    head, last = trigger_tokens[:-1], trigger_tokens[-1]
    width = len(head)
    return any(
        tokens[index : index + width] == head and _stem_hit(last, [tokens[index + width]])
        for index in range(max(0, len(tokens) - width))
        if index + width < len(tokens)
    )


def _score(skill: Skill, normalised: str, tokens: list[str]) -> int:
    total = 0
    for trigger in skill.triggers:
        if _hits(trigger, normalised, tokens):
            total += 2 if len(_normalise(trigger)[1]) > 1 else 1
    return total


def select_skills(question: str, locale: str, limit: int = MAX_SKILLS) -> tuple[Skill, ...]:
    """The best skills for ``question``: score, then priority, then name.

    ``formatting`` is always injected. Low-priority fallbacks (clarify,
    off-topic, content-gap) are dropped when a real safety skill matched.
    """
    skills = load_skills()
    cap = max(1, min(limit, MAX_SKILLS))
    normalised, tokens = _normalise(question)
    scored: list[tuple[int, Skill]] = []
    for skill in skills:
        if skill.locales and locale not in skill.locales:
            continue
        score = _score(skill, normalised, tokens)
        if skill.name == BASELINE_SKILL:
            score += 1
        if score:
            scored.append((score, skill))
    if not scored:
        return tuple(skill for skill in skills if skill.name == BASELINE_SKILL)
    topical = [pair for pair in scored if pair[1].name != BASELINE_SKILL]
    if any(skill.priority >= FALLBACK_KEEP for _, skill in topical):
        scored = [
            pair
            for pair in scored
            if pair[1].name == BASELINE_SKILL or pair[1].priority >= FALLBACK_KEEP
        ]
    scored.sort(key=lambda pair: (-pair[0], -pair[1].priority, pair[1].name))
    chosen = [skill for _, skill in scored if skill.name != BASELINE_SKILL][: cap - 1]
    chosen.extend(skill for _, skill in scored if skill.name == BASELINE_SKILL)
    return tuple(chosen[:cap])


def references_for(skill: Skill, question: str) -> tuple[Reference, ...]:
    """Reference metadata whose triggers fit the question; bodies stay on disk."""
    normalised, tokens = _normalise(question)
    return tuple(
        reference
        for reference in skill.references
        if any(_hits(trigger, normalised, tokens) for trigger in reference.triggers)
    )


@lru_cache(maxsize=64)
def _reference_text(path: Path) -> str:
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as error:
        logger.warning("reference unreadable at selection: %s (%s)", path, error)
        return ""
    meta, body = _split_frontmatter(path, text) or ({}, "")
    summary = meta.get("summary") or meta.get("description") or ""
    text = summary if isinstance(summary, str) and summary else body
    return " ".join(text.split())


def _clip(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    if limit <= 1:
        return "…"[:limit]
    cut = text[: limit - 1].rstrip()
    head, space, _ = cut.rpartition(" ")
    if space and len(head) >= limit // 2:
        cut = head.rstrip()
    return cut + "…"


def _skill_wrapper(name: str) -> int:
    return len(f'<skill name="{name}">\n\n</skill>')


def _ref_line(reference: Reference, skill: Skill, excerpt_chars: int) -> str:
    excerpt = _clip(_reference_text(reference.source), excerpt_chars)
    return f'<ref skill="{skill.name}" path="{reference.path}">{excerpt}</ref>'


def format_skills(
    question: str,
    locale: str,
    *,
    limit: int = MAX_SKILLS,
    budget_chars: int = BUDGET_CHARS,
) -> str:
    """``<skill>`` blocks plus a short ``<ref>`` index, within ``budget_chars``."""
    skills = select_skills(question, locale, limit=limit)
    pairs = [(skill, reference) for skill in skills for reference in references_for(skill, question)]
    body_room = budget_chars - (REF_RESERVE if pairs else 0)
    blocks: list[str] = []
    used = 0
    for skill in skills:
        separator = 2 if blocks else 0
        wrapper = _skill_wrapper(skill.name)
        room = body_room - used - separator - wrapper
        if room < 32:
            break
        body = _clip(skill.body, min(BODY_CHARS, room))
        block = f'<skill name="{skill.name}">\n{body}\n</skill>'
        blocks.append(block)
        used += separator + len(block)
    for skill, reference in pairs:
        separator = 1 if blocks else 0
        wrapper = len(f'<ref skill="{skill.name}" path="{reference.path}"></ref>')
        room = budget_chars - used - separator - wrapper
        if room < 8:
            continue
        line = _ref_line(reference, skill, min(REF_INDEX_CHARS, room))
        blocks.append(line)
        used += separator + len(line)
    return "\n".join(blocks)