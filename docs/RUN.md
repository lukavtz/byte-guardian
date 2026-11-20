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
`bot:8080` по `deploy/Caddyfile`.

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
Caddy терминирует TLS на `<домен>` из `deploy/Caddyfile`, Telegram ходит на `/webhook`.

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

## 8. Прод: Altare, Ubuntu 24.04

Прод не контейнер: `bot` и `worker` — обычные процессы под supervisor на VPS
Altare (Ubuntu 24.04). Postgres 16 и Redis 7 стоят на том же хосте из apt.
Режим — `polling`: входящий порт наружу не нужен, домен и TLS не требуются.

Схема: `/opt/bytebot` (код + `.venv` + `.env`) → supervisor → `/var/log/bytebot-*.log`.
Файлы в `/opt/bytebot`, логи в `/var/log`, юниты в `/etc/supervisor/conf.d`.

### Что установлено

```bash
sudo apt update
sudo apt install -y postgresql-16 redis-server supervisor git curl
curl -LsSf https://astral.sh/uv/install.sh | sudo env UV_INSTALL_DIR=/usr/local/bin sh
```

Python отдельно не ставится: `uv` тянет 3.14 сам (`uv python install 3.14`).
Postgres слушает только localhost (`/etc/postgresql/16/main/postgresql.conf`),
Redis — тоже (`bind 127.0.0.1` в `/etc/redis/redis.conf`, пароль в `requirepass`).

### Код и зависимости

```bash
sudo git clone https://github.com/lukavtz/byte-guardian.git /opt/bytebot
cd /opt/bytebot
uv python install 3.14
uv sync --frozen --no-dev
```

`.venv/bin/python` — тот самый интерпретатор, который запускает supervisor.

### `.env`

```bash
sudo install -m 600 /dev/null /opt/bytebot/.env
sudoedit /opt/bytebot/.env
```

Права `600`: читает только владелец файла — под ним и работают процессы.
Обязательный минимум:

```dotenv
BOT_TOKEN=
BOT_MODE=polling
ADMIN_IDS=
PORT=8080
DATABASE_URL=postgresql+asyncpg://bytebot:ПАРОЛЬ@127.0.0.1:5432/bytebot
REDIS_URL=redis://:ПАРОЛЬ@127.0.0.1:6379/0
LLM_BASE_URL=https://<провайдер>/v1
LLM_MODEL=
LLM_API_KEY=
LOG_LEVEL=info
```

`WEBHOOK_URL`/`WEBHOOK_SECRET` в polling не нужны. Секреты живут только в этом
файле; в git, чат и логи они не попадают. Для внешних секрет-сторов есть
варианты `*_FILE` — путь к файлу вместо значения.

### Postgres и Redis

```bash
sudo -u postgres createuser --pwprompt bytebot
sudo -u postgres createdb -O bytebot bytebot
sudo systemctl enable --now postgresql redis-server
```

Схему создают миграции, расширения не нужны. DSN — в `.env`.

### Миграции

Перед каждым обновлением кода, с подгруженным окружением (тот же `.env`, что у
процессов):

```bash
cd /opt/bytebot
set -a; . /opt/bytebot/.env; set +a
uv run alembic upgrade head
uv run alembic current
```

Если миграция падает — процессы не трогаем, разбираем лог `alembic` и
откатываемся на предыдущий коммит; бот без схемы не стартует.

### supervisor

`/etc/supervisor/conf.d/bytebot.conf`:

```ini
[program:bytebot-bot]
directory=/opt/bytebot
command=/opt/bytebot/.venv/bin/python -m app.main
autostart=true
autorestart=true
startsecs=10
stopasgroup=true
killasgroup=true
stopwaitsecs=30
stdout_logfile=/var/log/bytebot-bot.log
redirect_stderr=true
stdout_logfile_maxbytes=10MB
stdout_logfile_backups=5

[program:bytebot-worker]
directory=/opt/bytebot
command=/opt/bytebot/.venv/bin/python -m app.worker
autostart=true
autorestart=true
startsecs=10
stopasgroup=true
killasgroup=true
stopwaitsecs=30
stdout_logfile=/var/log/bytebot-worker.log
redirect_stderr=true
stdout_logfile_maxbytes=10MB
stdout_logfile_backups=5
```

`directory` обязателен: `.env` ищется относительно рабочего каталога. Если
процессы идут под отдельным пользователем — добавьте `user=bytebot` в обе
секции и выдайте ему `/opt/bytebot` и `.env`.

```bash
sudo supervisorctl reread
sudo supervisorctl update
sudo supervisorctl status bytebot-bot bytebot-worker
```

### Обновление и рестарт

```bash
cd /opt/bytebot
git pull --ff-only
uv sync --frozen --no-dev
set -a; . /opt/bytebot/.env; set +a
uv run alembic upgrade head
sudo supervisorctl restart bytebot-bot bytebot-worker
```

Только рестарт, без обновления кода:

```bash
sudo supervisorctl restart bytebot-bot bytebot-worker
sudo supervisorctl stop bytebot-bot bytebot-worker
sudo supervisorctl start bytebot-bot bytebot-worker
```

### Проверка

```bash
curl -fsS localhost:8080/healthz          # {"ok": true}
curl -fsS localhost:8080/readyz           # checks.postgres / checks.redis
sudo tail -n 50 /var/log/bytebot-bot.log
sudo tail -n 50 /var/log/bytebot-worker.log
```

Хранилища и Telegram:

```bash
cd /opt/bytebot && set -a; . ./.env; set +a
redis-cli --no-auth-warning -u "$REDIS_URL" ping
pg_isready -h 127.0.0.1 -p 5432 -U bytebot
curl -fsS "https://api.telegram.org/bot$BOT_TOKEN/getWebhookInfo"
```

`redis-cli` отвечает `PONG`; у `getWebhookInfo` поле `url` пустое — иначе
вебхук перехватывает апдейты и polling молчит:

```bash
curl -fsS "https://api.telegram.org/bot$BOT_TOKEN/deleteWebhook"
```

`readyz` отдаёт 503 — смотрим, что именно упало: `checks.postgres` (служба
Postgres, пароль в DSN) или `checks.redis` (`requirepass` не совпал с `REDIS_URL`).

### Бэкап

Postgres — источник правды: карточки, пользователи, очередь, статистика.

```bash
sudo -u postgres pg_dump -Fc bytebot > /var/backups/bytebot-$(date +%F).dump
```

Ежедневно через cron в crontab пользователя `postgres` (`sudo -u postgres crontab -e`):

```cron
15 4 * * * pg_dump -Fc bytebot > /var/backups/bytebot-$(date +\%F).dump
```

Ротация — `find /var/backups -name 'bytebot-*.dump' -mtime +14 -delete`.
Восстановление: `sudo -u postgres pg_restore --clean --if-exists -d bytebot <файл>`.
Redis терять не страшно: там dedupe `update_id` на 24 часа и лимиты, очередь
живёт в Postgres.

### Webhook (необязательно)

Прод работает на polling. Webhook нужен, только если появится домен и reverse
proxy: `BOT_MODE=webhook`, `WEBHOOK_URL=https://<домен>`, регистрация —
раздел 4, TLS — на прокси, дальше `localhost:8080/webhook`.
