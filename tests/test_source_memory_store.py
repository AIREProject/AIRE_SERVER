from datetime import UTC, datetime, timedelta

from sqlalchemy import select

from app.db.models import (
    ConversationModel,
    MemoryModel,
    MemorySourceModel,
    MessageModel,
    ProfileModel,
    SaveSlotModel,
)
from app.db.source_repository import SOURCE_MESSAGE, SourceScope
from app.source_memory_store import SourceBackedMemoryStore, render_prompt_memory
from tests.conftest import make_database, make_settings

_NOW = datetime(2026, 8, 17, 12, 0, tzinfo=UTC)


async def _memory(
    database: object,
    *,
    memory_id: str,
    profile_id: str = "profile-a",
    slot_id: str = "slot-a",
    companion_id: str = "mako",
    text: str,
    pinned: bool = False,
    deleted_source: bool = False,
    importance: int = 6,
    memory_type: str = "Preference",
) -> None:
    async with database.session_factory() as session:  # type: ignore[attr-defined]
        if await session.get(ProfileModel, profile_id) is None:
            session.add(ProfileModel(profile_id=profile_id, created_at=_NOW))
            await session.flush()
            session.add(
                SaveSlotModel(
                    row_id=slot_id,
                    save_slot_id=slot_id,
                    profile_id=profile_id,
                    created_at=_NOW,
                )
            )
            await session.flush()
        conversation_id = f"conversation-{memory_id}"
        message_id = f"message-{memory_id}"
        session.add(
            ConversationModel(
                row_id=conversation_id,
                conversation_id=conversation_id,
                profile_id=profile_id,
                save_slot_row_id=slot_id,
                companion_id=companion_id,
                session_id=f"session-{memory_id}",
                surface="mobile",
                created_at=_NOW,
            )
        )
        session.add(
            MessageModel(
                row_id=message_id,
                message_id=message_id,
                conversation_row_id=conversation_id,
                profile_id=profile_id,
                save_slot_row_id=slot_id,
                companion_id=companion_id,
                request_id=f"request-{memory_id}",
                sequence=1,
                speaker="player",
                source_mode="RealWorld",
                content=None if deleted_source else text,
                content_digest="a" * 64,
                time_context={},
                storage_class="MemorySource",
                retention_reason="test",
                expires_at=None,
                audit_expires_at=_NOW + timedelta(days=30),
                content_deleted_at=_NOW if deleted_source else None,
                created_at=_NOW,
                delivered_at=_NOW,
            )
        )
        session.add(
            MemoryModel(
                memory_id=memory_id,
                profile_id=profile_id,
                save_slot_row_id=slot_id,
                companion_id=companion_id,
                memory_type=memory_type,
                text=text,
                normalized_text=text.casefold(),
                importance=importance,
                pinned=pinned,
                status="Active",
                created_at=_NOW,
                recalled_at=None,
                recall_count=0,
                embedding=None,
                embedding_model=None,
            )
        )
        session.add(
            MemorySourceModel(
                row_id=f"source-{memory_id}",
                memory_id=memory_id,
                source_type=SOURCE_MESSAGE,
                source_id=message_id,
                source_mode="RealWorld",
                occurred_at=_NOW,
                created_at=_NOW,
            )
        )
        await session.commit()


async def test_recall_is_scoped_relevant_and_keeps_only_trace_ids_in_prompt_text() -> None:
    database = await make_database(make_settings())
    await _memory(database, memory_id="memory-night", text="나는 밤이 무서워")
    await _memory(database, memory_id="memory-stone", text="나는 돌을 좋아해")
    await _memory(
        database,
        memory_id="memory-foreign",
        profile_id="profile-b",
        slot_id="slot-b",
        text="나는 밤이 무서워",
    )
    recalled = await SourceBackedMemoryStore(database).recall(
        SourceScope("profile-a", "slot-a", "mako"),
        query="밤이 무서워",
        source_mode="RealWorld",
    )

    assert [(item.trace_id, item.text) for item in recalled] == [
        ("M0", "나는 밤이 무서워"),
    ]
    assert recalled[0].memory_id == "memory-night"


async def test_purged_source_and_unrelated_query_fail_closed() -> None:
    database = await make_database(make_settings())
    await _memory(database, memory_id="memory-purged", text="나는 밤이 무서워", deleted_source=True)
    store = SourceBackedMemoryStore(database)
    scope = SourceScope("profile-a", "slot-a", "mako")

    assert await store.recall(scope, query="밤", source_mode="RealWorld") == ()
    assert await store.recall(scope, query="레시피", source_mode="RealWorld") == ()


async def test_explicit_memory_question_recalls_preferences_without_embeddings() -> None:
    database = await make_database(make_settings())
    await _memory(database, memory_id="memory-wood", text="나는 나무가 너무좋아")

    recalled = await SourceBackedMemoryStore(database).recall(
        SourceScope("profile-a", "slot-a", "mako"),
        query="내가 뭘 좋아하는지 기억해?",
        source_mode="RealWorld",
    )

    assert [(item.trace_id, item.text) for item in recalled] == [("M0", "나는 나무가 너무좋아")]

    second = await SourceBackedMemoryStore(database).recall(
        SourceScope("profile-a", "slot-a", "mako"),
        query="내가 좋아하는 게 뭐야?",
        source_mode="RealWorld",
    )
    assert [item.text for item in second] == ["나는 나무가 너무좋아"]

    relevant = await SourceBackedMemoryStore(database).recall(
        SourceScope("profile-a", "slot-a", "mako"),
        query="나무 좋아해?",
        source_mode="RealWorld",
    )
    assert [item.text for item in relevant] == ["나는 나무가 너무좋아"]


async def test_explicit_promise_question_recalls_only_promise_memories() -> None:
    database = await make_database(make_settings())
    await _memory(
        database,
        memory_id="memory-promise",
        text="나는 다시 대통령이 되고싶어",
        memory_type="Promise",
    )
    await _memory(database, memory_id="memory-name", text="제이름은 대통령 윤 석열입니다")

    recalled = await SourceBackedMemoryStore(database).recall(
        SourceScope("profile-a", "slot-a", "mako"),
        query="약속 기억",
        source_mode="RealWorld",
    )

    assert [item.memory_id for item in recalled] == ["memory-promise"]


async def test_unrelated_personal_question_does_not_offer_a_name_memory() -> None:
    database = await make_database(make_settings())
    await _memory(database, memory_id="memory-name", text="제이름은 대통령 윤 석열입니다")

    recalled = await SourceBackedMemoryStore(database).recall(
        SourceScope("profile-a", "slot-a", "mako"),
        query="내가 지지하는 것은",
        source_mode="RealWorld",
    )

    assert recalled == ()


async def test_attached_name_word_ranks_the_profile_memory_first() -> None:
    database = await make_database(make_settings())
    await _memory(
        database,
        memory_id="memory-name",
        text="제이름은 대통령 윤 석열입니다",
        importance=8,
    )
    await _memory(database, memory_id="memory-preference", text="나는 나무가 너무좋아")

    recalled = await SourceBackedMemoryStore(database).recall(
        SourceScope("profile-a", "slot-a", "mako"),
        query="내 이름은?",
        source_mode="RealWorld",
    )

    assert recalled[0].memory_id == "memory-name"
    assert recalled[0].text == "제이름은 대통령 윤 석열입니다"


async def test_name_question_excludes_other_profile_facts_and_does_not_count_them() -> None:
    database = await make_database(make_settings())
    await _memory(
        database,
        memory_id="memory-name-specific",
        text="내 이름은 이재명이야",
        memory_type="ProfileFact",
    )
    await _memory(
        database,
        memory_id="memory-commute-specific",
        text="출근시간은 9시 반이야",
        memory_type="ProfileFact",
    )
    store = SourceBackedMemoryStore(database)
    scope = SourceScope("profile-a", "slot-a", "mako")

    recalled = await store.recall(
        scope,
        query="내 이름 뭐라고?",
        direct_recall=True,
        source_mode="RealWorld",
    )
    await store.record_used(scope, tuple(item.memory_id for item in recalled), _NOW)

    assert [item.memory_id for item in recalled] == ["memory-name-specific"]
    async with database.session_factory() as session:
        name = await session.get(MemoryModel, "memory-name-specific")
        commute = await session.get(MemoryModel, "memory-commute-specific")
    assert name is not None and name.recall_count == 1
    assert commute is not None and commute.recall_count == 0


async def test_compound_commute_query_recalls_both_start_and_end_times() -> None:
    database = await make_database(make_settings())
    await _memory(
        database,
        memory_id="memory-work-start",
        text="출근시간은 9시 반이야",
        memory_type="ProfileFact",
    )
    await _memory(
        database,
        memory_id="memory-work-end",
        text="퇴근은 언제나 6시 반이야",
        memory_type="ProfileFact",
    )

    recalled = await SourceBackedMemoryStore(database).recall(
        SourceScope("profile-a", "slot-a", "mako"),
        query="출퇴근 다 하면 하루 몇시간이지",
        source_mode="RealWorld",
    )

    assert {item.memory_id for item in recalled} == {
        "memory-work-start",
        "memory-work-end",
    }


async def test_prompt_memory_budget_never_exceeds_three_entries_or_360_characters() -> None:
    database = await make_database(make_settings())
    for index in range(4):
        await _memory(
            database,
            memory_id=f"memory-{index}",
            text=f"밤이 무서워 {index} {'가' * 100}",
        )
    recalled = await SourceBackedMemoryStore(database).recall(
        SourceScope("profile-a", "slot-a", "mako"), query="밤이", source_mode="RealWorld"
    )

    assert len(recalled) <= 3
    assert sum(len(render_prompt_memory(item)) for item in recalled) <= 360


async def test_recall_counter_is_capped_in_scoring_and_archive_candidates_do_not_mutate() -> None:
    database = await make_database(make_settings())
    await _memory(database, memory_id="memory-old", text="나는 밤이 무서워")
    await _memory(database, memory_id="memory-pinned", text="나는 밤을 좋아해", pinned=True)
    store = SourceBackedMemoryStore(database)
    scope = SourceScope("profile-a", "slot-a", "mako")
    for _ in range(7):
        recalled = await store.recall(scope, query="밤이", source_mode="RealWorld")
        assert recalled
        await store.record_used(scope, ("memory-old",), _NOW)

    async with database.session_factory() as session:
        memory = await session.get(MemoryModel, "memory-old")
        assert memory is not None and memory.recall_count == 7
    candidates = await store.archive_candidates(
        scope, threshold=10.0, now=_NOW + timedelta(days=365)
    )
    assert candidates == ("memory-old",)
    async with database.session_factory() as session:
        statuses = tuple((await session.execute(select(MemoryModel.status))).scalars())
    assert statuses == ("Active", "Active")


async def test_context_only_recall_uses_importance_gate_cooldown_and_actual_use_tracking() -> None:
    database = await make_database(make_settings())
    await _memory(
        database,
        memory_id="memory-night-cave",
        text="동굴의 밤은 무서워",
        importance=6,
    )
    await _memory(
        database,
        memory_id="memory-low-night",
        text="밤에는 조용히 걷고 싶어",
        importance=5,
    )
    store = SourceBackedMemoryStore(database)
    scope = SourceScope("profile-a", "slot-a", "mako")

    recalled = await store.recall(
        scope,
        query="안녕",
        context_query="동굴 밤",
        source_mode="GameWorld",
        now=_NOW,
    )
    assert [item.memory_id for item in recalled] == ["memory-night-cave"]
    assert recalled[0].required is True
    async with database.session_factory() as session:
        memory = await session.get(MemoryModel, "memory-night-cave")
        assert memory is not None and memory.recall_count == 0

    await store.record_used(scope, ("memory-night-cave",), _NOW)
    assert (
        await store.recall(
            scope,
            query="안녕",
            context_query="동굴 밤",
            source_mode="GameWorld",
            now=_NOW + timedelta(minutes=5),
        )
        == ()
    )
