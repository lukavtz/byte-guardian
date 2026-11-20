# ByteBot — дизайн

Дата: 2026-11-20
Проект: телеграм-бот-справочник по БЖД (безопасность жизнедеятельности), практика в вузе.
Стек: Python 3.14 + aiogram 3.31, Postgres 16, Redis 7 (dedupe, rate limit, очередь),
OpenAI-совместимый LLM.
Прод: нативный запуск под supervisor на Ubuntu 24.04 (Altare); Postgres 16 и Redis 7 —
на том же хосте. Webhook — опция, по умолчанию polling.

## 1. Зачем

Студент открывает бота, видит красивый экран с категориями угроз, читает короткую
карточку «что делать» и сразу проверяет себя мини-тестом. В группах бот отвечает
на вопросы по БЖД, опираясь на те же карточки. Вместо мини-роликов — структурные
карточки в стиле Telegram Rich Messages.

Успех: нужная инструкция находится за ≤3 нажатия; тест проходится без подсказок;
в группе бот отвечает по делу, коротко и со ссылкой на раздел.

## 2. Цели / не-цели / анти-цели

Цели:
- 60+ коротких карточек (≤1200 знаков) с единой структурой.
- 4 языка: RU (основной), EN, ZH, TT; фолбэк на RU с бейджем.
- Тест после карточки: 3 вопроса, объяснение, счёт.
- Поиск: inline-режим + поиск в меню.
- ИИ в личке и группах: RAG по карточкам, триггер — упоминание/реплай.
- Красивый вывод: Rich Messages (заголовки, таблицы, details, blockquote, footer).
- Рассылка админом, статистика, тумблеры разделов.
- Воспроизводимый деплой на Podman Compose.

Не-цели первой версии:
- Mini App, платежи, модерация чатов, видео/аудио.
- Редактор карточек в боте (контент — JSON в репозитории + сид в БД).
- Виджеты Bot API 10.4 (private-only, 4 КБ после сжатия, слой клиента 233).
- Память диалога ИИ: каждый вопрос самостоятельный.

Анти-цели:
- Ответ дольше 1 с на навигацию (без LLM) и дольше 8 с на LLM-ответ.
- MarkdownV2; только HTML и Rich HTML.
- FSM для навигации: экраны stateless.

## 3. Ограничения (проверено)

- aiogram 3.31: Python `>=3.11,<3.15`; локально 3.14.8, системный 3.15.2 не подходит.
- В aiogram 3.31 есть `InputRichMessage`, `send_rich_message`, `EditMessageText.rich_message`,
  `InlineKeyboardButton.style` — сырой адаптер не нужен (проверено по колесу 3.31.0).
- Custom emoji (`<tg-emoji>`) разрешены ботам с Fragment-username **или** если у
  владельца бота Telegram Premium (core.telegram.org/bots/api/formatting.md:78-79,120-121).
- Callback data ≤64 байта. Rich-кнопки `tg-button` с callback_data приходят как текст,
  не callback (`raw/iva-agent/.../rich-replies/SKILL.md:158-160`) → навигация только
  через `reply_markup`.
- Inline-режим не умеет Rich Messages: только `InlineQueryResult*` + HTML ≤4096.
- Podman 6.2.0 установлен, машина не создана (`podman machine init`); `podman compose`
  работает через внешний compose-провайдер версии 5.6.1.
- Очередь задач — Postgres; dedupe `update_id` и rate limit требуют Redis:
  без него бот стартует, но защита от повторов и лимиты отключены (см. §11).
- `src/i18n/{index,en,ru,zh}.ts` — мёртвый TypeScript-скаффолд от генератора:
  Python-бот его не импортировал, локали живут в `app/i18n/*.json`. Удалён 2026-11-20.

## 4. Подходы и решение

Навигация:
- **A (выбрано): stateless callback-экраны.** Данные экрана в `callback_data`,
  сообщение редактируется на месте. Нет FSM, рестарт бесплатен.
- B: FSM-мастер — персистентное состояние, больше кода и багов. Отклонено.
- C: Mini App — отдельный хостинг и фронтенд. Отклонено.

Поиск:
- **1 (выбрано): in-memory индекс.** 60–100 карточек грузятся при старте, поиск по
  `casefold` + синонимам, <1 мс. Postgres — источник правды, не поисковик.
- 2: Postgres FTS/pg_trgm — сетевая латентность и миграции ради 100 строк. Отклонено.

ИИ:
- **RAG с локальным индексом (выбрано):** на вопрос ищем 3 карточки, кладём их текст
  в system prompt, модель отвечает ≤700 знаков и называет раздел. Без памяти, без
  инструментов. Дешевле и точнее, чем «свободная» модель по БЖД.
- Полный агент с веб-поиском — дороже, медленнее, риск выдумок. Отклонено.

Рендеринг:
- **Rich HTML + inline keyboard со стилями.** Шаблон экрана: `h1` → лид → `hr` →
  секции/`details` → `footer`.
- Обычный `sendMessage(parse_mode=HTML)` — для служебных ответов и inline-режима.

## 5. Спецификация бота (BotSpec)

| Поле | Значение |
|---|---|
| stack | `aiogram` |
| features | `commands`, `inline`, `llm`, `broadcast`, `rich` |
| storage.postgres | `true` |
| storage.redis | `true` — обязателен для dedupe `update_id` и rate limit |
| runtime.queue | `postgres` |
| i18n.defaultLocale | `ru` |
| i18n.enabled | `en`, `ru`, `zh` (+ `tt` вручную после скаффолда) |
| admin ids | `1715051614` |
| deploy | прод — нативно: uv + supervisor на Ubuntu 24.04; Dockerfile + compose.yaml — локально |
| mode | polling в проде и локально; webhook — опция (Caddy) |

`tt` не входит в enum BotSpec (`docs/BOT_SPEC.md:31`). Добавляется после генерации:
`app/i18n/tt.json` + `"tt"` в `ENABLED_LOCALES`/`resolve_locale`.

LLM (env): `LLM_BASE_URL=https://plusvibeapi.ru/v1`, `LLM_MODEL` (старт —
`openai/gpt-5.6-luna`, запасной `claude-haiku-5.5`), `LLM_API_KEY_FILE`.
Провайдер OpenAI-совместимый, таймаут 20 с, 1 повтор только на 429/5xx.

## 6. Данные

```
category(slug PK, sort, emoji, enabled)
threat(slug PK, category_slug FK, severity, sort, enabled, content_version)
threat_text(threat_slug FK, locale, title, summary, signs jsonb, before jsonb,
            during jsonb, after jsonb, forbidden jsonb, contacts jsonb,
            source text, content_version, PK(threat_slug, locale))
quiz_question(threat_slug FK, question_id, locale, prompt, options jsonb,
              answer int, explanation text, content_version,
              PK(threat_slug, question_id, locale))
quiz_answer(telegram_id, threat_slug, question_id, session_seed, chosen,
            correct, created_at)          -- один INSERT на ответ
user(telegram_id PK, locale, started_at, last_seen_at, broadcasts_opt_in)
settings(key PK, value jsonb)             -- тумблеры, черновик рассылки
```

Правила:
- Сид идемпотентен: upsert по ключу при `content_version >` текущей. `enabled`,
  `user`, `quiz_answer` не перетираются.
- `question_id` стабилен в JSON (`q1`, `q2`, `q3`) — попытки не ломаются при reseed.
- Выбор локали: `user.locale → ru`; если строки нет — RU + бейдж. Тест берётся в
  разрешённой локали; кнопка «Тест» видна, если есть хоть один вопрос после фолбэка.
- Индекс поиска: title + summary + `content/aliases.json` (alias → slug, на локаль).

Контент: `content/<category>.json`, 8 категорий:
`fire`, `terror`, `tech`, `nature`, `medical`, `chem`, `rad`, `cyber`.
Severity 1–3, рендер — `●●○` + подпись «Высокая/Средняя/Низкая».
```json
{"version": 3, "category": {...}, "threats": [
 {"slug": "fire-home", "severity": 3, "texts": {"ru": {...}, "en": {...}},
  "quiz": {"ru": [{"id": "q1", "prompt": "...", "options": ["..."],
                   "answer": 0, "explanation": "..."}]}}
]}
```

## 7. UX

Все экраны — редактирование одного сообщения в личке. В группе бот отвечает
отдельным сообщением реплаем, меню не показывает.

1. `/start` — `h1`, 2–3 строки лида, `hr`, сетка 8 категорий (2 в ряд),
   кнопки «Поиск», «Язык». Deep link `/start card_<slug>` сразу открывает карточку.
2. Категория — список угроз, пагинация `‹ 1/3 ›` одной строкой.
3. Карточка — `h1` + severity, `blockquote` «Признаки», `details` «До / Во время /
   После», `details` «Нельзя», `footer` с телефоном и источником; кнопки «Тест»
   (primary), «Назад» (возврат на ту же страницу), «Поделиться».
4. Тест — вопрос, 4 варианта; после ответа верный — `success`, неверный — `danger`,
   объяснение, «Дальше»; итог `X/3`, «Ещё раз» (новый seed).
5. Поиск — любой текст в личке = поисковый запрос (без флагов и FSM);
   кнопка «Поиск» лишь подсказывает. Нет результатов → подсказки категорий.
   Под результатами — кнопка «Спросить Байта» (`/ask <текст>`): ИИ в личке
   включается только явно, обычный текст никогда не уходит в модель.
6. Язык — 4 кнопки, пишут `user.locale` и перерисовывают текущий экран.
7. `/admin` — тумблеры категорий, рассылка, статистика.
8. Группа — триггер: `@bot` в тексте, реплай на сообщение бота, `/ask <вопрос>`.
   Ответ ≤700 знаков HTML, реплай на вопрос, кнопка «Открыть карточку» (deep link).
   Privacy mode у BotFather остаётся включённым.

Навигация: `callback_data` = `b1:<action>:<args>` (≤64 байта):
`b1:menu`, `b1:cat:<slug>`, `b1:cat:<slug>:<page>`, `b1:card:<slug>:<page>`,
`b1:quiz:<slug>:<q>:<opt>:<seed>`, `b1:quiz:<slug>:next:<seed>:<q>`,
`b1:lang:<code>`, `b1:admin`, `b1:bc:<yes|no>`.

Тест:
- Вопросы и варианты перемешиваются seed'ом из callback (воспроизводимо).
- Каждый ответ — один INSERT в `quiz_answer`; счёт считается по `session_seed`.
- Foreign click (чужой callback) → alert, экран не меняется.
- Ретака = новый seed, старые строки остаются для статистики.

## 7.1 Правила API (из `.agents/skills`)

- Callback: всегда `answerCallbackQuery` (даже пустой ack); `callback_data` короткая,
  версионная, безопасная к повтору; экран — edit, не новое сообщение.
- Команды: `/start`, `/help`, `/settings` (язык, рассылка), `/admin`. Описания команд
  локализуются через `setMyCommands(language_code=...)` для `ru/en/zh` + `tt`.
  Scope команд не заменяет проверку прав.
- Inline: включается в BotFather; ответ — `InlineQueryResultArticle` с HTML ≤4096,
  `cache_time` 30 с и `is_personal=False` (результаты одинаковы для всех);
  no-result → подсказки категорий. Deep link `/start card_<slug>` — короткий,
  разбирается на сервере.
- Обновления: ровно один режим (webhook в проде, polling локально), `allowed_updates`
  явный, dedupe `update_id`, `retry_after` соблюдается.
- Секреты: токен = root; только `.env`/`*_FILE`, не логируется; webhook secret
  проверяется по заголовку; PII минимизируем (id, locale, opt-in, ответы теста).
- Группы: privacy mode включён; бот получает только команды, упоминания и реплаи —
  триггер ИИ строится на этом, без чтения всей переписки.

## 8. Слой вывода (перенос паттернов из raw/)

Из `raw/duckmods` (aiogram 3.31, ближайший аналог):
- один экран = одно сообщение, `show()` редактирует по `message_id`;
- лестница деградации: `not modified` → успех, устаревшее сообщение → новая копия,
  отказ `<tg-emoji>` → повтор без премиум-эмодзи только для этого экрана;
- `<table bordered striped compact>`, `<details>`, `<footer>` с фактами через `·`,
  `<tg-time format="wDT">`;
- семантика кнопок: ровно один `primary`, `success` — успех, `danger` — ошибка/назад
  только для разрушительного; навигация нейтральная.

Из `raw/iishko-public`:
- неймспейс `callback_data` и лимит длины;
- кастомный квиз на callback вместо native poll (нужен красивый экран и объяснение);
- пагинация и «Назад» в одной строке;
- в группе отвечать только на упоминание/реплай/имя.

Из `raw/iva-agent`:
- rich → HTML фолбэк при отказе rich;
- `tg-emoji` только в rich; служебные ответы — без премиум-эмодзи;
- виджеты и эфемерные ответы не используем.

Эмодзи: `name → {id, char}` в `app/ui/emoji.py`; id из `TELEGRAM_EMOJI_IDS` или
стикер-пака; без id — обычный символ. Контент хранит только имена (`"icon": "fire"`).

## 9. ИИ

- Триггеры: `/ask` в личке и в группе, `@bot` в группе, реплай на сообщение бота.
  Обычный текст в личке — только поиск; в модель он не уходит (`docs/DESIGN.md` §7.5).
- Вход: текст вопроса (+ цитата, если это реплай). Локальный поиск → 3 карточки.
- Prompt: роль «инструктор БЖД», карточки как факты, правила: только по карточкам,
  ≤700 знаков, без выдуманных телефонов, при угрозе жизни — «звоните 112», не давать
  медицинских диагнозов. Язык ответа = `user.locale`.
- Выход: HTML (b/i/code), обрезка по границе тега, реплай на вопрос.
- Лимиты: 5 запросов/мин на пользователя, 20/мин на чат; token bucket в памяти.
  Группа: не больше 1 ответа на 10 с.
- Отказ/таймаут/нет ключа: ответ «вот что нашёл» + 3 результата поиска с кнопками.
- Логи: только счётчики и латентность; текст промптов и ответов не пишем.

## 10. Рассылка и админка

- Очередь Postgres: задача `broadcast`, 25 сообщений/с, пауза 1 с, курсор в БД,
  пропуск `chat not found`/`bot was blocked`, `/stop` → `broadcasts_opt_in=false`.
- Черновик рассылки: `settings['broadcast_draft:<admin_id>']` (HTML, created_at),
  TTL 1 час; предпросмотр `sendRichMessage` себе, затем «Отправить»/«Отмена».
- `/admin`: тумблеры категорий, счётчики (пользователи, карточки, тесты), запуск
  рассылки с подтверждением.

## 11. Эксплуатация

- `compose.yaml`: bot + worker + migrate + Postgres + Redis + Caddy. Dockerfile
  `python:3.14-slim`, non-root, `alembic upgrade head`, health `/healthz`.
- Прод: Postgres 16 и Redis 7 из apt на том же хосте, `bot` и `worker` под
  supervisor (`/etc/supervisor/conf.d/bytebot.conf`), `.env` в `/opt/bytebot`
  с правами 600, логи `/var/log/bytebot-*.log`. Режим — polling: наружу ничего
  не публикуется, порт 8080 нужен только для `/healthz`, `/readyz`, `/metrics`.
- Локально: `podman machine init && podman machine start`,
  `podman compose up -d --build`; точные команды — `docs/RUN.md`.
  Rootless порты 80/443 — `net.ipv4.ip_unprivileged_port_start=80`.
- Секреты через `.env` / `*_FILE`; токен и LLM-ключ в чат не попадают.
- Redis обязателен: без него отключены dedupe `update_id` (повторы апдейтов) и
  rate limit (флуд и лимиты ИИ). `readyz` возвращает 503, если Redis недоступен.
- Миграция Alembic `0001_content`.
- Наблюдаемость: структурные логи, dedupe `update_id`, `retry_after`.

## 12. Тесты

- Сид: двойной запуск не дублирует строки и не сбрасывает `enabled`.
- Локаль: `tt → ru` с бейджем; `en` не откатывается; смена языка перерисовывает экран.
- Поиск: «пожар», «пожара», «fire» → одна карточка; пустой запрос → подсказки.
- Навигация: каждый экран рендерится из `callback_data`, `Назад` возвращает страницу.
- Квиз: верный/неверный ответ, foreign click, seed-перемешивание, счёт 3/3.
- LLM: фейковый OpenAI-совместимый сервер — успех, таймаут, 500, пустой ответ,
  отсутствие ключа → фолбэк в поиск; лимит 5/мин.
- Группа: отвечает на `@bot` и реплай, молчит на остальное.
- Рассылка: пауза/резюм на фейковом Bot API.
- Живой smoke: `podman compose up`, polling, `/start` → карточка → тест → вопрос ИИ.

## 13. Риски и открытые решения

| Риск | Решение |
|---|---|
| Premium emoji нет у владельца | обычные эмодзи; `<tg-emoji>` включается флагом, деградация на отказ |
| 240+ текстов + 720 вопросов | RU первым; вопросы генерируются офлайн-скриптом и вычитываются |
| Медленный LLM | таймаут 20 с, фолбэк в поиск, лимиты на пользователя и чат |
| Модель провайдера меняется | имя модели только в env, без хардкода |
| Виджеты 10.4 | не в MVP; отдельная фаза |
| Лицензии raw/ | duckmods без LICENSE: переносим паттерны, не копируем код дословно, если проект не ваш |
| Групповой спам | триггер только упоминание/реплай/`/ask`, rate limit, privacy mode включён |

## 14. Порядок сборки

1. `telegram_bot_target_resolve` → `telegram_bot_scaffold` (BotSpec из §5).
2. `telegram_bot_verify` на сгенерированном проекте.
3. Ручная локаль `tt`, слой `app/ui` (render/delivery/emoji/keyboards).
4. Миграция `0001_content`, модели, сид, in-memory индекс.
5. Хэндлеры: меню/категория/карточка/тест/поиск/inline/язык.
6. ИИ: клиент, RAG-промпт, лимиты, групповой триггер, фолбэк.
7. Админка, рассылка, статистика.
8. Контент: 8 категорий × 7–8 угроз, RU; вопросы офлайн-скриптом.
9. Переводы EN/ZH/TT по мере готовности.
10. `podman compose up`, smoke, README с деплоем.
