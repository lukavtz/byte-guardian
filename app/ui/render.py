"""Rich HTML screens: one function per screen, no I/O.

Every screen is a `RichScreen` (markup + keyboard) that `app.ui.delivery.show`
puts into a chat. Content is user-editable JSON, so every field is escaped and
clipped here: the markup never carries a tag the author did not intend, and no
card can push the message past Telegram's 32 000 UTF-16 ceiling.
"""

from __future__ import annotations

import secrets
from dataclasses import dataclass
from html import escape

from aiogram.types import InlineKeyboardMarkup

from app.content.types import CardView, CategoryView, QuizView, ThreatView
from app.services.callbacks import cb_card, cb_cat, cb_menu, cb_quiz, cb_quiz_next
from app.services.i18n import t
from app.ui.emoji import emoji
from app.ui.keyboards import MAX_BUTTONS, PAGE_SIZE, button, markup, paired

TITLE = 120
SUMMARY = 900
ITEM = 240
ITEMS = 6
RESULTS = 7
SEVERITIES = {1: "low", 2: "medium", 3: "high"}


@dataclass(frozen=True, slots=True)
class RichScreen:
    html: str
    keyboard: InlineKeyboardMarkup | None = None


def _esc(text: object) -> str:
    return escape(str(text), quote=False)


def _clip(text: object, limit: int) -> str:
    value = " ".join(str(text).split())
    return value if len(value) <= limit else value[: limit - 1].rstrip() + "…"


def _severity(severity: int, locale: str) -> str:
    level = min(max(severity, 1), 3)
    return f"{'●' * level}{'○' * (3 - level)} {t(locale, f'severity.{SEVERITIES[level]}')}"


def _items(items: tuple[str, ...], locale: str) -> str:
    return "<br/>".join(f"• {_esc(_clip(item, ITEM))}" for item in items[:ITEMS])


def _badge(card: CardView, locale: str) -> str:
    return f" · <b>{_esc(t(locale, 'badge.fallback'))}</b>" if card.fallback else ""


def render_menu(categories: list[CategoryView], locale: str) -> RichScreen:
    buttons = [
        button(f"{emoji(item.slug, item.emoji)} {_clip(item.title, TITLE)}", cb_cat(item.slug))
        for item in categories[:MAX_BUTTONS]
    ]
    rows = paired(buttons)
    html = (
        f"<h1>{emoji('shield', '🛡️')} {_esc(t(locale, 'menu.title'))}</h1>"
        f"<p>{_esc(t(locale, 'menu.lead'))}</p>"
        f"<hr/>"
        f"<p>{_esc(t(locale, 'menu.hint'))}</p>"
    )
    return RichScreen(html, markup(rows))


def render_category(
    category: CategoryView, threats: list[ThreatView], locale: str, page: int, pages: int
) -> RichScreen:
    rows: list[list] = [
        [button(f"{_severity(item.severity, locale)[:3]} {_clip(item.title, TITLE)}", cb_card(item.slug, page))]
        for item in threats[:PAGE_SIZE]
    ]
    if pages > 1:
        nav = []
        if page > 1:
            nav.append(button(t(locale, "button.prev"), cb_cat(category.slug, page - 1)))
        if page < pages:
            nav.append(button(t(locale, "button.next"), cb_cat(category.slug, page + 1)))
        if nav:
            rows.append(nav)
    rows.append([button(t(locale, "button.menu"), cb_menu())])
    html = (
        f"<h1>{emoji(category.slug, category.emoji)} {_esc(_clip(category.title, TITLE))}</h1>"
        f"<p>{_esc(t(locale, 'category.count', n=category.threat_count))}"
        f" · {_esc(t(locale, 'category.page', page=page, pages=pages))}</p>"
        f"<hr/>"
        f"<p>{_esc(t(locale, 'category.lead'))}</p>"
    )
    return RichScreen(html, markup(rows))


def render_card(card: CardView, locale: str, page: int, has_quiz: bool) -> RichScreen:
    actions = (
        f"<b>{_esc(t(locale, 'card.before'))}</b><br/>{_items(card.before, locale)}<br/>"
        f"<b>{_esc(t(locale, 'card.during'))}</b><br/>{_items(card.during, locale)}<br/>"
        f"<b>{_esc(t(locale, 'card.after'))}</b><br/>{_items(card.after, locale)}"
    )
    contacts = " · ".join(_esc(_clip(item, ITEM)) for item in card.contacts[:ITEMS])
    html = (
        f"<h1>{emoji(card.category_slug, card.icon)} {_esc(_clip(card.title, TITLE))}</h1>"
        f"<p>{_severity(card.severity, locale)} · {_esc(_clip(card.category_title, TITLE))}{_badge(card, locale)}</p>"
        f"<p>{_esc(_clip(card.summary, SUMMARY))}</p>"
        f"<blockquote><b>{_esc(t(locale, 'card.signs'))}</b><br/>{_items(card.signs, locale)}</blockquote>"
        f"<details><summary>{_esc(t(locale, 'card.actions'))}</summary>{actions}</details>"
        f"<details><summary>{_esc(t(locale, 'card.forbidden'))}</summary>{_items(card.forbidden, locale)}</details>"
        f"<footer>{contacts} · {_esc(_clip(card.source, ITEM))}</footer>"
    )
    nav = [
        button(t(locale, "button.back"), cb_cat(card.category_slug, page)),
        button(t(locale, "button.share"), switch_inline_query_current_chat=_clip(card.title, TITLE)),
    ]
    rows = []
    if has_quiz:
        rows.append(
            [button(t(locale, "button.quiz"), cb_quiz_next(card.slug, "start", secrets.token_hex(2)), "primary")]
        )
    rows.append(nav)
    return RichScreen(html, markup(rows))


def render_question(quiz: QuizView, slug: str, index: int, total: int, seed: str, locale: str) -> RichScreen:
    options = [
        button(f"{number + 1}. {_clip(option, ITEM)}", cb_quiz(slug, quiz.question_id, number, seed))
        for number, option in enumerate(quiz.options[:4])
    ]
    rows = paired(options)
    rows.append([button(t(locale, "button.menu"), cb_menu())])
    html = (
        f"<h1>{emoji('quiz', '📝')} {_esc(t(locale, 'quiz.title', index=index, total=total))}</h1>"
        f"<p>{_esc(_clip(quiz.prompt, SUMMARY))}</p>"
        f"<hr/>"
        f"<p>{_esc(t(locale, 'quiz.hint'))}</p>"
    )
    return RichScreen(html, markup(rows))


def render_result(correct: int, total: int, slug: str, locale: str) -> RichScreen:
    verdict = "perfect" if total and correct == total else ("partial" if correct else "zero")
    rows = [
        [button(t(locale, "button.retry"), cb_quiz_next(slug, "start", secrets.token_hex(2)), "primary")],
        [button(t(locale, "button.card"), cb_card(slug)), button(t(locale, "button.menu"), cb_menu())],
    ]
    html = (
        f"<h1>{emoji('done', '✅')} {correct}/{total}</h1>"
        f"<p>{_esc(t(locale, f'quiz.result.{verdict}'))}</p>"
        f"<footer>{_esc(t(locale, 'quiz.result.footer'))}</footer>"
    )
    return RichScreen(html, markup(rows))


def search_screen(query: str, results: list[ThreatView], locale: str) -> RichScreen:
    rows = [
        [button(f"{_severity(item.severity, locale)[:3]} {_clip(item.title, TITLE)}", cb_card(item.slug))]
        for item in results[:RESULTS]
    ]
    rows.append([button(t(locale, "button.menu"), cb_menu())])
    clean = _clip(query, TITLE)
    if results:
        body = f"<p>{_esc(t(locale, 'search.found', n=len(results)))}</p>"
    else:
        body = f"<p>{_esc(t(locale, 'search.empty'))}</p><p>{_esc(t(locale, 'search.hint'))}</p>"
    html = (
        f"<h1>{emoji('search', '🔎')} {_esc(t(locale, 'search.title'))}</h1>"
        f"{f'<blockquote>{_esc(clean)}</blockquote>' if clean else ''}"
        f"{body}"
    )
    return RichScreen(html, markup(rows))
