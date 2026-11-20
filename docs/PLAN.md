# ByteBot Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Бот-справочник БЖД с маскотом Ба́йтом: 8 категорий угроз, карточки, тесты, поиск, ИИ в личке и группах, рассылка.

**Architecture:** aiogram 3.31 + Postgres + Redis; экраны stateless на `callback_data`, рендер Rich HTML, сид контента из JSON в БД и in-memory индекс, ИИ — RAG по 3 карточкам через OpenAI-совместимый API.

**Tech Stack:** Python 3.14, aiogram 3.31, SQLAlchemy 2 async + asyncpg, Alembic, Postgres 18, Redis 8 (dedupe `update_id`, rate limit, очередь), pytest, Podman Compose.

**Spec:** `ByteBot-DESIGN.md`

## Global Constraints

- Python `>=3.11,<3.15`; локально `.venv` из `python3.14`.
- `callback_data` ≤64 байт, формат `b1:<action>:<args>`; всегда `answerCallbackQuery`.
- Навигация без FSM; экран = edit существующего сообщения.
- Единственный `primary` CTA на экран; `success/danger` — только результат/опасное.
- Локали: `ru` (default), `en`, `zh`, `tt`; фолбэк `-> ru` с бейджем `RU`.
- Секреты только через env/`*_FILE`; промпты и ответы ИИ не логируются.
- Никаких `<tg-emoji>` в обычном `sendMessage`; только rich или entity.
- Тесты офлайн; живой бот — только по явному флагу.

## Review Focus

- Пустой/мусорный ввод в поиск и в вопрос ИИ — бот не падает и не отвечает пустым сообщением.
- Отказ Telegram на `<tg-emoji>`/rich — экран всё равно доставляется (без иконок/HTML).
- Повторный клик и устаревший callback — не дублирует сообщения и не ломает счёт.
- Нет ключа/таймаут LLM — фолбэк в поиск, без «зависшего» typing.
- Группа без упоминания — бот молчит; с упоминанием — отвечает реплаем.

---

### Task 1: Scaffold, verify, repo

**Files:**
- Create: `ByteBot/` (генерируется), `.gitignore` (если нет), `docs/DESIGN.md`
- Move: `ByteBot-DESIGN.md` → `ByteBot/docs/DESIGN.md`; `ByteBot-PLAN.md` → `ByteBot/docs/PLAN.md`

**Interfaces:**
- Produces: рабочее дерево `ByteBot/` со стеком aiogram 3.31, `app/main.py`, `app/bot.py`, `app/db/*`, `app/services/i18n.py`, `app/handlers/{commands,llm,inline,broadcast,rich}.py`, `app/jobs/{llm,broadcast}.py`, `compose.yaml`.

- [ ] **Step 1:** `telegram_bot_target_resolve` со `stack: "aiogram"`; зафиксировать версии в заметке.
- [ ] **Step 2:** `telegram_bot_scaffold` с BotSpec из дизайна §5 (`features: commands, inline, llm, broadcast, rich`; `storage.postgres: true`; `storage.redis: true` — dedupe и rate limit; `runtime.queue: postgres`; admin `1715051614`).
- [ ] **Step 3:** `telegram_bot_verify` на `ByteBot/`; ожидается pass. Отчёт сохранить в `docs/VERIFY.md`.
- [ ] **Step 4:** `python3.14 -m venv .venv && .venv/bin/pip install -e .` (или `uv sync`); `pytest -q` — базовые тесты зелёные.
- [ ] **Step 5:** `git init`, первый коммит; `gh repo create lukavtz/byte-guardian --public --source=. --remote=origin --push`.

### Task 2: Локаль `tt` и локализованные команды

**Files:**
- Create: `app/i18n/tt.json`
- Modify: `app/services/i18n.py` (список локалей, resolver)
- Modify: `app/handlers/commands.py` (setMyCommands по языкам)
- Test: `tests/test_i18n_tt.py`

**Interfaces:**
- Produces: `resolve_locale("tt") == "tt"`; `t("tt", key)` возвращает татарский текст; `set_commands(bot, locale)`.

- [ ] **Step 1:** Тест: `resolve_locale("tt-TR") == "tt"`, `t("tt", "start.welcome")` не равен ключу; неизвестный `de` → `ru`.
- [ ] **Step 2:** `pytest tests/test_i18n_tt.py -q` → FAIL (KeyError/en locale нет).
- [ ] **Step 3:** Добавить `app/i18n/tt.json` (ключи из `ru.json`), `ENABLED_LOCALES = ["ru","en","zh","tt"]`, `LOCALES`, ветку `tt` в resolver.
- [ ] **Step 4:** `pytest tests/test_i18n_tt.py -q` → PASS.
- [ ] **Step 5:** Добавить `setMyCommands` для `ru/en/zh/tt` в `on_startup`; тест: фейковый bot получает 4 вызова.
- [ ] **Step 6:** Commit `feat(i18n): add tt locale and localized commands`.

### Task 3: UI-слой (emoji, render, delivery)

**Files:**
- Create: `app/ui/__init__.py`, `app/ui/emoji.py`, `app/ui/render.py`, `app/ui/delivery.py`, `app/ui/keyboards.py`
- Test: `tests/test_ui_render.py`, `tests/test_ui_delivery.py`

**Interfaces:**
- Produces:
  - `emoji(name: str, fallback: str) -> str`
  - `strip_premium(html: str) -> str`
  - `render_card(card: CardView, locale: str, page: int) -> RichScreen`
  - `render_menu(locale: str) -> RichScreen`
  - `Keyboard.button(text, data, style=None) -> InlineKeyboardButton`
  - `async def show(bot, chat_id: int, screen: RichScreen, message_id: int | None) -> Message | None`
  - `RichScreen(html: str, keyboard: InlineKeyboardMarkup | None)`

- [ ] **Step 1:** Тесты: `strip_premium('<tg-emoji emoji-id="1">🔥</tg-emoji>') == "🔥"`; карточка содержит `<details>`, `<h1>`, `<footer>`; меню содержит 8 кнопок категорий.
- [ ] **Step 2:** `pytest tests/test_ui_render.py -q` → FAIL (модулей нет).
- [ ] **Step 3:** Реализовать emoji (таблица `name→id`, id из `TELEGRAM_EMOJI_IDS`, фолбэк символ), render (HTML шаблоны), keyboards (стили, 2 в ряд, пагинация).
- [ ] **Step 4:** `pytest tests/test_ui_render.py -q` → PASS.
- [ ] **Step 5:** Тест delivery на фейковом bot: первый вызов `send_rich_message`, второй — `edit_message_text`; отказ `<tg-emoji>` → повтор без `<tg-emoji>`; `message is not modified` → `None`.
- [ ] **Step 6:** Реализовать лестницу деградации в `delivery.py` (максимум 4 попытки).
- [ ] **Step 7:** `pytest tests/test_ui_delivery.py -q` → PASS; commit `feat(ui): rich render and delivery`.

### Task 4: Данные, миграция, сид, индекс

**Files:**
- Create: `app/db/content.py` (модели Category/Threat/ThreatText/QuizQuestion/QuizAnswer/Settings), `app/content/repository.py`, `app/content/seed.py`, `content/*.json` (пустые заглушки категорий), `content/aliases.json`
- Create: `migrations/versions/0002_content.py`
- Test: `tests/test_seed.py`, `tests/test_repository.py`

**Interfaces:**
- Produces:
  - `class ContentRepository: async def load() -> None; def search(q: str, locale: str, limit: int) -> list[ThreatView]; def card(slug: str, locale: str) -> CardView | None; def categories() -> list[CategoryView]`
  - `async def seed_content(session, path: Path) -> SeedReport`
  - `CardView(slug, category_slug, severity, locale, title, summary, signs, before, during, after, forbidden, contacts, source, fallback: bool)`

- [ ] **Step 1:** Тест сида: сид дважды → число строк одинаково; `enabled=False` сохраняется; `content_version` выше → текст обновился.
- [ ] **Step 2:** `pytest tests/test_seed.py -q` → FAIL.
- [ ] **Step 3:** Модели + миграция `0002_content` (все таблицы из дизайна §6), upsert через `INSERT ... ON CONFLICT DO UPDATE WHERE excluded.version > current.version`.
- [ ] **Step 4:** `pytest tests/test_seed.py -q` → PASS.
- [ ] **Step 5:** Тесты репозитория: `card("fire-home","tt")` → `fallback is True`, title из `ru`; `search("пожара","ru")` и `search("fire","en")` возвращают `fire-home`; пустой запрос → `[]`.
- [ ] **Step 6:** Реализовать репозиторий: загрузка всех карточек в память, нормализация `casefold`, алиасы по локали.
- [ ] **Step 7:** `pytest tests/test_repository.py -q` → PASS; commit `feat(content): schema, idempotent seed, memory index`.

### Task 5: Навигация (меню, категория, карточка, назад)

**Files:**
- Create: `app/handlers/nav.py`, `app/services/callbacks.py`
- Modify: `app/handlers/commands.py` (`/start` с payload `card_<slug>`, `/help`, `/settings`)
- Test: `tests/test_nav.py`

**Interfaces:**
- Produces: `parse_cb(data: str) -> Callback`; `cb_menu()`, `cb_cat(slug, page)`, `cb_card(slug, page)`; хэндлеры возвращают `RichScreen` через `show()`.

- [ ] **Step 1:** Тест: `parse_cb("b1:card:fire-home:2")` → action `card`, slug, page 2; битые данные → `None`; каждый cb ≤64 байт.
- [ ] **Step 2:** `pytest tests/test_nav.py -q` → FAIL.
- [ ] **Step 3:** Реализовать парсер/билдеры; `/start card_fire-home` рендерит карточку, иначе меню; `b1:menu` — меню; `b1:cat:<slug>:<page>` — список; `b1:card:<slug>:<page>` — карточка; «Назад» несёт страницу; «Поделиться» → `switch_inline_query_current_chat=title`.
- [ ] **Step 4:** `/start` пишет `user.locale` из `language_code` (`zh-*→zh`, `tt→tt`, `en*→en`, иначе `ru`); `b1:lang:<code>` сохраняет и перерисовывает текущий экран; `/settings` показывает язык и статус рассылки.
- [ ] **Step 5:** `pytest tests/test_nav.py -q` → PASS; commit `feat(nav): stateless screens`.

### Task 6: Тест после карточки

**Files:**
- Create: `app/services/quiz.py`, `app/handlers/quiz.py`
- Modify: `app/ui/render.py` (`render_question`, `render_result`)
- Test: `tests/test_quiz.py`

**Interfaces:**
- Produces: `pick_questions(slug, locale, seed, n=3) -> list[QuizView]`; `record_answer(session, tg_id, slug, qid, seed, chosen, correct)`; `score(session, tg_id, slug, seed) -> tuple[int,int]`; callback `b1:quiz:<slug>:<q>:<opt>:<seed>`.

- [ ] **Step 1:** Тесты: seed даёт стабильный порядок; верный ответ → `correct=True`; повторный клик тем же seed не создаёт вторую строку (unique key); чужой `from_user` → alert и без INSERT; счёт 3/3.
- [ ] **Step 2:** `pytest tests/test_quiz.py -q` → FAIL.
- [ ] **Step 3:** Реализовать с `random.Random(seed)` и `ON CONFLICT DO NOTHING`; кнопка «Тест» скрыта, если вопросов нет.
- [ ] **Step 4:** `pytest tests/test_quiz.py -q` → PASS; commit `feat(quiz): seeded three-question test`.

### Task 7: Поиск, inline, deep link

**Files:**
- Modify: `app/handlers/inline.py`, `app/handlers/nav.py`
- Create: `app/handlers/search.py`
- Test: `tests/test_search.py`

**Interfaces:**
- Produces: `search_screen(q, locale) -> RichScreen`; inline отвечает ≤10 `InlineQueryResultArticle`, `cache_time=30`, `is_personal=False`; deep link `card_<slug>`.

- [ ] **Step 1:** Тесты: любой текст в личке → поиск; «пожар»/«пожара» → карточка `fire-home`; пустой результат → 8 подсказок-категорий; inline-результат содержит deep link и HTML ≤4096.
- [ ] **Step 2:** `pytest tests/test_search.py -q` → FAIL.
- [ ] **Step 3:** Реализовать единый роутер: команды → тексты → поиск; inline с `switch_pm` кнопкой при 0 результатов.
- [ ] **Step 4:** `pytest tests/test_search.py -q` → PASS; commit `feat(search): text and inline lookup`.

### Task 8: ИИ (RAG, лимиты, группы, промпт Байта)

**Files:**
- Create: `app/services/llm.py`, `app/services/prompt.py`, `app/handlers/ai.py`
- Modify: `app/jobs/llm.py`
- Test: `tests/test_llm.py`

**Interfaces:**
- Produces: `build_messages(question: str, locale: str, cards: list[CardView]) -> list[dict]`; `async def answer(client, messages) -> str`; `strip_html(text) -> str`; trigger `is_addressed(event, bot_username) -> bool`.

- [ ] **Step 1:** Тесты: `build_messages` содержит 3 карточки и правило «≤700 знаков»; при отсутствии `LLM_API_KEY` → `None`; таймаут → фолбэк; `is_addressed` истинно для `@bot`, реплая на бота, `/ask`; ложь для обычного текста.
- [ ] **Step 2:** `pytest tests/test_llm.py -q` → FAIL.
- [ ] **Step 3:** Реализовать клиент на `httpx` (`base_url`, `model`, `timeout=20`), RAG (3 карточки из репозитория), HTML-очистку, лимиты 5/мин пользователь, 20/мин чат, в группе — 1 ответ/10 с; при ошибке/таймауте/без ключа отдать `search_screen(question)` вместо текста.
- [ ] **Step 4:** Промпт персоны (точный текст, `app/services/prompt.py`):
  «Ты — Байт, маскот-помощник по безопасности жизнедеятельности университета ТИСБИ (Казань). Говоришь спокойно, коротко и по делу, без паники и морализаторства. Опираешься ТОЛЬКО на переданные карточки; если факта нет — скажи, что в справочнике этого нет, и предложи раздел. При угрозе жизни первая строка — «Звоните 112». Не ставишь диагнозы и не даёшь дозировки лекарств. Не обсуждаешь политику, религию и личности. Отвечаешь на языке пользователя (ru/en/zh/tt), максимум 700 знаков, структура: суть → 2–4 действия → 1 запрет. Не выдумывай номера телефонов».
- [ ] **Step 5:** Групповой хэндлер: только `@bot`/реплай/`/ask`, ответ реплаем, «Открыть карточку» deep link; в личке — на любой текст, если не команда.
- [ ] **Step 6:** `pytest tests/test_llm.py -q` → PASS; commit `feat(ai): RAG answers with Byte persona`.

### Task 9: Админка, рассылка, статистика

**Files:**
- Modify: `app/handlers/admin.py`, `app/handlers/broadcast.py`, `app/jobs/broadcast.py`
- Create: `app/services/broadcast.py`
- Test: `tests/test_admin.py`, `tests/test_broadcast.py`

**Interfaces:**
- Produces: `draft_save(admin_id, html)`, `draft_take(admin_id) -> str | None` (TTL 1 ч), callback `b1:bc:<yes|no>`; очередь `broadcast` с курсором.

- [ ] **Step 1:** Тесты: не-админ получает отказ; тумблер категории меняет только `enabled`; черновик переживает рестарт, истекает через час; рассылка пропускает `bot was blocked` и сохраняет курсор.
- [ ] **Step 2:** `pytest tests/test_admin.py tests/test_broadcast.py -q` → FAIL.
- [ ] **Step 3:** Реализовать `/admin` (toggle, stats, preview→confirm), `settings` для черновика, пейсинг 25/с.
- [ ] **Step 4:** `pytest -q` → PASS; commit `feat(admin): toggles, broadcast, stats`.

### Task 10: Контент и генератор тестов

**Files:**
- Create: `content/{fire,terror,tech,nature,medical,chem,rad,cyber}.json`, `content/aliases.json`, `scripts/gen_quiz.py`
- Test: `tests/test_content.py`

**Interfaces:**
- Produces: валидные JSON по схеме §6; `gen_quiz.py --card content/fire.json --locale ru` пишет `quiz` блоки через OpenAI-совместимый endpoint.

- [ ] **Step 1:** Тест: каждый файл парсится, slug уникальны, severity 1–3, у каждой карточки `ru` текст и ≥3 поля, `question_id` уникален в карточке.
- [ ] **Step 2:** `pytest tests/test_content.py -q` → FAIL (файлов нет).
- [ ] **Step 3:** Написать 8 файлов: по 7–8 угроз, RU полностью (EN/ZH/TT — по возможности, иначе отсутствуют).
- [ ] **Step 4:** `pytest tests/test_content.py -q` → PASS; прогнать `gen_quiz.py` для карточек без тестов, вычитать, закоммитить.
- [ ] **Step 5:** Commit `content: 8 categories with quizzes`.

### Task 11: Деплой, smoke, README

**Files:**
- Modify: `README.md`, `compose.yaml`, `.env.example`
- Create: `docs/RUN.md`

**Interfaces:**
- Produces: `podman compose up -d --build`; `/healthz` 200; polling-режим локально.

- [ ] **Step 1:** `podman machine init && podman machine start` (macOS, один раз).
- [ ] **Step 2:** `cp .env.example .env`, заполнить `BOT_TOKEN`, `WEBHOOK_SECRET`, `POSTGRES_PASSWORD`, `REDIS_PASSWORD`, `LLM_*` (не коммитить).
- [ ] **Step 3:** `podman compose up -d --build`; `curl -fsS localhost:8080/healthz`.
- [ ] **Step 4:** Smoke в Telegram: `/start` → категория → карточка → тест 3/3 → вопрос ИИ в личке → `@bot` в тестовой группе.
- [ ] **Step 5:** README: запуск, Podman, webhook, переменные, скриншоты; commit `docs: run and deploy`.
