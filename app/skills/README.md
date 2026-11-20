# Chat skills

One folder per job: `app/skills/<name>/SKILL.md`, optional
`app/skills/<name>/references/*.md`. The loader injects at most 3 skills and
900 characters into the system prompt; references surface only as a short
`<ref>` index line.

- Full contract, research, checklist, skill table, and golden eval:
  [`docs/SKILLS.md`](../../docs/SKILLS.md).
- Loader: `app/skills/loader.py` (`load_skills`, `select_skills`,
  `references_for`, `format_skills`).
- Tests: `tests/test_skills.py`.

Quick authoring rules: imperative rules first, one job per skill, triggers in
the user's words, 2 examples, `## Не делай`, body ≤ 2 KB, details in
`references/`.