# Chat skills

Prompt fragments for Байт. One file = one job. The loader is **not implemented
yet**; this is the contract for wiring after the prompt/handler changes land.

| File | Job |
|---|---|
| `persona.md` | Voice, openings, closings, small talk, not knowing |
| `triage.md` | Emergency vs ordinary; when to say 112; one question max |
| `first-aid.md` | Answer shape: now → 2–3 steps → ban → 112; never diagnose |
| `group-chat.md` | Short group answers, mention etiquette, no walls |
| `sources.md` | Cards first, web second, max 2 URLs, no invented links |
| `formatting.md` | Telegram HTML limits, 900 chars, no markdown, no emoji |

## File format

```markdown
---
name: triage
description: "What it does. When it applies."
triggers: ["112", "не дышит", "emergency"]
---

# Body
Imperative rules, examples, anti-patterns.
```

- `name` = file stem, lowercase, hyphens allowed.
- `description` is third person and names both the job and the trigger.
- `triggers` is a JSON array — also valid YAML, so stdlib `json.loads` parses it
  and no PyYAML dependency is needed.
- Body is Russian, like the system prompt. The model answers in `locale`.
- Hard limit 2 KB per file. The first 600 characters are what reaches the model,
  so rules go first, examples and anti-patterns after them.

## Loader API (`app/skills/loader.py`)

```python
@dataclass(frozen=True, slots=True)
class Skill:
    name: str
    description: str
    triggers: tuple[str, ...]
    body: str
    source: Path

def load_skills(directory: Path | None = None) -> list[Skill]: ...
def select_skills(question: str, locale: str, limit: int = 2) -> list[Skill]: ...
def render(skills: list[Skill]) -> str: ...
```

### `load_skills`

- Default directory: `Path(__file__).parent`; read `*.md`, skip `README.md`.
- Split frontmatter on the first two `---` lines. Missing `name`,
  `description`, `triggers` or body: skip the file, log one warning, never raise.
- Require `name == path.stem`; reject names that are not
  `[a-z0-9-]+`. Deduplicate by name.
- Sort by name. Cache with `lru_cache`; skills change only on deploy.

### `select_skills`

- Normalize: casefold, `ё` → `е`, split on non-alphanumerics.
- A trigger hits when a token starts with it (so `пожар` matches `пожаре`) or
  when a multi-word trigger is a substring of the normalized question.
- Score = number of distinct triggers hit. `formatting` gets +1 baseline because
  it applies to every answer.
- Order by score desc, then name. Clamp `limit` to `MAX_SKILLS = 2` regardless of
  the argument.
- No hits: return `[formatting]`.
- `locale` is passed through for future localized triggers; matching is
  language-agnostic today because triggers already include en terms.
- Questions only score. User text is never interpolated into a skill.

### `render`

```
<skill name="triage">
...body, first 600 chars, cut on a word boundary with «…»...
</skill>
```

- Join with a blank line. Max 2 skills (extra entries dropped), 600 chars each,
  1200 chars total (≈400–600 tokens). Count characters, not bytes.
- Append to the system content after `SYSTEM_PROMPT`; the user message stays
  exactly the question plus cards plus the UNTRUSTED web block.
- Never select on web content: it is untrusted data, not a trigger source.

## Wiring (later, owner: ChatBrain)

`build_messages()` in `app/services/llm.py` appends `render(select_skills(question, locale))`
to the system string. Do not edit `prompt.py` or handlers from this directory.

## Authoring checklist

1. One job per skill; the name states the activity.
2. Description is third person and says what + when; triggers use the user's words.
3. Body is imperative and short; no theory the model already knows.
4. Give 2–3 bad → good examples; examples beat explanation.
5. Name failure modes in a `## Не делай` section.
6. Budget: 2 KB source, 600 chars injected; front-load the rules.
7. Progressive disclosure: details live in one-level-deep references, not in the body.
8. Match freedom to fragility: exact steps for emergencies, heuristics for tone.
9. Loading is deterministic: stable order, no network, malformed files skipped.
10. Test both loader and answers; rewrite a rule only after an observed failure.

## Tests to add with the loader

- `load_skills()` returns 6, every rendered body ≤ 600 chars.
- `select_skills("человек без сознания")` starts with `triage`.
- `select_skills("привет")` → `persona`; `select_skills("дай ссылку")` → `sources`.
- Any question: 1–2 skills, never more; unknown question → `formatting`.
- Broken frontmatter is skipped without raising.