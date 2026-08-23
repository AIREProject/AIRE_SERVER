from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
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
from app.db.source_repository import OUTBOX_COMPLETED, SOURCE_MESSAGE, SourceRepository
from app.memory_worker import MemoryWorker
from tests.conftest import make_database, make_settings


class _Classifier:
    async def classify_memory(self, text: str) -> MemoryClassification:
        assert text == "나는 비 오는 날을 좋아해"
        return MemoryClassification(decision="Preference", importance=7, confidence=0.95)

    async def embed_memory_text(
        self, text: str
    ) -> tuple[tuple[float, ...] | None, str | None]:
        assert text == "나는 비 오는 날을 좋아해"
        return (0.6, 0.8), "test-embedding-v1"


@pytest.mark.parametrize("source_mode", ["RealWorld", "GameWorld"])
async def test_worker_stores_canonical_player_text_and_acknowledges(source_mode: str) -> None:
    database = await make_database(make_settings())
    now = datetime.now(UTC)
    profile_id = "profile-worker"
    slot_row_id = "slot-worker"
    conversation_id = str(uuid4())
    message_id = str(uuid4())
    async with database.session_factory() as session:
        session.add(ProfileModel(profile_id=profile_id, created_at=now))
        await session.flush()
        session.add(
            SaveSlotModel(
                row_id=slot_row_id,
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
                save_slot_row_id=slot_row_id,
                companion_id="mako",
                session_id="session-worker",
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
                save_slot_row_id=slot_row_id,
                companion_id="mako",
                request_id="request-worker",
                sequence=1,
                speaker="player",
                source_mode=source_mode,
                content="나는 비 오는 날을 좋아해",
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

    worker = MemoryWorker(
        database,  # type: ignore[arg-type]
        _Classifier(),  # type: ignore[arg-type]
        lease_seconds=60,
        max_attempts=3,
        batch_size=8,
    )
    assert await worker.drain_once() == 1

    async with database.session_factory() as session:
        memory = (await session.execute(select(MemoryModel))).scalar_one()
        outbox = (await session.execute(select(SourceOutboxModel))).scalar_one()
    assert memory.text == "나는 비 오는 날을 좋아해"
    assert memory.memory_type == "Preference"
    assert memory.importance == 7
    assert memory.embedding == [0.6, 0.8]
    assert memory.embedding_model == "test-embedding-v1"
    assert outbox.state == OUTBOX_COMPLETED
