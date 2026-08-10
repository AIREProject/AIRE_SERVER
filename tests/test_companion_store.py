"""대화 기억 저장소의 경계 동작 검증.

이 저장소는 프로세스 메모리에 있으므로 무한히 자라지 않는 것이 정확성만큼 중요하다.
"""

from datetime import UTC, datetime, timedelta

from app.brain.store import (
    MAX_HISTORY_TEXT,
    MAX_HISTORY_TURNS,
    ConversationMemory,
    InMemoryConversationStore,
    PendingSlot,
)


def make_pending(*, asked_at: datetime | None = None, ask_count: int = 1) -> PendingSlot:
    return PendingSlot(
        kind="gather_resource",
        quantity=None,
        ask_count=ask_count,
        asked_at=asked_at or datetime.now(UTC),
    )


def make_store(
    *,
    pending_ttl_seconds: float = 120.0,
    idle_ttl_seconds: float = 1800.0,
    max_entries: int = 10,
) -> InMemoryConversationStore:
    return InMemoryConversationStore(
        pending_ttl_seconds=pending_ttl_seconds,
        idle_ttl_seconds=idle_ttl_seconds,
        max_entries=max_entries,
    )


def test_unknown_key_returns_empty_memory() -> None:
    assert make_store().load("nobody").pending is None


def test_saved_memory_round_trips() -> None:
    store = make_store()
    pending = make_pending()

    store.save("conv-1", ConversationMemory(pending=pending))

    assert store.load("conv-1").pending == pending


def test_conversations_do_not_share_memory() -> None:
    """키가 다르면 남의 되묻기를 이어받으면 안 된다."""

    store = make_store()
    store.save("conv-1", ConversationMemory(pending=make_pending()))

    assert store.load("conv-2").pending is None


def test_expired_pending_is_forgotten() -> None:
    store = make_store(pending_ttl_seconds=60.0)
    stale = make_pending(asked_at=datetime.now(UTC) - timedelta(seconds=61))
    store.save("conv-1", ConversationMemory(pending=stale))

    assert store.load("conv-1").pending is None


def test_pending_within_ttl_survives() -> None:
    store = make_store(pending_ttl_seconds=60.0)
    fresh = make_pending(asked_at=datetime.now(UTC) - timedelta(seconds=30))
    store.save("conv-1", ConversationMemory(pending=fresh))

    assert store.load("conv-1").pending is not None


def test_saving_empty_memory_drops_the_entry() -> None:
    """아무것도 기억하지 않는 대화가 자리를 차지하면 안 된다."""

    store = make_store()
    store.save("conv-1", ConversationMemory(pending=make_pending()))

    store.save("conv-1", ConversationMemory())

    assert store.load("conv-1").pending is None
    assert len(store._entries) == 0


def test_store_evicts_the_least_recently_saved_entry() -> None:
    store = make_store(max_entries=2)
    store.save("conv-1", ConversationMemory(pending=make_pending()))
    store.save("conv-2", ConversationMemory(pending=make_pending()))

    store.save("conv-3", ConversationMemory(pending=make_pending()))

    assert store.load("conv-1").pending is None
    assert store.load("conv-2").pending is not None
    assert store.load("conv-3").pending is not None


def test_resaving_a_key_refreshes_its_eviction_order() -> None:
    """계속 쓰이는 대화가 오래됐다는 이유로 밀려나면 안 된다."""

    store = make_store(max_entries=2)
    store.save("conv-1", ConversationMemory(pending=make_pending()))
    store.save("conv-2", ConversationMemory(pending=make_pending()))
    store.save("conv-1", ConversationMemory(pending=make_pending(ask_count=2)))

    store.save("conv-3", ConversationMemory(pending=make_pending()))

    assert store.load("conv-1").pending is not None
    assert store.load("conv-2").pending is None


def test_ask_count_cap_is_reported_by_the_slot() -> None:
    from app.brain.store import MAX_ASK_COUNT

    assert make_pending(ask_count=MAX_ASK_COUNT - 1).may_ask_again
    assert not make_pending(ask_count=MAX_ASK_COUNT).may_ask_again


def test_asked_again_advances_the_count_and_timestamp() -> None:
    first = make_pending(asked_at=datetime.now(UTC) - timedelta(seconds=10))

    second = first.asked_again(now=datetime.now(UTC))

    assert second.ask_count == first.ask_count + 1
    assert second.asked_at > first.asked_at
    # 이미 말한 수량은 되물어도 유지된다.
    assert second.quantity == first.quantity


def test_history_keeps_only_the_most_recent_turns() -> None:
    memory = ConversationMemory()
    for n in range(MAX_HISTORY_TURNS):
        memory = memory.appended(f"질문{n}", f"대답{n}")

    assert len(memory.recent_turns) == MAX_HISTORY_TURNS
    # 앞쪽이 버려지고 마지막 왕복은 남는다.
    assert memory.recent_turns[-2].text == f"질문{MAX_HISTORY_TURNS - 1}"
    assert memory.recent_turns[-1].text == f"대답{MAX_HISTORY_TURNS - 1}"
    assert "질문0" not in [turn.text for turn in memory.recent_turns]


def test_history_records_both_speakers_in_order() -> None:
    memory = ConversationMemory().appended("따라와", "알겠어.")

    assert [(t.speaker, t.text) for t in memory.recent_turns] == [
        ("player", "따라와"),
        ("companion", "알겠어."),
    ]


def test_history_clips_overlong_turns() -> None:
    memory = ConversationMemory().appended("가" * 500, "나" * 500)

    for turn in memory.recent_turns:
        assert len(turn.text) == MAX_HISTORY_TEXT


def test_history_collapses_whitespace() -> None:
    memory = ConversationMemory().appended("따라와\n\n  줘", "알겠어.")

    assert memory.recent_turns[0].text == "따라와 줘"


def test_history_only_entry_expires_on_idle_ttl() -> None:
    """되묻기 슬롯이 없어도 기억이 대화보다 오래 살면 안 된다."""

    store = make_store(idle_ttl_seconds=0.0)
    store.save("conv-1", ConversationMemory().appended("따라와", "알겠어."))

    assert store.load("conv-1").recent_turns == ()


def test_idle_ttl_drops_the_whole_entry_including_a_fresh_slot() -> None:
    store = make_store(pending_ttl_seconds=9999.0, idle_ttl_seconds=0.0)
    store.save(
        "conv-1",
        ConversationMemory(pending=make_pending()).appended("따라와", "알겠어."),
    )

    memory = store.load("conv-1")

    assert memory.pending is None
    assert memory.recent_turns == ()


def test_expired_slot_does_not_take_the_history_with_it() -> None:
    """되묻기만 낡았을 때 대화 기록까지 버리면 문맥이 통째로 사라진다."""

    store = make_store(pending_ttl_seconds=60.0)
    stale = make_pending(asked_at=datetime.now(UTC) - timedelta(seconds=61))
    store.save(
        "conv-1",
        ConversationMemory(pending=stale).appended("저것 캐 줘", "무엇을 캐면 될까?"),
    )

    memory = store.load("conv-1")

    assert memory.pending is None
    assert len(memory.recent_turns) == 2
