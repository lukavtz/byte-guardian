# Как участвовать

Спасибо, что заглянули. Бот — справочник по БЖД для ТИСБИ: 8 разделов,
59 карточек, 177 вопросов теста, 4 языка.

## Подготовка

Нужны Podman ≥ 5 с compose-провайдером и [uv](https://docs.astral.sh/uv/).

```bash
git clone https://github.com/lukavtz/byte-guardian.git
cd byte-guardian
cp .env.example .env          # BOT_TOKEN, WEBHOOK_SECRET, POSTGRES_PASSWORD, REDIS_PASSWORD, LLM_API_KEY
podman machine init && podman machine start   # macOS, один раз
podman compose up -d --build
curl -fsS localhost:8080/healthz
```

Локально `BOT_MODE=polling` — TLS и домен не нужны. Подробности и режим
webhook — [docs/RUN.md](docs/RUN.md).

## Проверки

```bash
uv sync
uv run ruff check . && uv run mypy app
uv run pytest -q
```

CI гоняет те же четыре шага на Python 3.14. Pull request без зелёного CI не
мёржится.

## Коммиты и pull request

Conventional Commits, английский, scope по области:

```
feat(search): inline lookup by card title
fix(quiz): advance only once per click
docs(run): split diagnostics entries
content: verify 59 threats against official sources
```

Один коммит — одно изменение. В PR заполните «Что / Зачем / Как проверено /
Риск»; скриншот экрана бота помогает, если меняется интерфейс.

## Контент

- Источники — только официальные российские: МЧС, Минздрав, Роспотребнадзор,
  МВД, Минобрнауки, методички вузов. Википедия, форумы и блоги — нет.
- Карточка — JSON в `content/<раздел>.json`; Postgres после сида остаётся
  источником правды, поэтому правьте JSON и пересидьте.
- Структура карточки: признаки, действия до / во время / после, запреты,
  телефоны. При угрозе жизни первая строка — «Звоните 112».
- Никаких диагнозов и дозировок. Материал не заменяет МЧС и врача.
- Локализация: ru обязателен, en / zh / tt — по возможности; если перевода
  нет, карточка отдаётся на ru с бейджем `RU`.

## Уязвимости

Не через issue — см. [SECURITY.md](SECURITY.md).