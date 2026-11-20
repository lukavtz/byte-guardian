# Security audit — chat, LLM and admin surfaces

Date: 2026-11-20
Scope (read-only): `app/services/llm.py`, `app/services/conversation.py`,
`app/handlers/llm.py`, `app/handlers/admin/*`, `app/ui/draft.py`.
Support files read for the data flow: `app/services/callbacks.py`,
`app/services/web_search.py`, `app/services/prompt.py`, `app/middlewares/allowlist.py`,
`app/ui/keyboards.py`, `app/ui/render.py`, `app/ui/delivery.py`, `app/config.py`, `app/bot.py`.

## Result

No P0 (remote compromise / money loss) and no P1 (data loss / privilege escalation)
finding in the assigned files. Nothing was changed in code. Four P2 and two P3
hardening items are recorded below; they do not block the release.

## Findings

| id | severity | file:line | impact | smallest fix |
|----|----------|-----------|--------|--------------|
| SEC-01 | P2 | `app/ui/draft.py:135,144-146` | In group chats the streaming status sends **raw** model deltas via `send_message`/`edit_message_text`. Bot default `parse_mode` is HTML (`app/bot.py:78`), so injected `<a href>`, `<b>`, entities or unclosed tags render or break the message; three breaks disable the sink. The final answer is sanitized, so the defect is limited to intermediate status. | Pass `text` through `strip_html()` (already imported by the caller) before both calls in `StatusEditSink.push`. |
| SEC-02 | P2 | `app/services/llm.py:37-43,485-514`; `app/handlers/llm.py:59` | `RateLimiter` buckets live in process memory. A restart clears all limits; two replicas each allow the full 5/min and 20/min budget, so the per-chat and group limits are bypassed horizontally. | Store the three buckets in the already-configured Redis with `SET key 1 NX EX`, falling back to memory only in local mode. |
| SEC-03 | P2 | `app/services/conversation.py:21,100-102` | The 5000-conversation cap uses insertion-order eviction, so one chatty user in a busy group can evict other users' scratchpads. Memory stays bounded (no unbounded growth), but history becomes unreliable. | Evict by oldest `Turn.at`, or scope the cap per chat. |
| SEC-04 | P2 | `app/services/llm.py:198-208`; `app/services/web_search.py:282-292`; `app/services/prompt.py:17-20` | All prompt-injection defences for `<history>`, web snippets and cards are instructions to the model, not a boundary. There is no tool use or irreversible action downstream, so impact stops at text — but SEC-01 turns a successful injection into rendered HTML in group status. | Fix SEC-01; keep the final `strip_html()` path as the actual trust boundary. No prompt rewrite needed. |
| SEC-05 | P3 | `app/handlers/llm.py:150-164` | `SOURCE_RE` picks URLs from model text and only checks membership in the retrieved set. A poisoned search result returned by the upstream relay therefore becomes a clickable button; `http://` is accepted. | Require `https://` and an `is_official()` host (helper already exists in `web_search.py`) before building a source row. |
| SEC-06 | P3 | `app/handlers/llm.py:298-308` | `asyncio.create_task(_keep_typing(...))` is created after the LLM request is built but before the reply is sent. It is always cancelled and awaited in `finally`, so no cross-update leak; noted only because create_task generally deserves a retry review. | No change required. |

## Controls verified present

- **Secrets in logs / storage**: `answer()` and `stream_answer()` log only status, latency and token count (`app/services/llm.py:255-348`). No token-shaped literal in tracked sources; no `bot_token = "…"` assignment; no log line containing token, secret, initData or payload. `conversation._scrub()` removes bearer tokens, `digits:token` patterns, `sk-…`, `LLM_API_KEY`/`BOT_TOKEN` values and URLs **before** a turn is stored (`app/services/conversation.py:36-48`).
- **Admin authorization**: numeric Telegram ids only, via `is_admin(user.id, settings.admin_id_set)` (`app/middlewares/allowlist.py:17-18`). Re-checked in every privileged handler: `/admin` and every admin callback (`app/handlers/admin/home.py:238,256`), text edits (`app/handlers/admin/content.py:372`), `/cancel` and `/start` (`:515,529`), all broadcast commands and callbacks (`app/handlers/broadcast.py:81,107,280,328`). Hiding the menu button is not the only check.
- **callback_data**: `parse_cb()` rejects payloads over 64 bytes, unknown actions and malformed shapes (`app/services/callbacks.py:342-433`); `_pack()` fails closed on oversize (`:94-98`); slugs, locales, indices, options and uuids are validated. Pending edits are keyed by the *update's* `from_user.id`, never by callback content. Admin callback branches all ACK; a `CallbackAckMiddleware` is registered dispatcher-wide (`app/bot.py:133`).
- **Rate limits**: user/chat/group token buckets in `app/services/llm.py:463-514`, applied before any model call (`app/handlers/llm.py:242-248`); dispatcher also has a Redis-backed message rate limiter (`app/bot.py:151`). See SEC-02 for the in-process caveat.
- **Untrusted URLs → buttons**: `_cited_sources()` only accepts URLs that were actually retrieved, and deduplicates them (`app/handlers/llm.py:150-164`); the final text has all raw URLs stripped (`:312`, `app/services/llm.py:80-85`). Relay results are scheme- and host-checked (`app/services/web_search.py:109-122`). See SEC-05.
- **HTML injection**: model output passes `strip_answer_urls` → `strip_intro` → `strip_html` before the final message (`app/handlers/llm.py:312`), and `draft_html()` uses the same allowlist cleaner (`app/services/llm.py:88-101,395-460`). Admin screens escape every editable field (`_esc` in `app/handlers/admin/common.py:62`, used across `content.py`, `home.py`, `broadcast.py`) and `rich_button()` escapes both label and attributes (`app/ui/keyboards.py:62-88`). See SEC-01 for the group-status exception.
- **Unbounded history**: `ConversationStore` caps turns at 6 × 500 chars, TTL 1800 s and 5000 conversations (`app/services/conversation.py:19-21,68-102`). Streaming chunks are capped by `DRAFT_CHARS = 3500` and `TEXT_LIMIT = 1200`.
- No bare `except: pass`, no `MemoryStorage` FSM in a production dispatcher, no `create_all`/`drop` in startup.

## Verification performed

- Targeted `rg` battery for token literals, secret logging, callback splitting, bare excepts and `create_task` over the assigned files — no P0/P1 hits.
- Manual end-to-end trace of each privileged path (command → filter/middleware → handler → DB write) and of each LLM path (update → rate limit → prompt build → stream/answer → sanitize → buttons → history).
- Static reading only: no live bot, no network calls, no test suite run. Findings are from code inspection; none were reproduced at runtime.
