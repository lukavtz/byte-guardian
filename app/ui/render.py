"""Rich HTML screens: one function per screen, no I/O.

Every screen is a `RichScreen` whose buttons live inside the rich message
(`<tg-button-row>`), so the controls scroll with the text and no keyboard is
attached underneath. `app.ui.delivery.show` puts the screen into a chat.

Content is user-editable JSON, so every field is escaped and clipped here: the
markup never carries a tag the author did not intend, and no card can push the
message past Telegram's 32 000 UTF-16 ceiling.
"""

from __future__ import annotations

import secrets
from dataclasses import dataclass
from html import escape

from aiogram.types import InlineKeyboardMarkup

from app.content.types import CardView, CategoryView, QuizView, ThreatView
from app.services.callbacks import (
    cb_card,
    cb_cat,
    cb_game,
    cb_lang_menu,
    cb_menu,
    cb_quiz,
    cb_quiz_next,
    cb_search_hint,
)
from app.services.i18n import t
from app.ui.emoji import emoji
from app.ui.keyboards import PAGE_SIZE, Style, rich_button, rich_row

TITLE = 120
SUMMARY = 900
ITEM = 240
ITEMS = 6
RESULTS = 7
SEVERITIES = {1: "low", 2: "medium", 3: "high"}
ART = "https://raw.githubusercontent.com/lukavtz/byte-guardian/main/assets/art"
ARTS = frozenset({"fire", "terror", "tech", "nature", "medical", "chem", "rad", "cyber"})


@dataclass(frozen=True, slots=True)
class RichScreen:
    """A rich message; `keyboard` is only for screens with no rich form."""

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


def _dots(severity: int) -> str:
    level = min(max(severity, 1), 3)
    return f"{'●' * level}{'○' * (3 - level)}"


def _items(items: tuple[str, ...], locale: str) -> str:
    return "<br/>".join(f"• {_esc(_clip(item, ITEM))}" for item in items[:ITEMS])


def _badge(card: CardView, locale: str) -> str:
    return f" · <b>{_esc(t(locale, 'badge.fallback'))}</b>" if card.fallback else ""


def _art(slug: str) -> str:
    """One picture above the heading; an unknown section borrows the hero."""
    return f'<img src="{ART}/{slug if slug in ARTS else "hero"}.png"/>'


def _art_of_threat(slug: str) -> str:
    """A threat slug starts with its category (`fire-home` -> `fire`)."""
    return slug.split("-", 1)[0]


def _header(slug: str, heading: str, lead: str = "") -> str:
    """Art, heading, one lead block, rule: the shape every screen shares."""
    return f"{_art(slug)}<h1>{heading}</h1>{lead}<hr/>"


def render_menu(categories: list[CategoryView], locale: str) -> RichScreen:
    rows = [
        rich_row(
            rich_button(f"{emoji(item.slug, item.emoji)} {_clip(item.title, TITLE)}", data=cb_cat(item.slug))
        )
        for item in categories[:8]
    ]
    rows.append(
        rich_row(
            rich_button(f"{emoji('search', '🔎')} {t(locale, 'button.search')}", data=cb_search_hint()),
            rich_button(f"{emoji('language', '🌐')} {t(locale, 'button.language')}", data=cb_lang_menu()),
        )
    )
    heading = f"{emoji('shield', '🛡️')} {_esc(t(locale, 'menu.title'))}"
    html = _header("hero", heading, f"<p>{_esc(t(locale, 'menu.lead'))}</p>")
    return RichScreen(html + "\n".join(rows))


def render_category(
    category: CategoryView, threats: list[ThreatView], locale: str, page: int, pages: int
) -> RichScreen:
    rows = [
        rich_row(rich_button(f"{_dots(item.severity)} {_clip(item.title, TITLE)}", data=cb_card(item.slug, page)))
        for item in threats[:PAGE_SIZE]
    ]
    nav = []
    if page > 1:
        nav.append(rich_button(t(locale, "button.prev"), data=cb_cat(category.slug, page - 1)))
    if page < pages:
        nav.append(rich_button(t(locale, "button.next"), data=cb_cat(category.slug, page + 1)))
    if nav:
        rows.append(rich_row(*nav))
    rows.append(rich_row(rich_button(f"{emoji('menu', '📂')} {t(locale, 'button.menu')}", data=cb_menu())))
    lead = (
        f"{t(locale, 'category.count', n=category.threat_count)}"
        f" · {t(locale, 'category.page', page=page, pages=pages)}"
    )
    heading = f"{emoji(category.slug, _esc(category.emoji))} {_esc(_clip(category.title, TITLE))}"
    detail = f"{lead} · {t(locale, 'category.lead')}"
    html = _header(category.slug, heading, f"<p>{_esc(detail)}</p>")
    return RichScreen(html + "\n".join(rows))


def render_card(
    card: CardView, locale: str, page: int, has_quiz: bool, can_game: bool = True
) -> RichScreen:
    steps = "".join(
        f"<details><summary>{_esc(t(locale, key))}</summary>{_items(items, locale)}</details>"
        for key, items in (
            ("card.before", card.before),
            ("card.during", card.during),
            ("card.after", card.after),
        )
    )
    forbidden = (
        f"<details><summary>{_esc(t(locale, 'card.forbidden'))}</summary>"
        f"{_items(card.forbidden, locale)}</details>"
    )
    contacts = " · ".join(_esc(_clip(item, ITEM)) for item in card.contacts[:ITEMS])
    html = (
        _header(
            card.category_slug,
            f"{emoji(card.category_slug, _esc(card.icon))} {_esc(_clip(card.title, TITLE))}",
            f"<p>{_severity(card.severity, locale)}"
            f" · {_esc(_clip(card.category_title, TITLE))}{_badge(card, locale)}</p>",
        )
        + f"<p>{_esc(_clip(card.summary, SUMMARY))}</p>"
        + f"<blockquote><b>{_esc(t(locale, 'card.signs'))}</b><br/>{_items(card.signs, locale)}</blockquote>"
        + steps
        + forbidden
        + f"<footer>{contacts} · {_esc(_clip(card.source, ITEM))}</footer>"
    )
    rows = []
    if has_quiz:
        quiz_buttons = [
            rich_button(
                f"{emoji('quiz', '📝')} {t(locale, 'button.quiz')}",
                data=cb_quiz_next(card.slug, "start", secrets.token_hex(2)),
                style="primary",
            )
        ]
        if can_game:
            quiz_buttons.append(
                rich_button(
                    f"{emoji('game', '🎮')} {t(locale, 'button.game')}",
                    data=cb_game(card.slug),
                )
            )
        rows.append(rich_row(*quiz_buttons))
    rows.append(
        rich_row(
            rich_button(f"← {t(locale, 'button.back')}", data=cb_cat(card.category_slug, page)),
            rich_button(t(locale, "button.share"), query=_clip(card.title, TITLE)),
        )
    )
    return RichScreen(html + "\n".join(rows))


def render_question(
    quiz: QuizView,
    slug: str,
    index: int,
    total: int,
    seed: str,
    locale: str,
    chosen: int | None = None,
) -> RichScreen:
    """A question; with `chosen` it shows the verdict on the same screen."""
    rows = []
    for number, option in enumerate(quiz.options[:4]):
        style: Style | None = None
        if chosen is not None:
            if number == quiz.answer:
                style = "success"
            elif number == chosen:
                style = "danger"
        rows.append(
            rich_row(
                rich_button(
                    f"{number + 1}. {_clip(option, ITEM)}",
                    data=cb_quiz(slug, quiz.question_id, number, seed),
                    style=style,
                )
            )
        )
    if chosen is not None:
        tail = (
            f"<blockquote><b>{_esc(t(locale, 'quiz.explanation'))}</b><br/>"
            f"{_esc(_clip(quiz.explanation, SUMMARY))}</blockquote>"
        )
        rows.append(
            rich_row(
                rich_button(
                    t(locale, "button.next_question"),
                    data=cb_quiz_next(slug, quiz.question_id, seed),
                    style="primary",
                )
            )
        )
    else:
        tail = f"<p>{_esc(t(locale, 'quiz.hint'))}</p>"
    html = (
        _header(
            _art_of_threat(slug),
            f"{emoji('quiz', '📝')} {_esc(t(locale, 'quiz.title', index=index, total=total))}",
            f"<h3>{_esc(_clip(quiz.prompt, SUMMARY))}</h3>",
        )
        + tail
    )
    return RichScreen(html + "\n".join(rows))


def render_result(correct: int, total: int, slug: str, locale: str) -> RichScreen:
    verdict = "perfect" if total and correct == total else ("partial" if correct else "zero")
    passed = bool(total) and correct * 3 >= total * 2
    rows = [
        rich_row(
            rich_button(
                f"{emoji('refresh', '🔄')} {t(locale, 'button.retry')}",
                data=cb_quiz_next(slug, "start", secrets.token_hex(2)),
                style="success" if passed else "primary",
            )
        ),
        rich_row(rich_button(f"{emoji('menu', '📂')} {t(locale, 'button.menu')}", data=cb_menu())),
    ]
    html = _header(
        _art_of_threat(slug),
        f"{emoji('done', '✅')} {correct}/{total}",
        f"<p>{_esc(t(locale, f'quiz.result.{verdict}'))}</p>",
    )
    return RichScreen(html + "\n".join(rows))


def search_screen(
    query: str,
    results: list[ThreatView],
    locale: str,
    hints: list[CategoryView] | None = None,
) -> RichScreen:
    if results:
        rows = [
            rich_row(
                rich_button(f"{_dots(item.severity)} {_clip(item.title, TITLE)}", data=cb_card(item.slug))
            )
            for item in results[:RESULTS]
        ]
    else:
        # No match: the eight sections are the useful next tap, in the message.
        rows = [
            rich_row(rich_button(f"{emoji(item.slug, item.emoji)} {_clip(item.title, TITLE)}", data=cb_cat(item.slug)))
            for item in (hints or [])[:8]
        ]
    rows.append(rich_row(rich_button(f"{emoji('menu', '📂')} {t(locale, 'button.menu')}", data=cb_menu())))
    clean = _clip(query, TITLE)
    if results:
        body = f"<p>{_esc(t(locale, 'search.found', n=len(results)))}</p>"
    else:
        body = f"<p>{_esc(t(locale, 'search.empty'))}</p><p>{_esc(t(locale, 'search.hint'))}</p>"
    quote = f"<blockquote>{_esc(clean)}</blockquote>" if clean else ""
    html = _header("hero", f"{emoji('search', '🔎')} {_esc(t(locale, 'search.title'))}")
    html += quote + body
    return RichScreen(html + "\n".join(rows))