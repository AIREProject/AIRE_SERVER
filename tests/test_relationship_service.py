"""CAI-P4 deterministic relationship and cross-device vertical-slice fixtures."""

from datetime import UTC, datetime, timedelta
from uuid import uuid4

from sqlalchemy import func, select

from app.db.event_repository import SqlAlchemyEventRepository
from app.db.models import (
    ConversationModel,
    MessageModel,
    ProfileModel,
    RelationshipStateAuditModel,
    RelationshipStateEvidenceModel,
    RelationshipStateModel,
    SaveSlotModel,
)
from app.db.source_repository import SOURCE_MESSAGE, SourceRepository, SourceScope
from app.event_models import CreateGameEventRequest
from app.event_service import EventService
from app.identity import AuthenticatedDevice, DeviceRole
from app.memory_candidate_service import MemoryCandidate, MemoryCandidateService
from app.memory_service import MemoryService
from app.relationship_service import RelationshipPresentationStore, RelationshipService
from app.source_memory_store import SourceBackedMemoryStore
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


async def _accept_night_fear_preference(database: object, scope: SourceScope) -> str:
    message_id = f"message-{uuid4()}"
    conversation_id = f"conversation-{uuid4()}"
    async with database.session_factory() as session:  # type: ignore[attr-defined]
        session.add(
            ConversationModel(
                row_id=conversation_id,
                conversation_id=conversation_id,
                profile_id=scope.profile_id,
                save_slot_row_id=scope.save_slot_row_id,
                companion_id=scope.companion_id,
                session_id=f"mobile-{uuid4()}",
                surface="mobile",
                created_at=_NOW,
            )
        )
        session.add(
            MessageModel(
                row_id=message_id,
                message_id=message_id,
                conversation_row_id=conversation_id,
                profile_id=scope.profile_id,
                save_slot_row_id=scope.save_slot_row_id,
                companion_id=scope.companion_id,
                request_id=f"request-{message_id}",
                sequence=1,
                speaker="player",
                source_mode="RealWorld",
                content="나는 밤에 혼자 다니는 게 무서워",
                content_digest="a" * 64,
                time_context={"source": "RealWorld", "day": 1, "hour": 21, "period": "Night"},
                storage_class="Transient",
                retention_reason="test",
                expires_at=_NOW + timedelta(days=1),
                audit_expires_at=_NOW + timedelta(days=30),
                content_deleted_at=None,
                created_at=_NOW,
                delivered_at=_NOW,
            )
        )
        await SourceRepository(session).enqueue(SOURCE_MESSAGE, message_id)
        await session.commit()
    async with database.session_factory() as session:  # type: ignore[attr-defined]
        claim = await SourceRepository(session).claim_next(now=_NOW)
    assert claim is not None
    accepted = await MemoryCandidateService(database).accept_and_acknowledge(  # type: ignore[arg-type]
        claim,
        MemoryCandidate(
            "Preference",
            "나는 밤에 혼자 다니는 게 무서워",
            7,
            scope,
            SOURCE_MESSAGE,
            message_id,
        ),
        now=_NOW,
    )
    assert accepted is not None
    return accepted.memory_id


def _event(
    event_id: str,
    *,
    event_type: str,
    occurred_at: datetime,
    actor_id: str = "mako",
    target_ids: list[str] | None = None,
    period: str = "Night",
) -> CreateGameEventRequest:
    return CreateGameEventRequest.model_validate(
        {
            "schema_version": 1,
            "event_id": event_id,
            "session_id": f"game-{event_id}",
            "save_slot_id": "demo-slot-1",
            "companion_id": "mako",
            "type": event_type,
            "occurred_at": occurred_at.isoformat(),
            "time_context": {"source": "GameWorld", "day": 2, "hour": 22, "period": period},
            "actor_id": actor_id,
            "target_ids": target_ids or ["player"],
            "payload": {},
        }
    )


async def _accept_event(
    database: object, scope: SourceScope, request: CreateGameEventRequest
) -> None:
    identity = AuthenticatedDevice(scope.profile_id, "game-device", DeviceRole.GAME_CLIENT)
    async with database.session_factory() as session:  # type: ignore[attr-defined]
        await EventService(
            SqlAlchemyEventRepository(session),
            event_retention_days=7,
            audit_retention_days=30,
            relationship_service=RelationshipService(session),
        ).create_event(request, identity, body_hash="b" * 64)


async def _state(database: object, scope: SourceScope) -> str:
    async with database.session_factory() as session:  # type: ignore[attr-defined]
        row = await session.scalar(
            select(RelationshipStateModel).where(
                RelationshipStateModel.profile_id == scope.profile_id,
                RelationshipStateModel.save_slot_row_id == scope.save_slot_row_id,
                RelationshipStateModel.companion_id == scope.companion_id,
            )
        )
    return "Low" if row is None else row.state


async def test_mobile_memory_event_recall_and_source_deletion_form_one_vertical_slice() -> None:
    database = await make_database(make_settings())
    scope = await _scope(database)
    memory_id = await _accept_night_fear_preference(database, scope)

    first_event_at = _NOW + timedelta(days=1)
    await _accept_event(
        database,
        scope,
        _event("danger-night", event_type="Event.Danger.Detected", occurred_at=first_event_at),
    )
    assert await _state(database, scope) == "Growing"
    store = SourceBackedMemoryStore(database)
    recalled = await store.recall(
        scope, query="밤에 혼자", source_mode="GameWorld"
    )
    assert [(item.memory_id, item.text) for item in recalled] == [
        (memory_id, "나는 밤에 혼자 다니는 게 무서워")
    ]
    assert await store.recall(
        SourceScope(scope.profile_id, "other-slot", scope.companion_id),
        query="밤에 혼자",
        source_mode="GameWorld",
    ) == ()
    assert await store.recall(
        SourceScope(scope.profile_id, scope.save_slot_row_id, "other-companion"),
        query="밤에 혼자",
        source_mode="GameWorld",
    ) == ()

    await _accept_event(
        database,
        scope,
        _event(
            "rescue-night",
            event_type="Event.Rescue.Completed",
            occurred_at=first_event_at + timedelta(hours=25),
        ),
    )
    assert await _state(database, scope) == "High"
    assert await RelationshipPresentationStore(database).read(scope) == "High"  # type: ignore[arg-type]

    async with database.session_factory() as session:
        evidence_count = await session.scalar(
            select(func.count()).select_from(RelationshipStateEvidenceModel)
        )
        audits = tuple((await session.execute(select(RelationshipStateAuditModel))).scalars())
    assert evidence_count == 2
    assert [(audit.previous_state, audit.next_state) for audit in audits] == [
        ("Low", "Growing"),
        ("Growing", "High"),
    ]

    identity = AuthenticatedDevice(scope.profile_id, "web-device", DeviceRole.WEB_CLIENT)
    async with database.session_factory() as session:  # type: ignore[attr-defined]
        await MemoryService(session).delete(identity, memory_id, reason="user-request")
        audits = tuple((await session.execute(select(RelationshipStateAuditModel))).scalars())
    assert await _state(database, scope) == "Low"
    assert audits[-1].reason == "SourceInvalidated"
    assert (
        await store.recall(scope, query="밤", source_mode="GameWorld")
        == ()
    )


async def test_repeated_unrelated_and_foreign_events_do_not_create_relationship_evidence() -> None:
    database = await make_database(make_settings())
    scope = await _scope(database)
    await _accept_night_fear_preference(database, scope)
    first_event_at = _NOW + timedelta(days=1)
    await _accept_event(
        database,
        scope,
        _event("danger-first", event_type="Event.Danger.Detected", occurred_at=first_event_at),
    )
    await _accept_event(
        database,
        scope,
        _event(
            "danger-repeated",
            event_type="Event.Danger.Detected",
            occurred_at=first_event_at + timedelta(hours=1),
        ),
    )
    await _accept_event(
        database,
        scope,
        _event(
            "danger-unrelated",
            event_type="Event.Danger.Detected",
            occurred_at=first_event_at + timedelta(days=2),
            actor_id="other-companion",
        ),
    )
    foreign_scope = await _scope(database, profile_id="profile-b")
    await _accept_event(
        database,
        foreign_scope,
        _event(
            "danger-foreign",
            event_type="Event.Danger.Detected",
            occurred_at=first_event_at + timedelta(days=3),
        ),
    )

    async with database.session_factory() as session:
        evidence = tuple((await session.execute(select(RelationshipStateEvidenceModel))).scalars())
    assert len(evidence) == 1
    assert evidence[0].profile_id == scope.profile_id
    assert await _state(database, scope) == "Growing"
    assert await _state(database, foreign_scope) == "Low"
