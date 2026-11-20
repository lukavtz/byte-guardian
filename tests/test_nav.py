"""Navigation, deep links, language and settings — offline fakes only."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from aiogram.types import FSInputFile

from app.content.types import CardView, CategoryView, ThreatView
from app.handlers import commands, nav
from app.services.callbacks import (
    cb_card,
    cb_cat,
    cb_lang_menu,
    cb_menu,
    cb_search_hint,
    parse_cb,
)


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
        self.settings: dict[str, str] = {}
        self.users = dict(opt_in or {})
        self.locales = dict(locales or {})

    def factory(self) -> FakeSessionFactory:
        return FakeSessionFactory(self)

    def execute(self, statement, params: dict) -> _Result:
        sql = " ".join(str(statement).split())
        if sql.startswith("INSERT INTO settings"):
            self.settings[params["key"]] = params["value"]
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
    markup = kwargs.get("reply_markup")
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
async def test_start_payload_opens_card_and_caches_banner() -> None:
    db = FakeDB()
    factory = fake = db.factory()
    repo = FakeRepo()
    bot = FakeBot()
    message = _message()

    await commands.start(
        message, SimpleNamespace(args="card_fire-0"), bot, repo, factory
    )

    assert repo.card_calls[0] == ("fire-0", "ru")
    assert "Пожар в квартире" in bot.sent[0]["rich_message"].html
    import json
    assert json.loads(db.settings["banner_file_id:7"]) == "cached-file"
    assert isinstance(message.answer_photo.await_args.kwargs["photo"], FSInputFile)
    assert fake is factory


@pytest.mark.asyncio
async def test_start_without_payload_shows_menu_and_reuses_banner() -> None:
    import json

    db = FakeDB()
    db.settings["banner_file_id:7"] = json.dumps("cached-file")
    repo = FakeRepo()
    bot = FakeBot()
    message = _message()

    await commands.start(message, SimpleNamespace(args=None), bot, repo, db.factory())

    assert any(
        item.text == "🔥 Пожар" for item in _buttons(bot.sent[0])
    )
    assert message.answer_photo.await_args.kwargs["photo"] == "cached-file"


@pytest.mark.asyncio
async def test_banner_failure_still_shows_menu() -> None:
    repo = FakeRepo()
    bot = FakeBot()
    message = _message()
    message.answer_photo = AsyncMock(side_effect=RuntimeError("no photo"))

    await commands.start(message, SimpleNamespace(args=None), bot, repo, FakeDB().factory())

    assert bot.sent


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
    assert bot.edited
    assert any(item.text == "🔥 Пожар" for item in _buttons(bot.edited[0]))


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

    buttons = _buttons(bot.edited[0])
    datas = [item.callback_data for item in buttons if item.callback_data]
    assert "b1:cat:fire:2" in datas
    assert any(item.switch_inline_query_current_chat for item in buttons)


@pytest.mark.asyncio
async def test_language_callback_saves_and_rerenders_language_screen() -> None:
    db = FakeDB({7: False})
    bot = FakeBot()
    query = _query("b1:lang:en")

    await nav.on_lang(query, bot, db.factory())

    assert db.locales[7] == "en"
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
    labels = [item.text for item in _buttons(bot.edited[0])]
    assert {"Русский", "English", "中文", "Татарча"} <= set(labels)


@pytest.mark.asyncio
async def test_search_hint_is_private_only() -> None:
    bot = FakeBot()
    query = _query(cb_search_hint(), chat_type="group")

    await nav.on_search_hint(query, bot, FakeDB().factory())

    query.answer.assert_awaited_once()
    assert not bot.sent


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
