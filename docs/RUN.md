# Запуск ByteGuardian в Podman

Проверялось на Podman 6.2.0 (macOS arm64) и rootless Podman на Linux.
Требования: Podman ≥ 5, compose-провайдер (`podman compose`), `.env` в корне репозитория.

## 1. Подготовка

```bash
cp .env.example .env
```

Заполнить минимум:

| Переменная | Значение |
|---|---|
| `BOT_TOKEN` | токен от @BotFather |
| `WEBHOOK_SECRET` | `A-Za-z0-9_-`, 1–256 символов |
| `POSTGRES_PASSWORD` | пароль БД |
| `REDIS_PASSWORD` | пароль Redis |
| `BOT_MODE` | `polling` локально, `webhook` в проде |
| `WEBHOOK_URL` | `https://<домен>` (только webhook) |
| `ADMIN_IDS` | Telegram ID админов через запятую |
| `LLM_BASE_URL`, `LLM_MODEL`, `LLM_API_KEY` | OpenAI-совместимый провайдер |

`DATABASE_URL` и `REDIS_URL` compose собирает сам из паролей и имён сервисов.
Вручную их задавать не нужно; для запуска без compose — обязательно.

## 2. macOS

```bash
podman machine init      # один раз
podman machine start
podman machine inspect --format '{{.ConnectionInfo.PodmanSocket.Path}}'
```

## 3. Запуск

```bash
podman compose up -d --build
podman compose ps
podman compose logs -f bot
curl -fsS localhost:8080/healthz
curl -fsS localhost:8080/readyz
```

Порядок: `postgres`/`redis` (healthcheck) → `migrate` (`alembic upgrade head`) →
`bot`, `worker`. `caddy` включается только профилем `webhook` и проксирует
`bot:8080`.

```bash
podman compose --profile webhook up -d --build   # прод, TLS через Caddy
```

Проверка без запуска контейнеров:

```bash
podman compose config
```

`/healthz`, `/readyz`, `/metrics` отдаёт aiohttp-сервер на `PORT` в обоих
режимах: в webhook это основное приложение, в polling — отдельный сервер рядом
с long polling. Порт публикуется как `${HEALTH_HOST_PORT:-8080}:${PORT:-8080}`;
если 8080 занят на хосте, задайте `HEALTH_HOST_PORT=18080`.

## 4. Режимы

Polling (dev): `BOT_MODE=polling`, вебхук не нужен, наружу ничего не публикуется.

Webhook (prod): `BOT_MODE=webhook`, `WEBHOOK_URL=https://<домен>`.
Caddy терминирует TLS на `<домен>` из `Caddyfile`, Telegram ходит на `/webhook`.

Перед сменой режима удалить старый вебхук:

```bash
curl -fsS "https://api.telegram.org/bot$BOT_TOKEN/deleteWebhook"
```

Зарегистрировать новый:

```bash
curl -fsS "https://api.telegram.org/bot$BOT_TOKEN/setWebhook" \
  -d "url=$WEBHOOK_URL/webhook" \
  -d "secret_token=$WEBHOOK_SECRET" \
  -d 'allowed_updates=["message","callback_query","inline_query","widget_query","shared_widget_query"]'
curl -fsS "https://api.telegram.org/bot$BOT_TOKEN/getWebhookInfo"
```

## 5. Rootless-порты

Rootless Podman по умолчанию не может слушать порты < 1024, поэтому публикация
80/443 упадёт с `bind: permission denied`.

Linux (постоянно):

```bash
sudo sysctl -w net.ipv4.ip_unprivileged_port_start=80
echo 'net.ipv4.ip_unprivileged_port_start=80' | sudo tee /etc/sysctl.d/99-podman-ports.conf
```

Без прав root: убрать `ports` у `caddy` и поставить внешний reverse proxy, либо
слушать 8080/8443 и проксировать с хоста.

macOS (Podman 6 / libkrun): порог тот же — публикация 80/443 падает с
`rootlessport cannot expose privileged port 80`. Локально `caddy` не нужен
(профиль `webhook`); для проверки прод-режима на macOS поднимать порты ≥1024.

## 6. Обслуживание

```bash
podman compose logs -f bot worker
podman compose restart bot
podman compose exec bot uv run alembic upgrade head
podman compose down                 # тома сохраняются
podman compose down -v              # удалить БД и Redis
```

Бэкап БД:

```bash
podman compose exec postgres pg_dump -U "${POSTGRES_USER:-bot}" "${POSTGRES_DB:-bot}" > backup.sql
```

## 7. Диагностика

**`Cannot connect to Podman` / нет сокета**
```bash
podman machine start            # macOS
podman info                     # должен вернуть версию, не ошибку
systemctl --user status podman.socket   # Linux: включить и запустить
systemctl --user enable --now podman.socket
export DOCKER_HOST=unix://$XDG_RUNTIME_DIR/podman/podman.sock
```

**`podman compose` требует внешний провайдер**
`podman compose` — обёртка. Если провайдер не найден:
```bash
podman compose version          # покажет используемый бинарь
pipx install podman-compose     # альтернатива
# или поставить docker-compose и указать его:
export PODMAN_COMPOSE_PROVIDER=/opt/homebrew/bin/docker-compose
```
Проверка: `podman compose config` должен печатать разобранный YAML без ошибок.

**`pull access denied` / `short-name resolution`**
Добавить registry в имя образа (`docker.io/library/postgres:18-alpine`) или
`unqualified-search-registries` в `/etc/containers/registries.conf`.

**`migrate` завершился с ошибкой**
```bash
podman compose logs migrate
podman compose run --rm migrate uv run alembic current
```
Причина обычно — неверный `POSTGRES_PASSWORD` или недоступный `postgres`.

**`readyz` отдаёт 503**
```bash
podman compose ps                       # все ли healthy
podman compose exec redis redis-cli -a "$REDIS_PASSWORD" ping
podman compose exec postgres pg_isready -U "${POSTGRES_USER:-bot}"
```
`checks.postgres` / `checks.redis` в ответе показывают, что именно упало.

**Вебхук не доходит**
```bash
podman compose logs caddy bot
curl -i https://<домен>/healthz
curl -fsS "https://api.telegram.org/bot$BOT_TOKEN/getWebhookInfo"
```
`last_error_message` в ответе Telegram обычно указывает на 403 (неверный секрет)
или 502 (Caddy не видит `bot`).

**`postgres` падает: "there appears to be PostgreSQL data in /var/lib/postgresql/data"**
Образ `postgres:18`+ требует один том в `/var/lib/postgresql` (данные лежат в
подкаталоге версии). Том в `/var/lib/postgresql/data` не подходит:
```bash
podman compose down -v && podman compose up -d
```

**`migrate`/`bot`: `failed to create directory /app/.venv/...: Permission denied`**
`uv run` в рантайме пытался доставить dev-зависимости в `.venv`, собранный от
root с `--no-dev`. Образ выставляет `UV_NO_DEV=1`; при правке Dockerfile не
убирать.

**`rootlessport cannot expose privileged port 80`**
`caddy` включён профилем `webhook`. Локально:
`podman compose up -d` (без caddy) — либо `--profile webhook` с портами ≥1024.

**Порт 8080 на хосте занят**
Публикация бота — `${HEALTH_HOST_PORT:-8080}:${PORT:-8080}`. Задайте
`HEALTH_HOST_PORT=18080`, иначе `up` упадёт на конфликте порта.

**pytest падает на `test_webhook_requires_secret` после ручных curl-проверок**
`set -a; . ./.env` экспортирует `WEBHOOK_SECRET` в шелл, `pydantic-settings`
читает его раньше `_env_file=None`. Запускайте тесты в чистом окружении
(`unset WEBHOOK_SECRET` или новый терминал).

## 8. Altare (altare.gg)

Образ: `ghcr.io/lukavtz/byte-guardian:latest` (linux/amd64). Внутри —
`docker-entrypoint.sh`: сначала `alembic upgrade head`, потом бот. Если миграция
упала, контейнер пишет причину в stderr и выходит с кодом 1 — бот без схемы не
стартует.

### Образ

```bash
docker build --platform linux/amd64 -t ghcr.io/lukavtz/byte-guardian:latest .
docker push ghcr.io/lukavtz/byte-guardian:latest
```

Пакет на GHCR должен быть публичным, иначе Altare нужен registry login
(`altr docker create --registry-user lukavtz --registry-token-stdin`).

### Ресурсы

```bash
altr postgres create byte-guardian --size micro
altr valkey create byte-guardian --size micro
altr postgres info byte-guardian      # DATABASE_URL
altr valkey info byte-guardian        # REDIS_URL
```

`DATABASE_URL` — `postgres://user:pass@host:5432/db`, `REDIS_URL` —
`redis://:pass@host:6379/0`.

### Приложение

```bash
altr docker create byte-guardian ghcr.io/lukavtz/byte-guardian:latest \
  --port 8080 --memory 1024 --auto-update \
  -e BOT_MODE=webhook \
  -e WEBHOOK_URL=https://byte-guardian.altare.app \
  -e BOT_TOKEN=... \
  -e WEBHOOK_SECRET=... \
  -e DATABASE_URL=... \
  -e REDIS_URL=... \
  -e ADMIN_IDS=... \
  -e LLM_BASE_URL=... -e LLM_MODEL=... -e LLM_API_KEY=...
```

Переменные дальше правятся без пересборки:

```bash
altr docker env set byte-guardian PORT=8080 LOG_LEVEL=info
altr docker info byte-guardian       # образ, digest, последние деплои
altr docker redeploy byte-guardian   # подтянуть новый :latest
```

Домен: по умолчанию Altare даёт `byte-guardian.altare.app`; свой —
`altr domains add byte-guardian <домен>`. Затем `WEBHOOK_URL=https://<домен>`
(без слэша в конце; Telegram ходит на `$WEBHOOK_URL/webhook`).

### Webhook

Вебхук приложение не регистрирует само — после первого деплоя вызвать один раз
(и после каждой смены домена):

```bash
curl -fsS "https://api.telegram.org/bot$BOT_TOKEN/setWebhook" \
  -d "url=$WEBHOOK_URL/webhook" \
  -d "secret_token=$WEBHOOK_SECRET" \
  -d 'allowed_updates=["message","callback_query","inline_query","widget_query","shared_widget_query"]'
```

Проверка после деплоя:

```bash
curl -fsS https://<домен>/healthz
curl -fsS "https://api.telegram.org/bot$BOT_TOKEN/getWebhookInfo"
```

`last_error_message` в ответе Telegram: 403 — не совпал `WEBHOOK_SECRET`,
502/504 — приложение не слушает `PORT`.

### Откат миграций

Образ мигрирует при каждом старте, вперёд. Автоматического откатa нет: перед
деплоем с новой схемой сделайте бэкап (`altr postgres backups create byte-guardian`),
откат — новый образ со старой ревизией и ручной `alembic downgrade`.
