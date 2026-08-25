from datetime import UTC, datetime, timedelta
from uuid import uuid4

from sqlalchemy import select

from app.brain.memory import MemoryClassification
from app.db.models import (
    ConversationModel,
    MemoryModel,
    MessageModel,
    ProfileModel,
    SaveSlotModel,
    SourceOutboxModel,
)
from app.db.source_repository import (
    OUTBOX_CLAIMED,
    OUTBOX_COMPLETED,
    OUTBOX_PENDING,
    SOURCE_MESSAGE,
    SourceRepository,
)
from app.memory_worker import MemoryWorker
from tests.conftest import make_database, make_settings


class _RecoveringClassifier:
    def __init__(self) -> None:
        self.available = False

    async def classify_memory(self, text: str) -> MemoryClassification:
        if not self.available:
            raise TimeoutError("local memory classifier unavailable")
        return MemoryClassification(decision="ProfileFact", importance=8, confidence=0.95)

    async def embed_memory_text(self, text: str) -> tuple[tuple[float, ...] | None, str | None]:
        return None, None


async def _enqueue_player_message(database: object, *, text: str, now: datetime) -> str:
    suffix = str(uuid4())
    profile_id = f"profile-{suffix}"
    slot_id = f"slot-{suffix}"
    conversation_id = f"conversation-{suffix}"
    message_id = f"message-{suffix}"
    async with database.session_factory() as session:  # type: ignore[attr-defined]
        session.add(ProfileModel(profile_id=profile_id, created_at=now))
        await session.flush()
        session.add(
            SaveSlotModel(
                row_id=slot_id,
                save_slot_id="demo-slot-1",
                profile_id=profile_id,
                created_at=now,
            )
        )
        await session.flush()
        session.add(
            ConversationModel(
                row_id=conversation_id,
                conversation_id=conversation_id,
                profile_id=profile_id,
                save_slot_row_id=slot_id,
                companion_id="mako",
                session_id=f"session-{suffix}",
                surface="mobile",
                created_at=now,
            )
        )
        await session.flush()
        session.add(
            MessageModel(
                row_id=message_id,
                message_id=message_id,
                conversation_row_id=conversation_id,
                profile_id=profile_id,
                save_slot_row_id=slot_id,
                companion_id="mako",
                request_id=f"request-{suffix}",
                sequence=1,
                speaker="player",
                source_mode="RealWorld",
                content=text,
                content_digest="a" * 64,
                time_context={"source": "RealWorld"},
                storage_class="Transient",
                retention_reason="test",
                expires_at=now + timedelta(days=7),
                audit_expires_at=now + timedelta(days=30),
                content_deleted_at=None,
                created_at=now,
                delivered_at=now,
            )
        )
        await SourceRepository(session).enqueue(SOURCE_MESSAGE, message_id)
        await session.commit()
    return message_id


async def test_worker_keeps_failed_source_and_promotes_it_after_runtime_recovers() -> None:
    database = await make_database(make_settings())
    now = datetime.now(UTC)
    await _enqueue_player_message(database, text="내 이름은 이재명이야", now=now)
    classifier = _RecoveringClassifier()
    worker = MemoryWorker(
        database,  # type: ignore[arg-type]
        classifier,  # type: ignore[arg-type]
        lease_seconds=5,
        max_attempts=1,
        batch_size=8,
    )

    assert await worker.drain_once() == 0
    async with database.session_factory() as session:
        outbox = (await session.execute(select(SourceOutboxModel))).scalar_one()
        assert outbox.state == OUTBOX_CLAIMED
        assert outbox.completed_at is None
        assert await SourceRepository(session).cursor() == 0
        outbox.lease_expires_at = now - timedelta(seconds=1)
        await session.commit()

    classifier.available = True
    assert await worker.drain_once() == 1
    async with database.session_factory() as session:
        outbox = (await session.execute(select(SourceOutboxModel))).scalar_one()
        memory = (await session.execute(select(MemoryModel))).scalar_one()
    assert outbox.state == OUTBOX_COMPLETED
    assert memory.text == "내 이름은 이재명이야"


async def test_recent_completed_source_recovery_requeues_and_rewinds_atomically() -> None:
    database = await make_database(make_settings())
    now = datetime.now(UTC)
    await _enqueue_player_message(database, text="내 이름은 이재명이야", now=now)
    async with database.session_factory() as session:
        repository = SourceRepository(session)
        claim = await repository.claim_next(now=now)
        assert claim is not None
        assert await repository.acknowledge(claim, now=now)

    async with database.session_factory() as session:
        repository = SourceRepository(session)
        dry_run = await repository.recover_unprocessed_messages(
            since=now - timedelta(days=7),
        )
        outbox = await session.get(SourceOutboxModel, dry_run[0])
        assert outbox is not None and outbox.state == OUTBOX_COMPLETED

        applied = await repository.recover_unprocessed_messages(
            since=now - timedelta(days=7),
            now=now,
            apply=True,
        )
        await session.commit()

    assert applied == dry_run
    async with database.session_factory() as session:
        outbox = await session.get(SourceOutboxModel, applied[0])
        cursor = await SourceRepository(session).cursor()
    assert outbox is not None and outbox.state == OUTBOX_PENDING
    assert cursor == applied[0] - 1
