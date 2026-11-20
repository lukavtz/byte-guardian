from __future__ import annotations

from app.services.conversation import TURN_CHARS, ConversationStore


def _store(*, turns: int = 6, ttl: float = 1800.0, clock=None) -> ConversationStore:
    now = [0.0]
    return ConversationStore(turns=turns, ttl=ttl, clock=clock or (lambda: now[0]))


def test_turns_are_kept_per_user_and_capped() -> None:
    store = _store(turns=3)
    for index in range(5):
        store.append(chat_id=1, user_id=10, group=False, role="user", text=f"u{index}")
    assert [turn.text for turn in store.recent(chat_id=1, user_id=10, group=False)] == [
        "u2",
        "u3",
        "u4",
    ]


def test_one_group_chat_keeps_two_users_apart() -> None:
    store = _store()
    store.append(chat_id=7, user_id=1, group=True, role="user", text="первый")
    store.append(chat_id=7, user_id=2, group=True, role="user", text="второй")
    assert [turn.text for turn in store.recent(chat_id=7, user_id=1, group=True)] == ["первый"]
    assert [turn.text for turn in store.recent(chat_id=7, user_id=2, group=True)] == ["второй"]


def test_expired_turns_are_dropped_by_the_clock() -> None:
    now = [0.0]
    store = _store(ttl=10.0, clock=lambda: now[0])
    store.append(chat_id=1, user_id=10, group=False, role="user", text="старое")
    now[0] = 11.0
    assert store.recent(chat_id=1, user_id=10, group=False) == ()


def test_clear_forgets_one_conversation() -> None:
    store = _store()
    store.append(chat_id=1, user_id=10, group=False, role="user", text="вопрос")
    store.clear(chat_id=1, user_id=10)
    assert store.recent(chat_id=1, user_id=10, group=False) == ()


def test_history_text_is_scrubbed_and_clipped() -> None:
    store = _store()
    store.append(
        chat_id=1,
        user_id=10,
        group=False,
        role="user",
        text="ключ sk-abcdefghijklmnopqrstuvwx и Bearer abcdefghijklmnop",
    )
    turn = store.recent(chat_id=1, user_id=10, group=False)[0]
    assert "sk-" not in turn.text
    assert "Bearer" not in turn.text

    store.append(chat_id=1, user_id=10, group=False, role="user", text="а" * 900)
    assert len(store.recent(chat_id=1, user_id=10, group=False)[-1].text) <= TURN_CHARS


def test_conversation_count_is_bounded() -> None:
    store = ConversationStore(clock=lambda: 0.0, max_conversations=2)
    for user in range(3):
        store.append(chat_id=1, user_id=user, group=False, role="user", text="привет")
    assert store.recent(chat_id=1, user_id=0, group=False) == ()
    assert store.recent(chat_id=1, user_id=2, group=False)[0].text == "привет"


def test_roles_are_preserved() -> None:
    store = _store()
    store.append(chat_id=1, user_id=10, group=False, role="user", text="вопрос")
    store.append(chat_id=1, user_id=10, group=False, role="assistant", text="ответ")
    turns = store.recent(chat_id=1, user_id=10, group=False)
    assert [turn.role for turn in turns] == ["user", "assistant"]