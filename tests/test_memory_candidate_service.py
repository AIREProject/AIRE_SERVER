"""CAI-P3-T01 source-backed memory acceptance fixtures."""

from datetime import UTC, datetime, timedelta
from uuid import uuid4

from sqlalchemy import func, select

from app.db.models import (
    ConversationModel,
    GameEventModel,
    MemoryModel,
    MemorySourceModel,
    MessageModel,
    ProfileModel,
    SaveSlotModel,
    SourceOutboxModel,
    SourceRetentionReferenceModel,
)
from app.db.source_repository import (
    OUTBOX_COMPLETED,
    SOURCE_EVENT,
    SOURCE_MESSAGE,
    SourceRepository,
    SourceScope,
)
from app.memory_candidate_service import (
    MemoryCandidate,
    MemoryCandidateService,
    render_event_memory,
)
from tests.conftest import make_database, make_settings

_NOW = datetime(2026, 8, 17, 12, 0, tzinfo=UTC)


async def _scope(database: object, *, profile_id: str = "profile-a") -> SourceScope:
    slot_row_id = f"slot-{profile_id}"
    async with database.session_factory() as session:  # type: ignore[attr-defined]
        session.add(ProfileModel(profile_id=profile_id, created_at=_NOW))
        await session.flush()
        session.add(
            SaveSlotModel(
                row_id=slot_row_id,
                save_slot_id="demo-slot-1",
                profile_id=profile_id,
                created_at=_NOW,
            )
        )
        await session.commit()
    return SourceScope(profile_id, slot_row_id, "mako")


async def _message(
    database: object,
    scope: SourceScope,
    *,
    text: str,
    speaker: str = "player",
    mode: str = "RealWorld",
) -> str:
    row_id = f"message-{uuid4()}"
    async with database.session_factory() as session:  # type: ignore[attr-defined]
        conversation_id = f"conversation-{uuid4()}"
        session.add(
            ConversationModel(
                row_id=conversation_id,
                conversation_id=conversation_id,
                profile_id=scope.profile_id,
                save_slot_row_id=scope.save_slot_row_id,
                companion_id=scope.companion_id,
                session_id=f"session-{uuid4()}",
                surface="mobile",
                created_at=_NOW,
            )
        )
        session.add(
            MessageModel(
                row_id=row_id,
                message_id=row_id,
                conversation_row_id=conversation_id,
                profile_id=scope.profile_id,
                save_slot_row_id=scope.save_slot_row_id,
                companion_id=scope.companion_id,
                request_id=f"request-{row_id}",
                sequence=1,
                speaker=speaker,
                source_mode=mode,
                content=text,
                content_digest="a" * 64,
                time_context={"source": mode},
                storage_class="Transient",
                retention_reason="test",
                expires_at=_NOW + timedelta(days=1),
                audit_expires_at=_NOW + timedelta(days=30),
                content_deleted_at=None,
                created_at=_NOW,
                delivered_at=_NOW,
            )
        )
        await SourceRepository(session).enqueue(SOURCE_MESSAGE, row_id)
        await session.commit()
    return row_id


async def _event(database: object, scope: SourceScope) -> tuple[str, str]:
    row_id = f"event-{uuid4()}"
    async with database.session_factory() as session:  # type: ignore[attr-defined]
        event = GameEventModel(
            row_id=row_id,
            event_id=row_id,
            profile_id=scope.profile_id,
            save_slot_row_id=scope.save_slot_row_id,
            companion_id=scope.companion_id,
            session_id="session-1",
            schema_version=1,
            event_type="Event.Rescue.Completed",
            importance="High",
            occurred_at=_NOW,
            game_time={"source": "GameWorld"},
            actor_id="mako",
            target_ids=["player"],
            body_hash="b" * 64,
            storage_class="Transient",
            retention_reason="test",
            expires_at=_NOW + timedelta(days=1),
            audit_expires_at=_NOW + timedelta(days=30),
            content_deleted_at=None,
            response_body={},
            received_at=_NOW,
        )
        session.add(event)
        await session.flush()
        await SourceRepository(session).enqueue(SOURCE_EVENT, row_id)
        await session.commit()
        return row_id, render_event_memory(event)


async def _claim(database: object, *, now: datetime = _NOW):
    async with database.session_factory() as session:  # type: ignore[attr-defined]
        claim = await SourceRepository(session).claim_next(now=now)
        assert claim is not None
        return claim


async def test_direct_realworld_message_creates_memory_and_promotes_source() -> None:
    database = await make_database(make_settings())
    scope = await _scope(database)
    source_id = await _message(database, scope, text="나는 밤이 무서워")
    claim = await _claim(database)

    accepted = await MemoryCandidateService(database).accept_and_acknowledge(  # type: ignore[arg-type]
        claim,
        MemoryCandidate("Preference", "나는 밤이 무서워", 6, scope, SOURCE_MESSAGE, source_id),
    )

    assert accepted is not None and accepted.created
    async with database.session_factory() as session:
        memory = await session.get(MemoryModel, accepted.memory_id)
        source = await session.get(MessageModel, source_id)
        references = await session.scalar(
            select(func.count()).select_from(SourceRetentionReferenceModel)
        )
        links = await session.scalar(select(func.count()).select_from(MemorySourceModel))
        outbox = await session.get(SourceOutboxModel, claim.source_seq)
    assert memory is not None and memory.memory_type == "Preference"
    assert source is not None and source.storage_class == "MemorySource"
    assert references == links == 1
    assert outbox is not None and outbox.state == OUTBOX_COMPLETED


async def test_duplicate_after_restart_reuses_memory_and_adds_second_source() -> None:
    database = await make_database(make_settings())
    scope = await _scope(database)
    first_id = await _message(database, scope, text="나는 밤이 무서워")
    first_claim = await _claim(database)
    service = MemoryCandidateService(database)  # type: ignore[arg-type]
    candidate = MemoryCandidate(
        "Preference", "나는 밤이 무서워", 6, scope, SOURCE_MESSAGE, first_id
    )
    first = await service.accept(first_claim, candidate)
    restart_claim = await _claim(database, now=_NOW + timedelta(seconds=61))
    recovered = await service.accept_and_acknowledge(restart_claim, candidate)
    assert recovered is not None and not recovered.created
    assert recovered.memory_id == first.memory_id

    second_id = await _message(database, scope, text="나는 밤이 무서워")
    second_claim = await _claim(database)
    second = await service.accept_and_acknowledge(
        second_claim,
        MemoryCandidate("Preference", "나는 밤이 무서워", 6, scope, SOURCE_MESSAGE, second_id),
    )

    assert second is not None and not second.created and second.memory_id == first.memory_id
    async with database.session_factory() as session:
        count = await session.scalar(select(func.count()).select_from(MemoryModel))
        links = await session.scalar(select(func.count()).select_from(MemorySourceModel))
    assert count == 1
    assert links == 2


async def test_companion_and_foreign_scope_candidates_are_rejected_and_acknowledged() -> None:
    database = await make_database(make_settings())
    scope = await _scope(database)
    companion_id = await _message(database, scope, text="나는 밤이 무서워", speaker="companion")
    companion_claim = await _claim(database)
    service = MemoryCandidateService(database)  # type: ignore[arg-type]
    assert await service.accept_and_acknowledge(
        companion_claim,
        MemoryCandidate("Preference", "나는 밤이 무서워", 6, scope, SOURCE_MESSAGE, companion_id),
    ) is None

    player_id = await _message(database, scope, text="나는 밤이 무서워")
    player_claim = await _claim(database)
    foreign = SourceScope("profile-other", scope.save_slot_row_id, scope.companion_id)
    assert await service.accept_and_acknowledge(
        player_claim,
        MemoryCandidate("Preference", "나는 밤이 무서워", 6, foreign, SOURCE_MESSAGE, player_id),
    ) is None

    async with database.session_factory() as session:
        count = await session.scalar(select(func.count()).select_from(MemoryModel))
        states = tuple((await session.execute(select(SourceOutboxModel.state))).scalars())
    assert count == 0
    assert states == (OUTBOX_COMPLETED, OUTBOX_COMPLETED)


async def test_verified_event_is_the_only_relationship_evidence_source() -> None:
    database = await make_database(make_settings())
    scope = await _scope(database)
    source_id, text = await _event(database, scope)
    claim = await _claim(database)

    accepted = await MemoryCandidateService(database).accept_and_acknowledge(  # type: ignore[arg-type]
        claim,
        MemoryCandidate("RelationshipEvidence", text, 8, scope, SOURCE_EVENT, source_id),
    )

    assert accepted is not None and accepted.created
