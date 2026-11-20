"""Navigation, deep links, language and settings — offline fakes only."""

from __future__ import annotations

import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.content.types import CardView, CategoryView, ThreatView
from app.handlers import commands, nav
from app.services.callbacks import (
    Callback,
    cb_card,
    cb_cat,
    cb_chat,
    cb_chat_exit,
    cb_lang_menu,
    cb_menu,
    cb_search_hint,
    parse_cb,
)
from app.services.settings import chat_mode_on, set_chat_mode
from app.ui.keyboards import keyboard_from_rich


class _Result:
    def __init__(self, rows: list) -> None:
        self.rows = rows

    def first(self):
        return self.rows[0] if self.rows else None


class FakeDB:
    def __init__(
        self,
        opt_in: dict[int, bool] | None = None,
        locales: dict[int, str] | None = None,
    ) -> None:
        self.settings: dict[str, object] = {}
        self.users = dict(opt_in or {})
        self.locales = dict(locales or {})

    def factory(self) -> FakeSessionFactory:
        return FakeSessionFactory(self)

    def execute(self, statement, params: dict) -> _Result:
        sql = " ".join(str(statement).split())
        if sql.startswith("INSERT INTO settings"):
            # asyncpg decodes jsonb, so the fake must too.
            self.settings[params["key"]] = json.loads(params["value"])
            return _Result([])
        if sql.startswith("SELECT value FROM settings"):
            key = params["key"]
            return _Result([(self.settings[key],)] if key in self.settings else [])
        if sql.startswith("DELETE FROM settings"):
            return _Result([])
        if sql.startswith("SELECT locale FROM users"):
            user_id = params["user_id"]
            return _Result([(self.locales[user_id],)] if user_id in self.locales else [])
        if sql.startswith("UPDATE users SET locale"):
            self.locales[params["user_id"]] = params["locale"]
            return _Result([])
        if sql.startswith("SELECT broadcasts_opt_in"):
            user_id = params["user_id"]
            return _Result([(self.users[user_id],)] if user_id in self.users else [])
        if sql.startswith("UPDATE users SET broadcasts_opt_in"):
            self.users[params["user_id"]] = params["opt_in"]
            return _Result([])
        raise AssertionError("unexpected SQL: " + sql)


class _Session:
    def __init__(self, db: FakeDB) -> None:
        self.db = db

    async def __aenter__(self) -> _Session:
        return self

    async def __aexit__(self, *exc: object) -> bool:
        return False

    def begin(self) -> _Session:
        return self

    async def execute(self, statement, params=None) -> _Result:
        return self.db.execute(statement, params or {})


class FakeSessionFactory:
    def __init__(self, db: FakeDB) -> None:
        self.db = db

    def __call__(self) -> _Session:
        return _Session(self.db)


class FakeRepo:
    def __init__(self) -> None:
        self.card_calls: list[tuple[str, str]] = []
        self.threat_calls: list[tuple[str, str]] = []
        self._threats = [
            ThreatView(
                slug=f"fire-{index}",
                category_slug="fire",
                category_title="Пожар",
                title=f"Угроза {index}",
                summary="Коротко.",
                severity=2,
                locale="ru",
                fallback=False,
            )
            for index in range(7)
        ]

    def categories(self) -> list[CategoryView]:
        return [
            CategoryView(slug="fire", title="Пожар", emoji="🔥", sort=1, threat_count=7),
        ]

    def threats(self, slug: str, locale: str) -> list[ThreatView]:
        self.threat_calls.append((slug, locale))
        return self._threats if slug == "fire" else []

    def card(self, slug: str, locale: str) -> CardView | None:
        self.card_calls.append((slug, locale))
        if slug != "fire-0":
            return None
        return CardView(
            slug=slug,
            category_slug="fire",
            category_title="Пожар",
            severity=3,
            locale="ru",
            fallback=False,
            title="Пожар в квартире",
            summary="Звоните 101.",
            signs=("Дым",),
            before=("Датчик",),
            during=("Выходите",),
            after=("Не входите",),
            forbidden=("Не открывайте окна",),
            contacts=("101", "112"),
            source="МЧС России",
            icon="🔥",
        )

    def has_quiz(self, slug: str, locale: str) -> bool:
        return True


class FakeBot:
    def __init__(self) -> None:
        self.sent: list[dict] = []
        self.edited: list[dict] = []

    async def me(self) -> SimpleNamespace:
        return SimpleNamespace(username="byte_bot")

    async def send_rich_message(self, **kwargs: object) -> SimpleNamespace:
        self.sent.append(kwargs)
        return SimpleNamespace(message_id=100 + len(self.sent))

    async def edit_message_text(self, **kwargs: object) -> SimpleNamespace:
        self.edited.append(kwargs)
        return SimpleNamespace(message_id=kwargs.get("message_id"))


def _message(*, language_code: str = "ru", text: str = "/start") -> SimpleNamespace:
    photo = SimpleNamespace(file_id="cached-file")
    return SimpleNamespace(
        chat=SimpleNamespace(id=7, type="private"),
        from_user=SimpleNamespace(id=7, language_code=language_code),
        message_id=10,
        text=text,
        answer=AsyncMock(),
        answer_photo=AsyncMock(return_value=SimpleNamespace(photo=[photo])),
    )


def _query(data: str, *, chat_type: str = "private") -> SimpleNamespace:
    return SimpleNamespace(
        data=data,
        from_user=SimpleNamespace(id=7, language_code="ru"),
        message=SimpleNamespace(
            chat=SimpleNamespace(id=7, type=chat_type), message_id=10
        ),
        answer=AsyncMock(),
    )


def _buttons(kwargs: dict) -> list[object]:
    """The screen's controls: in-message buttons first, then the keyboard."""
    rich = kwargs.get("rich_message")
    markup = keyboard_from_rich(rich.html or "") if rich is not None else None
    markup = markup or kwargs.get("reply_markup")
    return [item for row in (markup.inline_keyboard if markup else []) for item in row]


def test_parse_card_carries_slug_and_page() -> None:
    parsed = parse_cb("b1:card:fire-home:2")
    assert parsed is not None
    assert (parsed.action, parsed.arg, parsed.page) == ("card", "fire-home", 2)


@pytest.mark.parametrize("data", ["", "b1:", "b1:card", "b1:card::1", "b1:cat:fire:0", "x"])
def test_malformed_callback_is_none(data: str) -> None:
    assert parse_cb(data) is None


def test_worst_case_callbacks_fit_telegram() -> None:
    for data in (cb_menu(), cb_cat("a" * 32, 999), cb_card("a" * 32, 999)):
        assert len(data.encode("utf-8")) <= 64


@pytest.mark.asyncio
async def test_start_payload_opens_card_as_one_rich_message() -> None:
    db = FakeDB()
    factory = fake = db.factory()
    repo = FakeRepo()
    bot = FakeBot()
    message = _message()

    await commands.start(
        message, SimpleNamespace(args="card_fire-0"), bot, repo, factory
    )

    assert repo.card_calls[0] == ("fire-0", "ru")
    assert len(bot.sent) == 1
    assert "Пожар в квартире" in bot.sent[0]["rich_message"].html
    message.answer_photo.assert_not_awaited()
    assert fake is factory


@pytest.mark.asyncio
async def test_start_without_payload_sends_only_the_menu() -> None:
    db = FakeDB()
    repo = FakeRepo()
    bot = FakeBot()
    message = _message()

    await commands.start(message, SimpleNamespace(args=None), bot, repo, db.factory())

    assert len(bot.sent) == 1
    assert any(item.text == "🔥 Пожар" for item in _buttons(bot.sent[0]))
    message.answer_photo.assert_not_awaited()


@pytest.mark.asyncio
async def test_start_saves_telegram_language_once() -> None:
    db = FakeDB()
    message = _message(language_code="tt-TR")

    await commands.start(message, SimpleNamespace(args=None), FakeBot(), FakeRepo(), db.factory())

    assert db.locales[7] == "tt"


@pytest.mark.asyncio
async def test_menu_callback_edits_in_private_chat() -> None:
    bot = FakeBot()
    query = _query(cb_menu())

    await nav.on_menu(query, bot, FakeRepo(), FakeDB().factory())

    query.answer.assert_awaited_once()
    assert bot.edited and not bot.sent
    assert bot.edited[0]["message_id"] == 10
    assert any(item.text == "🔥 Пожар" for item in _buttons(bot.edited[0]))


@pytest.mark.asyncio
async def test_admin_row_is_hidden_without_admin_ids() -> None:
    bot = FakeBot()
    query = _query(cb_menu())

    await nav.on_menu(query, bot, FakeRepo(), FakeDB().factory())

    assert not any(item.callback_data == "b1:admin" for item in _buttons(bot.edited[0]))


@pytest.mark.asyncio
async def test_admin_row_is_visible_to_a_listed_id() -> None:
    bot = FakeBot()
    query = _query(cb_menu())
    settings = SimpleNamespace(admin_id_set=frozenset({7}))

    await nav.on_menu(query, bot, FakeRepo(), FakeDB().factory(), settings)

    labels = [item.text for item in _buttons(bot.edited[0])]
    assert any(label.endswith("Админ-панель") for label in labels)
    assert any(item.callback_data == "b1:admin" for item in _buttons(bot.edited[0]))


@pytest.mark.asyncio
async def test_admin_row_is_hidden_from_an_unlisted_id() -> None:
    bot = FakeBot()
    query = _query(cb_menu())
    settings = SimpleNamespace(admin_id_set=frozenset({99}))

    await nav.on_menu(query, bot, FakeRepo(), FakeDB().factory(), settings)

    assert not any(item.callback_data == "b1:admin" for item in _buttons(bot.edited[0]))


@pytest.mark.asyncio
async def test_group_callback_is_answered_but_not_edited() -> None:
    bot = FakeBot()
    query = _query(cb_menu(), chat_type="group")

    await nav.on_menu(query, bot, FakeRepo(), FakeDB().factory())

    query.answer.assert_awaited_once()
    assert not bot.edited
    assert not bot.sent


@pytest.mark.asyncio
async def test_category_page_carries_page_into_card_and_back() -> None:
    bot = FakeBot()
    query = _query(cb_cat("fire", 2))

    await nav.on_category(query, bot, FakeRepo(), FakeDB().factory())

    assert bot.edited and not bot.sent
    assert bot.edited[0]["message_id"] == 10
    html = bot.edited[0]["rich_message"].html
    assert "Страница 2/2" in html
    datas = [item.callback_data for item in _buttons(bot.edited[0])]
    assert "b1:card:fire-5:2" in datas
    assert "b1:cat:fire:1" in datas


@pytest.mark.asyncio
async def test_card_back_button_keeps_page() -> None:
    bot = FakeBot()
    query = _query(cb_card("fire-0", 2))

    await nav.on_card(query, bot, FakeRepo(), FakeDB().factory())

    assert bot.edited and not bot.sent
    assert bot.edited[0]["message_id"] == 10
    buttons = _buttons(bot.edited[0])
    datas = [item.callback_data for item in buttons if item.callback_data]
    assert "b1:cat:fire:2" in datas
    assert any(item.switch_inline_query_current_chat for item in buttons)


@pytest.mark.asyncio
async def test_card_shows_the_game_only_with_widgets_enabled() -> None:
    off_bot = FakeBot()
    await nav.on_card(_query(cb_card("fire-0", 1)), off_bot, FakeRepo(), FakeDB().factory())
    off_datas = [item.callback_data for item in _buttons(off_bot.edited[0]) if item.callback_data]
    assert "b1:game:fire-0" not in off_datas
    assert any(data.startswith("b1:quiz_next:") for data in off_datas)

    on_bot = FakeBot()
    await nav.on_card(
        _query(cb_card("fire-0", 1)),
        on_bot,
        FakeRepo(),
        FakeDB().factory(),
        SimpleNamespace(widgets_enabled=True),
    )
    on_datas = [item.callback_data for item in _buttons(on_bot.edited[0]) if item.callback_data]
    assert "b1:game:fire-0" in on_datas


@pytest.mark.asyncio
async def test_language_callback_saves_and_rerenders_language_screen() -> None:
    db = FakeDB({7: False})
    bot = FakeBot()
    query = _query("b1:lang:en")

    await nav.on_lang(query, bot, db.factory())

    assert db.locales[7] == "en"
    assert bot.edited and not bot.sent
    assert bot.edited[0]["message_id"] == 10
    html = bot.edited[0]["rich_message"].html
    assert "Language" in html
    assert any(item.text == "English" for item in _buttons(bot.edited[0]))


@pytest.mark.asyncio
async def test_unknown_language_is_ignored() -> None:
    db = FakeDB()
    bot = FakeBot()

    await nav.on_lang(_query("b1:lang:de"), bot, db.factory())

    assert 7 not in db.locales
    assert not bot.edited


@pytest.mark.asyncio
async def test_settings_command_shows_language_and_broadcast_status() -> None:
    bot = FakeBot()
    message = _message()

    await commands.settings_command(message, bot, FakeDB({7: False}).factory())

    html = bot.sent[0]["rich_message"].html
    assert "Настройки" in html
    assert "Выключена" in html


@pytest.mark.asyncio
async def test_broadcast_toggle_updates_flag_and_redraws() -> None:
    db = FakeDB({7: True})
    bot = FakeBot()
    query = _query(nav.OPT_IN_OFF)

    await commands.toggle_broadcasts(query, bot, db.factory())

    query.answer.assert_awaited_once()
    assert db.users[7] is False
    assert bot.edited and not bot.sent
    assert bot.edited[0]["message_id"] == 10
    assert "Выключена" in bot.edited[0]["rich_message"].html


@pytest.mark.asyncio
async def test_menu_has_search_and_language_row() -> None:
    bot = FakeBot()
    query = _query(cb_menu())

    await nav.on_menu(query, bot, FakeRepo(), FakeDB().factory())

    buttons = _buttons(bot.edited[0])
    datas = [item.callback_data for item in buttons if item.callback_data]
    assert cb_search_hint() in datas
    assert cb_lang_menu() in datas
    assert len(buttons) <= 10


@pytest.mark.asyncio
async def test_language_menu_opens_four_languages() -> None:
    bot = FakeBot()
    query = _query(cb_lang_menu())

    await nav.on_lang_menu(query, bot, FakeDB().factory())

    query.answer.assert_awaited_once()
    assert bot.edited and not bot.sent
    assert bot.edited[0]["message_id"] == 10
    labels = [item.text for item in _buttons(bot.edited[0])]
    assert {"Русский", "English", "中文", "Татарча"} <= set(labels)


@pytest.mark.asyncio
async def test_search_hint_is_private_only() -> None:
    bot = FakeBot()
    query = _query(cb_search_hint(), chat_type="group")

    await nav.on_search_hint(query, bot, FakeDB().factory())

    query.answer.assert_awaited_once()
    assert not bot.sent
    assert not bot.edited


@pytest.mark.asyncio
async def test_search_hint_edits_the_menu_in_place() -> None:
    bot = FakeBot()
    query = _query(cb_search_hint())

    await nav.on_search_hint(query, bot, FakeDB().factory())

    query.answer.assert_awaited_once()
    assert not bot.sent
    assert _edited_message_id(bot.edited) == 10
    assert any(item.callback_data == cb_menu() for item in _buttons(bot.edited[0]))


def _edited_message_id(edited: list[dict]) -> int:
    return edited[0]["message_id"]


@pytest.mark.asyncio
async def test_group_start_replies_with_private_link_only() -> None:
    db = FakeDB()
    bot = FakeBot()
    message = _message()
    message.chat.type = "group"

    await commands.start(
        message, SimpleNamespace(args=None), bot, FakeRepo(), db.factory()
    )

    assert not bot.sent
    assert message.answer_photo.await_count == 0
    text = message.answer.await_args.args[0]
    assert "https://t.me/byte_bot" in text
    assert "tt" not in db.locales and "ru" not in db.locales


@pytest.mark.asyncio
async def test_group_settings_is_not_posted() -> None:
    bot = FakeBot()
    message = _message()
    message.chat.type = "supergroup"

    await commands.settings_command(message, bot, FakeDB().factory())

    assert not bot.sent
    assert "https://t.me/byte_bot" in message.answer.await_args.args[0]


def _screen_buttons(screen) -> list[object]:
    markup = keyboard_from_rich(screen.html) or screen.keyboard
    return [item for row in markup.inline_keyboard for item in row]


def test_language_screen_marks_the_current_locale_success() -> None:
    buttons = _screen_buttons(nav.language_screen("ru"))
    styles = {item.text: item.style for item in buttons}
    assert styles["Русский"] == "success"
    assert [item.style for item in buttons].count("primary") == 0


@pytest.mark.asyncio
async def test_menu_offers_the_chat_entry_as_the_primary_button() -> None:
    bot = FakeBot()
    await nav.on_menu(_query(cb_menu()), bot, FakeRepo(), FakeDB().factory())

    buttons = _buttons(bot.edited[0])
    primary = [item for item in buttons if item.style == "primary"]
    assert len(primary) == 1
    assert primary[0].callback_data == cb_chat()
    search = next(item for item in buttons if item.callback_data == cb_search_hint())
    assert search.style is None


@pytest.mark.asyncio
async def test_chat_callback_opens_the_chat_screen_and_keeps_the_mode() -> None:
    db = FakeDB()
    bot = FakeBot()
    query = _query(cb_chat())

    await nav.on_chat(query, bot, db.factory())

    query.answer.assert_awaited_once()
    assert await chat_mode_on(db.factory(), 7) is True
    assert bot.edited and not bot.sent
    assert _edited_message_id(bot.edited) == 10
    assert "Чат с Байтом" in bot.edited[0]["rich_message"].html
    datas = [item.callback_data for item in _buttons(bot.edited[0]) if item.callback_data]
    assert {cb_chat_exit(), cb_menu()} <= set(datas)


@pytest.mark.asyncio
async def test_chat_exit_clears_the_mode_and_draws_the_menu() -> None:
    db = FakeDB()
    await set_chat_mode(db.factory(), 7, True)
    bot = FakeBot()

    await nav.on_chat_exit(_query(cb_chat_exit()), bot, FakeRepo(), db.factory())

    assert await chat_mode_on(db.factory(), 7) is False
    assert bot.edited and not bot.sent
    assert any(item.text == "🔥 Пожар" for item in _buttons(bot.edited[0]))


def test_chat_callbacks_fit_telegram() -> None:
    assert len(cb_chat().encode("utf-8")) <= 64
    assert len(cb_chat_exit().encode("utf-8")) <= 64
    assert parse_cb(cb_chat()) == Callback("chat")
    assert parse_cb(cb_chat_exit()) == Callback("chat_exit")


def test_settings_has_exactly_one_primary_toggle() -> None:
    buttons = _screen_buttons(nav.settings_screen("ru", True))
    assert [item.style for item in buttons].count("primary") == 1
