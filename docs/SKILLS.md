# Skill system v2

Prompt skills for Байт. One folder per job: `app/skills/<name>/SKILL.md` plus
optional `references/*.md`. The loader is `app/skills/loader.py`; it injects at
most **3 skills and 900 characters** into the system prompt.

## Research base

Primary sources read for this version (Nov 2026). Only actionable rules were
kept; marketing copy and tool-specific setup were dropped.

- [Anthropic — Skill authoring best practices](https://platform.claude.com/docs/en/agents-and-tools/agent-skills/best-practices):
  concise bodies, degrees of freedom matched to fragility, descriptions that say
  what + when in third person, references one level deep, 500-line body ceiling,
  eval checklist.
- [Agent Skills specification](https://agentskills.io/specification) and
  [skill-creator best practices](https://agentskills.io/skill-creation/best-practices):
  `SKILL.md` as the hub, `references/` as spokes, "read `references/x.md` when
  Y" gating, defaults over menus, gotchas over generic advice.
- [Optimizing skill descriptions](https://agentskills.io/skill-creation/optimizing-descriptions):
  descriptions carry triggering; test with ~20 queries, including near-misses,
  and keep a train/validation split.
- [Claude Code lessons on skills](https://claude.com/blog/lessons-from-building-claude-code-how-we-use-skills):
  filesystem progressive disclosure; the hub holds a symptom-to-file table.
- [OpenAI — Prompt engineering](https://developers.openai.com/api/docs/guides/prompt-engineering):
  developer instructions outrank user messages; identity → instructions →
  examples → context order; few-shot pairs beat prose; XML tags separate data
  from rules; prompts live in code and ship through tests.
- [OpenAI — Working with evals](https://developers.openai.com/api/docs/guides/evals):
  declare the task, run fixed inputs, grade, iterate; keep behavior tests beside
  the prompt.
- [OpenAI — Safety best practices](https://developers.openai.com/api/docs/guides/safety-best-practices)
  and [Model Spec: chain of command](https://model-spec.openai.com/2025-02-12.html#chain_of_command):
  constrain topic and output, treat retrieved text as data, red-team redirects.
- [Red Hat — Building skills for AI agents (Jul 2026)](https://next.redhat.com/2026/07/28/building-skills-for-ai-agents-pitfalls-and-best-practices):
  scripts for mechanical work, instructions for judgment; 1–3 skills per task;
  L3 ungated references are the biggest token sink; eval before every bump.

## 15-rule checklist

Each rule is testable in this repo, not advice.

| # | Rule | Check |
|---|---|---|
| 1 | Description says what the skill does and when to use it, in third person, one sentence. | `tests/test_skills.py::test_frontmatter_fields_are_parsed`, manual review |
| 2 | Name is the folder, `[a-z0-9-]+`, and names one activity. | loader rejects a mismatch |
| 3 | One coherent job per skill; a task needs 1–3 skills, never a mega-skill. | golden table shows ≤3 per question |
| 4 | Only content the model lacks: project rules, numbers, bans. No textbook theory. | body review; 24 KB authored, ≤900 chars injected |
| 5 | Front-load the rules; examples and anti-patterns follow. | first 240 chars are the injected body |
| 6 | Body stays small (≤2 KB here, ≤500 lines by spec); details move to `references/`. | loader rejects >2048 bytes |
| 7 | References are one level deep and gated by their own triggers. | `test_reference_index_appears_only_on_a_trigger_match` |
| 8 | Only a short reference index is injected: name + summary/first 120 chars. | `test_reference_body_is_not_injected` |
| 9 | Match freedom to fragility: exact steps for fire/gas/112, heuristics for tone. | skill bodies differ by priority and wording |
| 10 | Give a default, not a menu; one recommended action per step. | review of first-aid/fire-smoke |
| 11 | Show 2 concrete input → output pairs, not abstract advice. | every SKILL.md has `## Примеры` |
| 12 | Name anti-patterns and gotchas explicitly. | every SKILL.md has `## Не делай` |
| 13 | Triggers use user words, support ru morphology and tt agglutination, and avoid near-miss words. | `test_russian_morphology_matches_without_substrings_inside_words` |
| 14 | Selection is deterministic: score, priority, name; locale filters; baseline always present; low-priority fallbacks yield to safety skills. | selection tests |
| 15 | Budget is enforced and measured: ≤3 skills, ≤900 chars, references only as an index; malformed skills are skipped once and never raise. | budget/malformed tests, `test_build_messages_*` |

## Mapping: old skills and failures → rules

| Old skill / failure | Rules | Change |
|---|---|---|
| `persona` claimed the generic trigger `помоги`, so vague requests got a greeting instead of a clarifying question. | 1, 3, 13 | Removed `помоги`; `clarify` owns vague requests. |
| `triage` missed `обжёг` (different root from `ожог`) and `проглотил`. | 13 | Added `обжег/обжог`, `проглот`; morphology suffix window widened. |
| `first-aid` had no path for «не дышит» / «без сознания» because triage scored alone. | 3, 10 | Added those triggers; CPR skill now rides with triage. |
| `group-chat` triggered on bare `все`/`чат`, which also matches unrelated text. | 13 | Kept chat-scoped triggers, dropped the universal word. |
| `sources` had no rule for an untrusted web block beyond one line. | 4, 12 | Explicit UNTRUSTED rule, 2-link cap, refusal to invent URLs. |
| `formatting` said 900 chars while the loader allowed 1200. | 15 | Budget is now 900 in code, prompt, and test. |
| Flat `*.md` loader: no directory scan, no priority, no locales, no references. | 2, 6, 7, 14 | Folder layout, priority, locale filter, gated references. |
| Substring matching selected skills inside unrelated words. | 13 | Word-boundary + stem matching with a bounded suffix. |
| `locale` argument was ignored. | 14 | Skills may declare `locales`; unavailable locales are dropped. |
| No eval meant every prompt edit was unmeasured. | 15 | 20-question golden set, budget test, malformed test. |
| Fallback skills could crowd out a life-safety skill. | 3, 14 | Fallbacks (priority < 40) are dropped when a topical skill matched. |

## Layout and budget

```
app/skills/<name>/SKILL.md          # frontmatter + body ≤ 2 KB
app/skills/<name>/references/*.md   # optional, one level deep, gated
```

Frontmatter (JSON-compatible YAML; stdlib `json.loads`, no PyYAML):

```yaml
---
name: gas-leak
description: "Даёт действия при запахе газа: не включать свет, перекрыть, выйти, звонить 104."
triggers: ["газ", "утечк", "плит"]
priority: 93
locales: ["ru", "en", "tt"]      # optional; omitted = every locale
---
```

Reference frontmatter: `triggers: [...]`, optional `summary`. The summary (or
the first 120 characters of the body) becomes one `<ref>` line. The body stays
on disk: this bot has no file tool, so the index is the retrieval surface.

Injection shape and limits:

```
<skill name="fire-smoke">
...body, clipped at 240 chars on a word boundary...
</skill>
<ref skill="fire-smoke" path="extinguishers.md">Огнетушители: ОП порошковый…</ref>
```

- `MAX_SKILLS = 3`, `BUDGET_CHARS = 900`, `BODY_CHARS = 240`,
  `REF_INDEX_CHARS = 120`, `SKILL_MAX_BYTES = 2048`.
- Selection: locale filter → trigger score (multi-word triggers weigh 2) →
  drop fallbacks (`priority < 40`) when a topical skill matched → sort by score,
  priority, name → reserve a slot for `formatting`.
- References are selected per chosen skill and only when a reference trigger
  matches the question.

## Shipped skills

18 skills: the 6 existing ones rewritten and 12 new. Bytes are the authored body
size, triggers the frontmatter count.

| Skill | Bytes | Triggers | Priority | Refs |
|---|---:|---:|---:|---:|
| `admin-help` | 1631 | 9 | 30 | 0 |
| `clarify` | 1277 | 9 | 30 | 0 |
| `content-gap` | 1493 | 9 | 25 | 0 |
| `cyber-fraud` | 1572 | 13 | 75 | 1 |
| `emergency-call` | 1364 | 14 | 91 | 2 |
| `evacuation` | 1364 | 11 | 80 | 1 |
| `fire-smoke` | 1261 | 11 | 92 | 1 |
| `first-aid` | 1142 | 22 | 84 | 0 |
| `formatting` | 1078 | 9 | 20 | 0 |
| `gas-leak` | 1405 | 8 | 93 | 0 |
| `group-chat` | 1145 | 7 | 50 | 0 |
| `mental-support` | 1564 | 16 | 78 | 2 |
| `off-topic` | 1257 | 13 | 5 | 0 |
| `persona` | 1227 | 13 | 10 | 0 |
| `radiation` | 1514 | 10 | 90 | 0 |
| `sources` | 1201 | 12 | 30 | 0 |
| `study-help` | 1539 | 10 | 40 | 1 |
| `triage` | 1207 | 16 | 85 | 0 |

## Golden eval

20 real questions, expected exact skill set (order ignored). The test asserts
at least 18/20; current score is **20/20**.

| # | Question | Expected set |
|---:|---|---|
| 1 | Человек без сознания, не дышит | `triage`, `first-aid`, `formatting` |
| 2 | Пожар в общежитии, что делать | `fire-smoke`, `emergency-call`, `formatting` |
| 3 | Пахнет газом в квартире | `gas-leak`, `emergency-call`, `formatting` |
| 4 | Как вызвать скорую | `emergency-call`, `formatting` |
| 5 | Ребёнок проглотил таблетки | `triage`, `first-aid`, `formatting` |
| 6 | Сильное кровотечение из ноги | `triage`, `first-aid`, `formatting` |
| 7 | Обжёг руку кипятком | `first-aid`, `formatting` |
| 8 | Как выйти из горящего здания | `evacuation`, `fire-smoke`, `formatting` |
| 9 | На улице радиация, что делать | `radiation`, `formatting` |
| 10 | Мошенники выманили код из смс | `cyber-fraud`, `formatting` |
| 11 | Паническая атака, не могу дышать | `mental-support`, `formatting` |
| 12 | Умер близкий, как справиться | `mental-support`, `formatting` |
| 13 | Какие вопросы на экзамене по БЖД | `study-help`, `formatting` |
| 14 | Помоги с билетом по безопасности жизнедеятельности | `study-help`, `formatting` |
| 15 | Привет | `persona`, `formatting` |
| 16 | Какая погода завтра | `off-topic`, `formatting` |
| 17 | Помоги | `clarify`, `formatting` |
| 18 | Как пользоваться админкой | `admin-help`, `formatting` |
| 19 | Ты не знаешь ответ? | `content-gap`, `formatting` |
| 20 | Где почитать про хлор | `sources`, `formatting` |

## Adding a skill

1. `mkdir app/skills/<name> && $EDITOR app/skills/<name>/SKILL.md`.
2. Frontmatter: name (folder), one-sentence description, user-word triggers,
   integer priority, optional locales.
3. Body: imperative rules first, 2 examples, `## Не делай`, optional locale
   notes. Keep the file ≤ 2048 bytes.
4. Move long tables and scripts talk into `references/*.md` with `triggers` and
   `summary` in frontmatter.
5. Add a golden row, run `uv run pytest tests/test_skills.py`, then the full
   suite, `ruff`, and `mypy`.

Commands: `uv run pytest`, `uv run ruff check app tests`, `uv run mypy app`.