"""Rich HTML screens: one function per screen, no I/O.

Every screen is a `RichScreen` whose buttons live inside the rich message
(`<tg-button-row>`), so the controls scroll with the text and no keyboard is
attached underneath. `app.ui.delivery.show` puts the screen into a chat.

Content is user-editable JSON, so every field is escaped here: the markup never
carries a tag the author did not intend. Text is never trimmed for looks; the
only cap drops whole card list items when a message would pass Telegram's
32 000 UTF-16 ceiling.
"""

from __future__ import annotations

import secrets
from dataclasses import dataclass
from html import escape

from aiogram.types import InlineKeyboardMarkup

from app.content.types import CardView, CategoryView, QuizView, ThreatView
from app.services.callbacks import (
    cb_admin,
    cb_card,
    cb_cat,
    cb_chat,
    cb_chat_exit,
    cb_game,
    cb_lang_menu,
    cb_menu,
    cb_quiz,
    cb_quiz_next,
    cb_search_hint,
)
from app.services.i18n import t
from app.ui.emoji import premium
from app.ui.keyboards import PAGE_SIZE, Style, rich_button, rich_row

RESULTS = 7
CARD_CAP = 30000
SEVERITIES = {1: "low", 2: "medium", 3: "high"}
ART = "https://raw.githubusercontent.com/lukavtz/byte-guardian/main/assets/art"
ARTS = frozenset({"fire", "terror", "tech", "nature", "medical", "chem", "rad", "cyber", "chat"})


@dataclass(frozen=True, slots=True)
class RichScreen:
    """A rich message; `keyboard` is only for screens with no rich form."""

    html: str
    keyboard: InlineKeyboardMarkup | None = None


def _esc(text: object) -> str:
    return escape(str(text), quote=False)


def _utf16(text: str) -> int:
    return len(text.encode("utf-16-le")) // 2


def _items(items: tuple[str, ...], shown: int) -> str:
    """One item per line; a dropped tail leaves a single «…» marker."""
    lines = [f"• {_esc(item)}" for item in items[:shown]]
    if lines and shown < len(items):
        lines[-1] += " …"
    return "<br/>".join(lines)


def _details(key: str, body: str, locale: str, opened: bool = False, icon: tuple[str, str] | None = None) -> str:
    if not body:
        return ""
    flag = " open" if opened else ""
    lead = f"{premium(*icon)} " if icon else ""
    return f"<details{flag}><summary><h2>{lead}{_esc(t(locale, key))}</h2></summary>{body}</details>"


def _brand(locale: str) -> str:
    return f"<footer>{premium('duck', '🦆')} {_esc(t(locale, 'brand.footer'))}</footer>"


def _severity(severity: int, locale: str) -> str:
    level = min(max(severity, 1), 3)
    return f"{'●' * level}{'○' * (3 - level)} {t(locale, f'severity.{SEVERITIES[level]}')}"


def _dots(severity: int) -> str:
    level = min(max(severity, 1), 3)
    return f"{'●' * level}{'○' * (3 - level)}"


def _badge(card: CardView, locale: str) -> str:
    return f" · {_esc(t(locale, 'badge.fallback'))}" if card.fallback else ""


def _art(slug: str) -> str:
    """One picture above the heading: the section art; an unknown section
    borrows the hero. The menu is text-only and calls no art."""
    return f'<img src="{ART}/{slug if slug in ARTS else "hero"}.png"/>'


def _art_of_threat(slug: str) -> str:
    """A threat slug starts with its category (`fire-home` -> `fire`)."""
    return slug.split("-", 1)[0]


def _header(slug: str, heading: str, lead: str = "") -> str:
    """Art, heading, one lead block, rule: the shape every screen shares."""
    return f"{_art(slug)}<h1>{heading}</h1>{lead}<hr/>"


def render_menu(categories: list[CategoryView], locale: str, is_admin: bool = False) -> RichScreen:
    rows = [
        rich_row(
            rich_button(
                item.title,
                data=cb_cat(item.slug),
                icon=(item.slug, _esc(item.emoji)),
            )
        )
        for item in categories[:8]
    ]
    rows.append(
        rich_row(
            rich_button(
                t(locale, "button.chat"),
                data=cb_chat(),
                style="primary",
                icon=("support", "💬"),
            )
        )
    )
    rows.append(
        rich_row(
            rich_button(
                t(locale, "button.search"),
                data=cb_search_hint(),
                icon=("search", "🔍"),
            ),
            rich_button(
                t(locale, "button.language"),
                data=cb_lang_menu(),
                icon=("lang", "🌐"),
            ),
        )
    )
    if is_admin:
        # One small neutral row, and only for an id in ADMIN_IDS. The public
        # command list stays the same: the panel is reached from here.
        rows.append(
            rich_row(
                rich_button(
                    t(locale, "button.admin"),
                    data=cb_admin(),
                    icon=("admin", "🛠"),
                )
            )
        )
    heading = f"{premium('shield', '🛡️')} {_esc(t(locale, 'menu.title'))}"
    html = f"<h1>{heading}</h1><p>{_esc(t(locale, 'menu.lead'))}</p><hr/>"
    return RichScreen(html + "\n".join(rows))


def render_chat(locale: str) -> RichScreen:
    """The chat screen: one lead line and the two ways out."""
    rows = [
        rich_row(
            rich_button(
                t(locale, "button.chat_exit"),
                data=cb_chat_exit(),
                icon=("cancel", "❌"),
            ),
            rich_button(
                t(locale, "button.menu"),
                data=cb_menu(),
                icon=("menu", "🏘"),
            ),
        )
    ]
    heading = f"{premium('support', '💬')} {_esc(t(locale, 'chat.title'))}"
    html = _header("chat", heading, f"<p>{_esc(t(locale, 'chat.lead'))}</p>")
    return RichScreen(html + "\n".join(rows))


def render_category(
    category: CategoryView, threats: list[ThreatView], locale: str, page: int, pages: int
) -> RichScreen:
    rows = [
        rich_row(rich_button(f"{_dots(item.severity)} {item.title}", data=cb_card(item.slug, page)))
        for item in threats[:PAGE_SIZE]
    ]
    nav = []
    if page > 1:
        nav.append(rich_button(t(locale, "button.prev"), data=cb_cat(category.slug, page - 1)))
    if page < pages:
        nav.append(rich_button(t(locale, "button.next"), data=cb_cat(category.slug, page + 1)))
    if nav:
        rows.append(rich_row(*nav))
    rows.append(
        rich_row(
            rich_button(
                t(locale, "button.menu"),
                data=cb_menu(),
                icon=("menu", "🏘"),
            )
        )
    )
    lead = t(locale, "category.lead")
    meta = (
        f"{premium('stats', '🔢')} {_esc(t(locale, 'category.count', n=category.threat_count))}"
        f" · {premium('catalog', '📂')} {_esc(t(locale, 'category.page', page=page, pages=pages))}"
    )
    heading = f"{premium(category.slug, _esc(category.emoji))} {_esc(category.title)}"
    html = _header(category.slug, heading, f"<p>{_esc(lead)}</p>") + f"<footer>{meta}</footer>"
    return RichScreen(html + "\n".join(rows))


def _card_html(card: CardView, locale: str, shown: list[int], summary: str | None = None) -> str:
    """The card markup, with the first `shown[n]` items of each list."""
    signs, before, during, after, forbidden = (
        _items(group, count)
        for group, count in zip(
            (card.signs, card.before, card.during, card.after, card.forbidden),
            shown[:5],
            strict=True,
        )
    )
    contacts = [_esc(item) for item in card.contacts[: shown[5]]]
    if len(card.contacts) > shown[5]:
        contacts.append("…")
    text = card.summary if summary is None else summary
    brand = t(locale, "brand.footer")
    return (
        _header(
            card.category_slug,
            f"{premium(card.category_slug, _esc(card.icon))} {_esc(card.title)}",
            f"<p>{_severity(card.severity, locale)} · {_esc(card.category_title)}{_badge(card, locale)}</p>",
        )
        + (f"<p>{_esc(text)}</p><br/>" if text else "")
        + (
            f"<blockquote>{premium('eyes', '👀')} {_esc(t(locale, 'card.signs'))}<br/>{signs}</blockquote>"
            if signs
            else ""
        )
        + _details("card.before", before, locale, icon=("hourglass", "⏳"))
        + _details("card.during", during, locale, opened=True, icon=("fire", "🔥"))
        + _details("card.after", after, locale, icon=("refresh", "🔄"))
        + _details("card.forbidden", forbidden, locale, icon=("cancel", "❌"))
        + "<footer>"
        + " · ".join(
            [
                f"{premium('duck', '🦆')} {_esc(brand)}",
                *contacts,
                *([_esc(card.source)] if card.source else []),
            ]
        )
        + "</footer>"
    )


def render_card(
    card: CardView, locale: str, page: int, has_quiz: bool, can_game: bool = False
) -> RichScreen:
    """Art, heading, severity, signs, three steps, bans, brand footer.

    Every field is shown whole. The only trim is the ceiling guard: whole list
    items fall off the tail until the markup fits, and then «…» marks the cut.

    ``can_game`` is off by default: Telegram closed HTML widgets for general
    bots, so the card carries one primary «Тест» unless WIDGETS_ENABLED is set.
    """
    rows = []
    if has_quiz:
        quiz_buttons = [
            rich_button(
                t(locale, "button.quiz"),
                data=cb_quiz_next(card.slug, "start", secrets.token_hex(2)),
                style="primary",
                icon=("test", "✅"),
            )
        ]
        if can_game:
            quiz_buttons.append(
                rich_button(
                    t(locale, "button.game"),
                    data=cb_game(card.slug),
                    style="success",
                    icon=("game", "🎮"),
                )
            )
        rows.append(rich_row(*quiz_buttons))
    rows.append(
        rich_row(
            rich_button(
                t(locale, "button.back"),
                data=cb_cat(card.category_slug, page),
                icon=("back", "🏠"),
            ),
            rich_button(
                t(locale, "button.share"),
                query=card.title,
                icon=("send", "📤"),
            ),
        )
    )

    groups = (card.signs, card.before, card.during, card.after, card.forbidden, card.contacts)
    shown = [len(group) for group in groups]
    # Least critical first: bans and later steps go before signs, the open
    # step, and the phones.
    drop_order = (4, 3, 1, 0, 2, 5)

    def screen(summary: str | None = None) -> str:
        return _card_html(card, locale, shown, summary) + "\n" + "\n".join(rows)

    html = screen()
    while _utf16(html) > CARD_CAP:
        for index in drop_order:
            if shown[index]:
                shown[index] -= 1
                break
        else:
            # Unreachable for seeded content (a summary is bounded far below
            # the cap); the footer and every heading still reach the reader.
            html = screen("")
            break
        html = screen()
    return RichScreen(html)


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
                    f"{number + 1}. {option}",
                    data=cb_quiz(slug, quiz.question_id, number, seed),
                    style=style,
                )
            )
        )
    if chosen is not None:
        tail = (
            f"<blockquote>{premium('description', '📖')} {_esc(t(locale, 'quiz.explanation'))}<br/>"
            f"{_esc(quiz.explanation)}</blockquote>"
        )
        rows.append(
            rich_row(
                rich_button(
                    t(locale, "button.next_question"),
                    data=cb_quiz_next(slug, quiz.question_id, seed),
                    style="primary",
                    icon=("arrow", "➡️"),
                )
            )
        )
    else:
        tail = ""
    html = (
        _header(_art_of_threat(slug), f"{premium('test', '📝')} {_esc(t(locale, 'quiz.heading'))}")
        + f"<h2>{premium('hourglass', '⏳')} {_esc(t(locale, 'quiz.title', index=index, total=total))}</h2>"
        + f"<p>{_esc(quiz.prompt)}</p>"
        + tail
    )
    return RichScreen(html + "\n".join(rows))


def render_result(correct: int, total: int, slug: str, locale: str) -> RichScreen:
    verdict = "perfect" if total and correct == total else ("partial" if correct else "zero")
    passed = bool(total) and correct * 3 >= total * 2
    rows = [
        rich_row(
            rich_button(
                t(locale, "button.retry"),
                data=cb_quiz_next(slug, "start", secrets.token_hex(2)),
                style="success" if passed else "primary",
                icon=("refresh", "🔄"),
            )
        ),
        rich_row(
            rich_button(
                t(locale, "button.menu"),
                data=cb_menu(),
                icon=("menu", "🏘"),
            )
        ),
    ]
    html = (
        _header(
            _art_of_threat(slug),
            f"{premium('test', '✅')} {_esc(t(locale, 'quiz.result.title'))}",
        )
        + f"<h2>{premium('stats', '🔢')} {_esc(t(locale, 'quiz.score', correct=correct, total=total))}</h2>"
        + f"<p>{_esc(t(locale, f'quiz.result.{verdict}'))}</p>"
        + _brand(locale)
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
                rich_button(f"{_dots(item.severity)} {item.title}", data=cb_card(item.slug))
            )
            for item in results[:RESULTS]
        ]
    else:
        # No match: the eight sections are the useful next tap, in the message.
        rows = [
            rich_row(
                rich_button(
                    item.title,
                    data=cb_cat(item.slug),
                    icon=(item.slug, _esc(item.emoji)),
                )
            )
            for item in (hints or [])[:8]
        ]
    rows.append(
        rich_row(
            rich_button(
                t(locale, "button.menu"),
                data=cb_menu(),
                icon=("menu", "🏘"),
            )
        )
    )
    clean = " ".join(query.split())
    if results:
        body = f"<p>{_esc(t(locale, 'search.found', n=len(results)))}</p>"
    else:
        body = f"<p>{_esc(t(locale, 'search.empty'))}</p><p>{_esc(t(locale, 'search.hint'))}</p>"
    quote = f"<p><code>{_esc(clean)}</code></p>" if clean else ""
    html = _header("hero", f"{premium('search', '🔍')} {_esc(t(locale, 'search.title'))}")
    html += quote + body
    return RichScreen(html + "\n".join(rows))
