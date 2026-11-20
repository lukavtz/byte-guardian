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

import hashlib
import math
import re
import secrets
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from html import escape
from pathlib import Path
from urllib.parse import quote, urlencode

from aiogram.types import InlineKeyboardMarkup

from app.content.types import CardView, CategoryView, QuizView, ThreatView
from app.services.callbacks import (
    cb_admin,
    cb_card,
    cb_cat,
    cb_chat,
    cb_chat_exit,
    cb_game,
    cb_lang,
    cb_quiz,
    cb_quiz_next,
    cb_random,
    cb_search,
    cb_search_hint,
    cb_sections,
    cb_settings,
)
from app.services.i18n import ENABLED_LOCALES, t
from app.ui.emoji import premium
from app.ui.keyboards import PAGE_SIZE, Style, menu_button, rich_button, rich_row

RESULTS = 7
CARD_CAP = 30000
SHARE = "https://t.me/share/url?"
BAR_CELLS = 10
SEVERITIES = {1: "low", 2: "medium", 3: "high"}
ART = "https://raw.githubusercontent.com/lukavtz/byte-guardian/main/assets/art"
# The menu opens with the same banner the README shows, straight from assets/.
BANNER = "https://raw.githubusercontent.com/lukavtz/byte-guardian/main/assets/brand/banner.jpg"
EXT = "jpg"


# Telegram caches <img> by URL, and raw.githubusercontent sends max-age=300.
# Hash the deployed art bytes so any art change makes every image a fresh URL.
def _art_version(root: Path) -> str:
    art = root / "assets" / "art"
    files = sorted(art.glob("*.jpg")) + sorted((root / "assets" / "brand").glob("banner.jpg"))
    digest = hashlib.sha256()
    for path in files:
        digest.update(path.name.encode())
        digest.update(path.read_bytes())
    return digest.hexdigest()[:12] if files else "3a6d3c7"


ART_VERSION = _art_version(Path(__file__).resolve().parents[2])
ARTS = frozenset({"fire", "terror", "tech", "nature", "medical", "chem", "rad", "cyber", "chat", "lang"})


@dataclass(frozen=True, slots=True)
class RichScreen:
    """A rich message; `keyboard` is only for screens with no rich form."""

    html: str
    keyboard: InlineKeyboardMarkup | None = None


def _esc(text: object) -> str:
    return escape(str(text), quote=False)


LINK = re.compile(r"https?://\S+")


def _no_links(value: object) -> str:
    """The text without URLs: a footer names organizations, never links."""
    text = LINK.sub("", str(value))
    text = re.sub(r"\(\s*\)", "", text)
    return re.sub(r"\s{2,}", " ", text).strip(" ·,;—()-")


def _utf16(text: str) -> int:
    return len(text.encode("utf-16-le")) // 2


def _items(items: tuple[str, ...], shown: int) -> str:
    """One item per line; a dropped tail is marked by the caller."""
    return "<br/>".join(f"• {_esc(item)}" for item in items[:shown])


def _details(key: str, body: str, locale: str, opened: bool = False, icon: tuple[str, str] | None = None) -> str:
    if not body:
        return ""
    flag = " open" if opened else ""
    lead = f"{premium(*icon)} " if icon else ""
    return f"<details{flag}><summary><b>{lead}{_esc(t(locale, key))}</b></summary>{body}</details>"


def _section(key: str, body: str, locale: str, icon: tuple[str, str]) -> str:
    if not body:
        return ""
    return f"<p><b>{premium(*icon)} {_esc(t(locale, key))}</b><br/>{body}</p>"


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


def _img(url: str) -> str:
    """An <img> with a cache-busting marker: new art commits get new URLs."""
    return f'<img src="{url}?v={ART_VERSION}"/>'


def _art(slug: str) -> str:
    """One picture above the heading: the section art; an unknown section
    borrows the hero. The menu has a banner of its own and does not call this."""
    return _img(f"{ART}/{slug if slug in ARTS else 'hero'}.{EXT}")


def _art_of_threat(slug: str) -> str:
    """A threat slug starts with its category (`fire-home` -> `fire`)."""
    return slug.split("-", 1)[0]


def _header(slug: str, heading: str, lead: str = "") -> str:
    """Art, heading, one lead block, rule: the shape every screen shares."""
    return f"{_art(slug)}<h1>{heading}</h1>{lead}<hr/>"


def _card_header(heading: str, lead: str) -> str:
    """Cards open with text: no oversized art before the first answer."""
    return f"<h1>{heading}</h1>{lead}"


def render_menu(categories: list[CategoryView], locale: str, is_admin: bool = False) -> RichScreen:
    """Banner, heading, one lead line, four rows (five with admin).

    The eight sections live one tap deeper: the menu stays a doorway, not a
    catalog. There is no menu button here, because this is the menu.
    """
    rows = [
        rich_row(
            rich_button(
                t(locale, "button.chat"),
                data=cb_chat(),
                style="primary",
                icon=("support", "💬"),
            )
        ),
        rich_row(
            rich_button(
                t(locale, "button.sections"),
                data=cb_sections(),
                icon=("catalog", "📂"),
            )
        ),
        rich_row(
            rich_button(
                t(locale, "button.search"),
                data=cb_search_hint(),
                icon=("search", "🔍"),
            ),
            rich_button(
                t(locale, "button.random"),
                data=cb_random(),
                icon=("refresh", "🎲"),
            ),
        ),
        rich_row(
            rich_button(
                t(locale, "button.settings"),
                data=cb_settings(),
                icon=("gear", "⚙"),
            )
        ),
    ]
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
    html = f'{_img(BANNER)}<h1>{heading}</h1><p>{_esc(t(locale, "menu.lead"))}</p><hr/>'
    return RichScreen(html + "\n".join(rows))


def render_sections(categories: list[CategoryView], locale: str) -> RichScreen:
    """The eight sections, one per row, then one full-width way home."""
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
    rows.append(rich_row(menu_button(locale)))
    heading = f"{premium('catalog', '📂')} {_esc(t(locale, 'button.sections'))}"
    html = _header("hero", heading, f"<p>{_esc(t(locale, 'sections.lead'))}</p>")
    return RichScreen(html + "\n".join(rows))


def render_language(locale: str, names: Mapping[str, str]) -> RichScreen:
    """Language switch: its own banner, then four options and the menu button."""
    languages = [
        rich_button(
            names[code],
            data=cb_lang(code),
            style="success" if code == locale else None,
            icon=("lang", "🌐"),
        )
        for code in ENABLED_LOCALES
    ]
    rows = [
        rich_row(*languages[index : index + 2])
        for index in range(0, len(languages), 2)
    ]
    rows.append(rich_row(menu_button(locale)))
    html = _header(
        "lang",
        f"{premium('lang', '🌐')} {_esc(t(locale, 'settings.language'))}",
        f"<p>{_esc(t(locale, 'language.hint'))}</p>",
    )
    return RichScreen(html + "\n".join(rows))


def render_chat(locale: str) -> RichScreen:
    """One lead line and one danger exit — no duplicate menu row."""
    rows = [
        rich_row(
            rich_button(
                t(locale, "button.chat_exit"),
                data=cb_chat_exit(),
                style="danger",
                icon=("cancel", "❌"),
            )
        )
    ]
    heading = f"{premium('support', '💬')} {_esc(t(locale, 'chat.title'))}"
    html = _header("chat", heading, f"<p>{_esc(t(locale, 'chat.lead'))}</p>")
    return RichScreen(html + "\n".join(rows))


def render_not_found(locale: str) -> RichScreen:
    """A stale card or section: say so, and keep both ways forward."""
    rows = "\n".join(
        (
            rich_row(
                rich_button(
                    t(locale, "button.search"), data=cb_search_hint(), icon=("search", "🔍")
                )
            ),
            rich_row(menu_button(locale)),
        )
    )
    heading = f"{premium('error', '❗')} {_esc(t(locale, 'card.not_found.title'))}"
    html = _header("hero", heading, f"<p>{_esc(t(locale, 'card.not_found.lead'))}</p>")
    return RichScreen(html + rows)


def _pager(locale: str, page: int, pages: int, link: Callable[[int], str]) -> str:
    """`‹ 2/5 ›`: the arrows are coloured by direction, the place stays neutral.

    A missing arrow is left out entirely, so page one shows only `›` and the
    last page only `‹`. The place is a button too, because a rich row has no
    plain text cell; tapping it redraws the same page and changes nothing.
    """
    buttons = []
    if page > 1:
        buttons.append(rich_button(t(locale, "button.prev"), data=link(page - 1), style="danger"))
    buttons.append(rich_button(f"{page}/{pages}", data=link(page)))
    if page < pages:
        buttons.append(rich_button(t(locale, "button.next"), data=link(page + 1), style="success"))
    return rich_row(*buttons)


def render_category(
    category: CategoryView, threats: list[ThreatView], locale: str, page: int, pages: int
) -> RichScreen:
    rows = [
        rich_row(rich_button(f"{_dots(item.severity)} {item.title}", data=cb_card(item.slug, page)))
        for item in threats[:PAGE_SIZE]
    ]
    if pages > 1:
        rows.append(_pager(locale, page, pages, lambda number: cb_cat(category.slug, number)))
    rows.append(rich_row(menu_button(locale)))
    lead = t(locale, "category.lead")
    meta = (
        f"{premium('stats', '🔢')} {_esc(t(locale, 'category.count', n=category.threat_count))}"
        f" · {premium('catalog', '📂')} {_esc(t(locale, 'category.page', page=page, pages=pages))}"
    )
    heading = f"{premium(category.slug, _esc(category.emoji))} {_esc(category.title)}"
    html = _header(category.slug, heading, f"<p>{_esc(lead)}</p>") + f"<footer>{meta}</footer>"
    return RichScreen(html + "\n".join(rows))


def _card_html(card: CardView, locale: str, shown: list[int], summary: str | None = None) -> str:
    """Card markup: critical help stays open, later steps collapse."""
    groups = (card.signs, card.before, card.during, card.after, card.forbidden)
    bodies = [_items(group, count) for group, count in zip(groups, shown[:5], strict=True)]
    signs, before, during, after, forbidden = bodies
    truncated = any(len(group) > count for group, count in zip(groups, shown[:5], strict=True))
    contacts = [_no_links(item) for item in card.contacts[: shown[5]]]
    contacts = [item for item in contacts if item]
    if len(card.contacts) > shown[5]:
        contacts.append("…")
        truncated = True
    source = _no_links(card.source) if card.source else ""
    text = card.summary if summary is None else summary
    brand = t(locale, "brand.footer")
    quick = card.quick or (card.contacts[0] if card.contacts else "")
    numbers = " · ".join(item for item in contacts if item and item != "…")
    emergency = f"<p><b>{premium('warning', '⚠️')} {_esc(numbers)}</b></p>" if numbers else ""
    return (
        _card_header(
            f"{premium(card.category_slug, _esc(card.icon))} {_esc(card.title)}",
            f"<p>{_severity(card.severity, locale)} · {_esc(card.category_title)}{_badge(card, locale)}</p>",
        )
        + emergency
        + (f"<p>{_esc(text)}</p>" if text else "")
        + _section("card.quick", _esc(quick), locale, ("warning", "⚠️"))
        + _section("card.signs", signs, locale, ("eyes", "👀"))
        + _details("card.during", during, locale, opened=True, icon=("fire", "🔥"))
        + _details("card.details_more", before + after + forbidden, locale, icon=("catalog", "📂"))
        + (f"<p>… {_esc(t(locale, 'card.truncated'))}</p>" if truncated else "")
        + "<footer>"
        + " · ".join(
            [
                f"{premium('duck', '🦆')} {_esc(brand)}",
                *contacts,
                *([_esc(source)] if source else []),
            ]
        )
        + "</footer>"
    )


SHARE_LIMIT = 180


def _short(value: str, limit: int = SHARE_LIMIT) -> str:
    """One line, cut at a word boundary; «…» only when something was cut."""
    text = " ".join(value.split())
    if len(text) <= limit:
        return text
    cut = text[:limit].rsplit(" ", 1)[0].rstrip(" ,;:—") or text[:limit]
    return cut + "…"


def share_text(card: CardView) -> str:
    """Title, the first thing to do, brand: a message a person can forward."""
    action = _short(card.during[0] if card.during else card.summary)
    return "\n".join((card.title, action, t(card.locale, "brand.footer")))


def share_url(username: str | None, card: CardView) -> str:
    """`t.me/share/url` with the deep link and a human message.

    ``quote_via=quote`` keeps a space as ``%20``; the default writes ``+``,
    which Telegram shows literally in the share sheet. Without a resolved
    username the card is still shareable, only the deep link is dropped rather
    than pointing at the wrong bot.
    """
    params = {"text": share_text(card)}
    if username:
        params = {"url": f"https://t.me/{username}?start=card_{card.slug}", **params}
    return SHARE + urlencode(params, quote_via=quote)


def render_card(
    card: CardView,
    locale: str,
    page: int,
    has_quiz: bool,
    can_game: bool = False,
    username: str | None = None,
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
                t(locale, "button.share"),
                url=share_url(username, card),
                icon=("send", "📤"),
            )
        )
    )
    rows.append(
        rich_row(
            rich_button(
                t(locale, "button.back"),
                data=cb_cat(card.category_slug, page),
                style="danger",
                icon=("back", "🏠"),
            ),
            menu_button(locale),
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
            # A pathological summary can still overflow after lists are gone.
            cap = CARD_CAP - _utf16("\n" + "\n".join(rows)) - 200
            html = _card_html(card, locale, shown, card.summary[: max(cap, 0)]) + "\n" + "\n".join(rows)
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
    rows.append(rich_row(menu_button(locale)))
    html = (
        _header(_art_of_threat(slug), f"{premium('test', '📝')} {_esc(t(locale, 'quiz.heading'))}")
        + f"<h2>{premium('hourglass', '⏳')} {_esc(t(locale, 'quiz.title', index=index, total=total))}</h2>"
        + f"<p>{_esc(quiz.prompt)}</p>"
        + tail
    )
    return RichScreen(html + "\n".join(rows))


def _bar(correct: int, total: int) -> str:
    """Ten cells for the session: the score at a glance, no extra text."""
    filled = 0 if not total else min(BAR_CELLS, max(0, round(correct / total * BAR_CELLS)))
    return "▰" * filled + "▱" * (BAR_CELLS - filled)


def render_result(
    correct: int,
    total: int,
    slug: str,
    locale: str,
    streak: int | None = None,
    best: int | None = None,
) -> RichScreen:
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
        rich_row(menu_button(locale)),
    ]
    html = (
        _header(
            _art_of_threat(slug),
            f"{premium('test', '✅')} {_esc(t(locale, 'quiz.result.title'))}",
        )
        + f"<h2>{premium('stats', '🔢')} {_esc(t(locale, 'quiz.score', correct=correct, total=total))}</h2>"
        + f"<p>{_esc(t(locale, f'quiz.result.{verdict}'))}</p>"
        + f"<p><code>{_bar(correct, total)}</code></p>"
        + (
            f"<p>{_esc(t(locale, 'quiz.streak', streak=streak, best=best))}</p>"
            if streak is not None and best is not None
            else ""
        )
        + _brand(locale)
    )
    return RichScreen(html + "\n".join(rows))


def search_screen(
    query: str,
    results: list[ThreatView],
    locale: str,
    hints: list[CategoryView] | None = None,
    *,
    page: int = 1,
    total: int | None = None,
    digest: str | None = None,
) -> RichScreen:
    """One page of hits; ``total`` is every hit, so the count covers the tail.

    ``digest`` is the key the query is stored under, so «Показать ещё» carries
    8 hex characters instead of text that could exceed Telegram's 64-byte
    callback ceiling.
    """
    found = len(results) if total is None else max(total, len(results))
    pages = max(1, math.ceil(found / RESULTS))
    page = min(max(page, 1), pages)
    visible = results[(page - 1) * RESULTS : page * RESULTS]
    if visible:
        rows = [
            rich_row(
                rich_button(f"{_dots(item.severity)} {item.title}", data=cb_card(item.slug))
            )
            for item in visible
        ]
        if digest and pages > 1:
            rows.append(
                _pager(locale, page, pages, lambda number: cb_search(digest, number))
            )
    else:
        # No match: the eight sections are the useful next tap, in the message,
        # plus the one tap that never depends on knowing what to type.
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
                    t(locale, "button.random"),
                    data=cb_random(),
                    icon=("refresh", "🎲"),
                )
            )
        )
    rows.append(rich_row(menu_button(locale)))
    clean = " ".join(query.split())
    if visible:
        body = f"<p>{_esc(t(locale, 'search.found', n=found))}</p>"
    else:
        body = f"<p>{_esc(t(locale, 'search.empty'))}</p><p>{_esc(t(locale, 'search.hint'))}</p>"
    quote = f"<p><code>{_esc(clean)}</code></p>" if clean else ""
    html = _header("hero", f"{premium('search', '🔍')} {_esc(t(locale, 'search.title'))}")
    html += quote + body
    return RichScreen(html + "\n".join(rows))
