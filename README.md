# Byte Guardian Telegram bot

Бот-справочник по БЖД на **Python 3.14 + aiogram 3.31** (Bot API 10.4).
Podman — единственная поддерживаемая среда запуска.

## Что внутри

- 8 категорий угроз, карточки, тесты, поиск, inline-режим.
- ИИ-помощник Байт: RAG по карточкам, ответы в личке через `/ask` и в группах по
  упоминанию/реплаю.
- Рассылка и тумблеры разделов для админов.
- Очередь задач, dedupe и rate limit на Redis; состояние — в Postgres.

## Локальная разработка

```bash
cp .env.example .env         # заполнить BOT_TOKEN, WEBHOOK_SECRET, LLM_*
uv sync
uv run pytest -q
uv run ruff check .
```

Без Podman боту нужны доступные `DATABASE_URL` и `REDIS_URL` (можно поднять только
хранилища: `podman compose up -d postgres redis`).

## Запуск в Podman

macOS (один раз):

```bash
podman machine init
podman machine start
```

Все платформы:

```bash
cp .env.example .env
# BOT_TOKEN, WEBHOOK_SECRET, POSTGRES_PASSWORD, REDIS_PASSWORD, LLM_API_KEY
podman compose up -d --build
podman compose ps
curl -fsS localhost:8080/healthz   # {"ok": true}
```

Секреты живут только в `.env` (файл в `.gitignore`). Для оркестраторов можно
смонтировать файлы и указать `*_FILE` (`BOT_TOKEN_FILE`, `WEBHOOK_SECRET_FILE`,
`DATABASE_URL_FILE`, `REDIS_URL_FILE`, `LLM_API_KEY_FILE`) — они имеют приоритет над
переменными.

`compose.yaml` поднимает: `bot` (webhook/polling), `worker` (очередь), `postgres`,
`redis`, `migrate` (Alembic, разово), `caddy` (TLS и прокси на `bot:8080`).

## Режимы

- `BOT_MODE=polling` (по умолчанию) — локальная разработка, TLS не нужен.
- `BOT_MODE=webhook` — прод. Нужен `WEBHOOK_URL=https://<домен>`; сервер слушает
  `:8080`, маршрут `/webhook`, секрет проверяется по заголовку
  `X-Telegram-Bot-Api-Secret-Token`.

Регистрация вебхука (домен уже указывает на Caddy):

```bash
curl -fsS "https://api.telegram.org/bot$BOT_TOKEN/setWebhook" \
  -d "url=https://<домен>/webhook" \
  -d "secret_token=$WEBHOOK_SECRET" \
  -d 'allowed_updates=["message","callback_query","inline_query"]'
```

Только один режим одновременно. Перед переключением на polling вызовите
`deleteWebhook`.

## Эксплуатация

```bash
podman compose logs -f bot worker
podman compose restart bot
podman compose down            # данные в named volumes сохраняются
podman compose up -d --build   # после изменения кода
podman compose exec bot uv run alembic upgrade head
```

- `/healthz` — живость, `/readyz` — Postgres + Redis, `/metrics` — Prometheus.
- `/admin` доступен только ID из `ADMIN_IDS` (числа через запятую).
- Rootless Podman не может занять порт 80 без настройки:
  `sudo sysctl net.ipv4.ip_unprivileged_port_start=80` (Linux, см. `docs/RUN.md`).
- Ошибки запуска и compose-провайдера — `docs/RUN.md`.

## Bot API 10.4 typed raw adapter

`app/services/api104.py` реализует typed `TelegramMethod` для методов, которых нет в
aiogram 3.31.0: `sendWidget`, `sendSharedWidget`, `answerWidgetQuery`,
`editMessageWidgetData`, `editSharedWidgetData`, `sendRichMessage`,
`sendRichMessageDraft`. Вызов — `await bot(method)`. Widget query нужно закрыть за
15 с; rich draft живёт 30 с и заменяется финальным `sendRichMessage`.

## Безопасность

- `.env` и `secrets/` не коммитятся; токен не попадает в логи.
- Вебхук без валидного секрета отклоняется.
- Админ-команды проверяют серверный allowlist.
- `update_id` дедуплицируется 24 часа; ответы ИИ лимитированы на пользователя и чат.