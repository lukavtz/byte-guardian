"""First-start onboarding: the language picker that precedes the menu."""

from __future__ import annotations

import re
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.content.types import CategoryView
from app.handlers import commands, nav
from app.services.callbacks import Callback, cb_onb, parse_cb
from app.services.i18n import ENABLED_LOCALES, t
from app.ui.keyboards import keyboard_from_rich

USER_ID = 7


class _Result:
    def __init__(self, rows: list) -> None:
        self.rows = rows

    def first(self):
        return self.rows[0] if self.rows else None


class FakeDB:
    """Only the `users` columns this feature reads or writes."""

    def __init__(self, locale: str | None = None, chosen: bool = False) -> None:
        self.locales: dict[int, str] = {} if locale is None else {USER_ID: locale}
        self.chosen: dict[int, bool] = {USER_ID: chosen} if locale else {}

    def factory(self) -> FakeSessionFactory:
        return FakeSessionFactory(self)

    def execute(self, statement, params: dict) -> _Result:
        sql = " ".join(str(statement).split())
        if sql.startswith("SELECT locale, locale_chosen FROM users"):
            user_id = params["user_id"]
            if user_id not in self.locales:
                return _Result([])
            return _Result([(self.locales[user_id], self.chosen.get(user_id, False))])
        if sql.startswith("UPDATE users SET locale"):
            self.locales[params["user_id"]] = params["locale"]
            self.chosen[params["user_id"]] = True
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

    def categories(self) -> list[CategoryView]:
        return [CategoryView(slug="fire", title="Пожар", emoji="🔥", sort=1, threat_count=7)]

    def threats(self, slug: str, locale: str) -> list:
        return []

    def card(self, slug: str, locale: str):
        self.card_calls.append((slug, locale))
        return None

    def has_quiz(self, slug: str, locale: str) -> bool:
        return False


class FakeBot:
    def __init__(self) -> None:
        self.sent: list[dict] = []
        self.edited: list[dict] = []

    async def send_rich_message(self, **kwargs: object) -> SimpleNamespace:
        self.sent.append(kwargs)
        return SimpleNamespace(message_id=100 + len(self.sent))

    async def edit_message_text(self, **kwargs: object) -> SimpleNamespace:
        self.edited.append(kwargs)
        return SimpleNamespace(message_id=kwargs.get("message_id"))


def _message(*, language_code: str = "ru") -> SimpleNamespace:
    return SimpleNamespace(
        chat=SimpleNamespace(id=USER_ID, type="private"),
        from_user=SimpleNamespace(id=USER_ID, language_code=language_code),
        message_id=10,
        answer=AsyncMock(),
    )


def _query(data: str) -> SimpleNamespace:
    return SimpleNamespace(
        data=data,
        from_user=SimpleNamespace(id=USER_ID, language_code="ru"),
        message=SimpleNamespace(chat=SimpleNamespace(id=USER_ID, type="private"), message_id=10),
        answer=AsyncMock(),
    )


def _buttons(kwargs: dict) -> list[object]:
    rich = kwargs["rich_message"]
    markup = keyboard_from_rich(rich.html)
    return [item for row in (markup.inline_keyboard if markup else []) for item in row]


async def _first_start(bot: FakeBot, db: FakeDB, *, args: str | None = None, language_code: str = "ru") -> str:
    await commands.start(
        _message(language_code=language_code),
        SimpleNamespace(args=args),
        bot,
        FakeRepo(),
        db.factory(),
    )
    assert len(bot.sent) == 1
    return bot.sent[0]["rich_message"].html


@pytest.mark.asyncio
async def test_first_start_opens_the_language_picker_not_the_menu() -> None:
    bot = FakeBot()
    html = await _first_start(bot, FakeDB())

    assert t("ru", "onboarding.greeting") in html
    assert t("ru", "onboarding.question") in html
    assert t("ru", "menu.lead") not in html

    labels = [item.text for item in _buttons(bot.sent[0])]
    assert not any(label.endswith(t("ru", "button.sections")) for label in labels)
    datas = [item.callback_data for item in _buttons(bot.sent[0])]
    assert datas == [cb_onb(code) for code in ENABLED_LOCALES]


@pytest.mark.asyncio
async def test_first_start_writes_nothing_until_the_user_picks() -> None:
    db = FakeDB()
    await _first_start(FakeBot(), db)

    assert db.locales == {}
    assert db.chosen == {}


@pytest.mark.asyncio
async def test_first_start_preselects_the_telegram_language() -> None:
    bot = FakeBot()
    await _first_start(bot, FakeDB(), language_code="en-US")

    buttons = _buttons(bot.sent[0])
    primary = [item for item in buttons if item.style == "primary"]
    assert len(primary) == 1
    assert primary[0].callback_data == cb_onb("en")
    assert all(item.style is None for item in buttons if item is not primary[0])


@pytest.mark.asyncio
async def test_picking_a_language_saves_it_and_opens_the_menu() -> None:
    db = FakeDB()
    bot = FakeBot()
    query = _query(cb_onb("en"))

    await nav.on_onboarding_lang(query, bot, FakeRepo(), db.factory())

    query.answer.assert_awaited_once()
    assert db.locales[USER_ID] == "en"
    assert db.chosen[USER_ID] is True
    assert bot.edited and not bot.sent
    html = bot.edited[0]["rich_message"].html
    assert t("en", "menu.lead") in html
    assert t("en", "onboarding.question") not in html
    datas = [item.callback_data for item in _buttons(bot.edited[0])]
    assert nav.cb_sections() in datas


@pytest.mark.asyncio
async def test_first_start_buttons_carry_distinct_language_flags() -> None:
    bot = FakeBot()
    html = await _first_start(bot, FakeDB())

    labels = re.findall(r"<tg-button[^>]*>(.*?)</tg-button>", html, re.DOTALL)
    assert len(labels) == 4
    flags = [re.sub(r"<[^>]+>", "", label).split(" ", 1)[0] for label in labels]
    assert flags == ["🇷🇺", "🇬🇧", "🇨🇳", "🏳️"]
    assert len(set(flags)) == 4
    for plain in labels[:3]:
        assert "<tg-emoji" not in plain
    assert 'emoji-id="5220113606724557083"' in labels[3]


@pytest.mark.asyncio
async def test_repeat_start_skips_the_picker() -> None:
    bot = FakeBot()
    html = await _first_start(bot, FakeDB(locale="ru", chosen=True))

    assert t("ru", "onboarding.greeting") not in html
    assert t("ru", "menu.lead") in html
    datas = [item.callback_data for item in _buttons(bot.sent[0])]
    assert nav.cb_sections() in datas


@pytest.mark.asyncio
async def test_deep_link_before_the_choice_still_opens_the_picker() -> None:
    bot = FakeBot()
    repo = FakeRepo()

    await commands.start(
        _message(), SimpleNamespace(args="card_fire-0"), bot, repo, FakeDB().factory()
    )

    assert repo.card_calls == []
    assert t("ru", "onboarding.question") in bot.sent[0]["rich_message"].html


@pytest.mark.asyncio
async def test_unknown_language_pick_is_ignored() -> None:
    db = FakeDB()
    bot = FakeBot()

    await nav.on_onboarding_lang(_query("b1:onb:de"), bot, FakeRepo(), db.factory())

    assert db.locales == {}
    assert not bot.sent and not bot.edited


def test_onboarding_callback_is_parsed() -> None:
    assert parse_cb(cb_onb("zh")) == Callback("onb", arg="zh")


def test_onboarding_copy_exists_in_every_locale() -> None:
    keys = (
        "onboarding.greeting",
        "onboarding.about.1",
        "onboarding.about.2",
        "onboarding.question",
    )
    for code in ENABLED_LOCALES:
        for key in keys:
            value = t(code, key)
            assert value != key and len(value) > 3, (code, key)
    assert len({t(code, "onboarding.greeting") for code in ENABLED_LOCALES}) == 4
