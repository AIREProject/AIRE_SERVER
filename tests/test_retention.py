"""Deterministic retention, source outbox, and legacy quarantine fixtures."""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import uuid4

import pytest
from sqlalchemy import select

from app.db.models import (
    ConversationModel,
    EpisodicMemoryModel,
    GameEventModel,
    LegacyImportReportModel,
    MessageModel,
    ProfileModel,
    SaveSlotModel,
    SourceOutboxModel,
)
from app.db.source_repository import (
    OUTBOX_COMPLETED,
    OUTBOX_TOMBSTONE,
    SOURCE_EVENT,
    SOURCE_MESSAGE,
    SourceNotFoundError,
    SourceRepository,
    SourceScope,
)
from app.retention import (
    LegacyMemoryMaintenance,
    RetentionService,
    _sweep_transcript_quarantine,
)
from tests.conftest import make_database, make_settings

_NOW = datetime(2026, 8, 16, 12, 0, tzinfo=UTC)


async def _scope(database: object) -> tuple[str, str, str, str]:
    profile_id = f"profile-{uuid4()}"
    slot_row_id = f"slot-row-{uuid4()}"
    companion_id = "mako"
    conversation_row_id = f"conversation-row-{uuid4()}"
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
        await session.flush()
        session.add(
            ConversationModel(
                row_id=conversation_row_id,
                conversation_id=f"conversation-{uuid4()}",
                profile_id=profile_id,
                save_slot_row_id=slot_row_id,
                companion_id=companion_id,
                session_id="session-1",
                surface="mobile",
                created_at=_NOW,
            )
        )
        await session.commit()
    return profile_id, slot_row_id, companion_id, conversation_row_id


async def _message(
    database: object,
    *,
    scope: tuple[str, str, str, str],
    message_id: str,
    expires_at: datetime,
    sequence: int = 1,
) -> MessageModel:
    profile_id, slot_row_id, companion_id, conversation_row_id = scope
    async with database.session_factory() as session:  # type: ignore[attr-defined]
        row = MessageModel(
            row_id=f"message-row-{uuid4()}",
            message_id=message_id,
            conversation_row_id=conversation_row_id,
            profile_id=profile_id,
            save_slot_row_id=slot_row_id,
            companion_id=companion_id,
            request_id=f"request-{message_id}",
            sequence=sequence,
            speaker="player",
            source_mode="RealWorld",
            content="원문 보존 테스트",
            content_digest="a" * 64,
            time_context={"source": "RealWorld"},
            storage_class="Transient",
            retention_reason="ChatUserDefault",
            expires_at=expires_at,
            audit_expires_at=_NOW + timedelta(days=30),
            content_deleted_at=None,
            created_at=_NOW,
            delivered_at=_NOW,
        )
        session.add(row)
        await session.commit()
        return row


async def _event(
    database: object,
    *,
    scope: tuple[str, str, str, str],
    event_id: str,
    expires_at: datetime,
) -> GameEventModel:
    profile_id, slot_row_id, companion_id, _conversation_row_id = scope
    async with database.session_factory() as session:  # type: ignore[attr-defined]
        row = GameEventModel(
            row_id=f"event-row-{uuid4()}",
            event_id=event_id,
            profile_id=profile_id,
            save_slot_row_id=slot_row_id,
            companion_id=companion_id,
            session_id="session-1",
            schema_version=1,
            event_type="Event.Danger.Detected",
            importance="High",
            occurred_at=_NOW,
            game_time={"hour": 3},
            actor_id="actor-1",
            target_ids=["target-1"],
            body_hash="b" * 64,
            storage_class="Transient",
            retention_reason="EventDefault",
            expires_at=expires_at,
            audit_expires_at=_NOW + timedelta(days=30),
            content_deleted_at=None,
            response_body={"payload": {}},
            received_at=_NOW,
        )
        session.add(row)
        await session.commit()
        return row


@pytest.fixture
async def retention_database() -> object:
    return await make_database(make_settings())


@pytest.mark.asyncio
async def test_shared_reference_and_last_release_make_source_due(
    retention_database: object,
) -> None:
    scope = await _scope(retention_database)
    message = await _message(
        retention_database,
        scope=scope,
        message_id="message-shared",
        expires_at=_NOW - timedelta(seconds=1),
    )
    source_scope = SourceScope(scope[0], scope[1], scope[2])

    async with retention_database.session_factory() as session:  # type: ignore[attr-defined]
        repository = SourceRepository(session)
        promoted = await repository.promote(
            SOURCE_MESSAGE,
            message.row_id,
            "memory-1",
            scope=source_scope,
        )
        await repository.promote(SOURCE_MESSAGE, message.row_id, "memory-2")
        assert promoted.storage_class == "MemorySource"
        await repository.release(SOURCE_MESSAGE, message.row_id, "memory-1")

    async with retention_database.session_factory() as session:  # type: ignore[attr-defined]
        source = await SourceRepository(session).get_source(SOURCE_MESSAGE, message.row_id)
        assert source.storage_class == "MemorySource"
        await SourceRepository(session).release(
            SOURCE_MESSAGE,
            message.row_id,
            "memory-2",
            now=_NOW,
        )

    async with retention_database.session_factory() as session:  # type: ignore[attr-defined]
        source = await SourceRepository(session).get_source(SOURCE_MESSAGE, message.row_id)
        assert source.storage_class == "Transient"
        assert source.expires_at is not None
        assert source.expires_at.replace(tzinfo=UTC) <= _NOW


@pytest.mark.asyncio
async def test_due_message_and_event_purge_content_and_tombstone_outbox(
    retention_database: object,
) -> None:
    scope = await _scope(retention_database)
    message = await _message(
        retention_database,
        scope=scope,
        message_id="message-due",
        expires_at=_NOW - timedelta(seconds=1),
    )
    event = await _event(
        retention_database,
        scope=scope,
        event_id="event-due",
        expires_at=_NOW - timedelta(seconds=1),
    )
    async with retention_database.session_factory() as session:  # type: ignore[attr-defined]
        repository = SourceRepository(session)
        await repository.enqueue(SOURCE_MESSAGE, message.row_id, commit=True)
        await repository.enqueue(SOURCE_EVENT, event.row_id, commit=True)

    result = await RetentionService(retention_database).sweep(now=_NOW)  # type: ignore[arg-type]
    assert result.message_content_purged == 1
    assert result.event_content_purged == 1

    async with retention_database.session_factory() as session:  # type: ignore[attr-defined]
        stored_message = await SourceRepository(session).get_source(
            SOURCE_MESSAGE, message.row_id
        )
        stored_event = await SourceRepository(session).get_source(SOURCE_EVENT, event.row_id)
        assert stored_message.content is None
        assert stored_message.created_at is None
        assert stored_message.speaker is None
        assert stored_message.content_deleted_at is not None
        assert stored_message.content_deleted_at.replace(tzinfo=UTC) == _NOW
        assert stored_message.content_digest == "a" * 64
        assert stored_event.occurred_at is None
        assert stored_event.event_type is None
        assert stored_event.importance is None
        assert stored_event.game_time is None
        assert stored_event.actor_id is None
        assert stored_event.target_ids is None
        rows = tuple((await session.execute(select(SourceOutboxModel))).scalars())
        assert {row.state for row in rows} == {OUTBOX_TOMBSTONE}

        # A tombstoned source is skipped rather than re-claimed after restart.
        assert await SourceRepository(session).claim_next(now=_NOW) is None
        assert await SourceRepository(session).cursor() == max(row.source_seq for row in rows)

    second = await RetentionService(retention_database).sweep(now=_NOW + timedelta(hours=1))  # type: ignore[arg-type]
    assert second.message_content_purged == second.event_content_purged == 0


@pytest.mark.asyncio
async def test_audit_expiry_removes_purged_source_but_not_memory_source(
    retention_database: object,
) -> None:
    scope = await _scope(retention_database)
    expired = await _message(
        retention_database,
        scope=scope,
        message_id="message-audit-expired",
        expires_at=_NOW - timedelta(days=1),
    )
    retained = await _message(
        retention_database,
        scope=scope,
        message_id="message-memory-source",
        expires_at=_NOW - timedelta(days=1),
        sequence=2,
    )
    async with retention_database.session_factory() as session:  # type: ignore[attr-defined]
        expired_row = await session.get(MessageModel, expired.row_id)
        assert expired_row is not None
        expired_row.audit_expires_at = _NOW - timedelta(seconds=1)
        await session.commit()
        await SourceRepository(session).promote(
            SOURCE_MESSAGE,
            retained.row_id,
            "memory-retained",
        )

    result = await RetentionService(retention_database).sweep(now=_NOW)  # type: ignore[arg-type]

    assert result.audit_records_deleted == 1
    async with retention_database.session_factory() as session:  # type: ignore[attr-defined]
        with pytest.raises(SourceNotFoundError):
            await SourceRepository(session).get_source(SOURCE_MESSAGE, expired.row_id)
        memory_source = await SourceRepository(session).get_source(
            SOURCE_MESSAGE, retained.row_id
        )
        assert memory_source.content == "원문 보존 테스트"


@pytest.mark.asyncio
async def test_expired_claim_is_repaired_and_stale_ack_is_rejected(
    retention_database: object,
) -> None:
    scope = await _scope(retention_database)
    message = await _message(
        retention_database,
        scope=scope,
        message_id="message-lease",
        expires_at=_NOW + timedelta(days=1),
    )
    async with retention_database.session_factory() as session:  # type: ignore[attr-defined]
        await SourceRepository(session).enqueue(
            SOURCE_MESSAGE,
            message.row_id,
            commit=True,
        )

    async with retention_database.session_factory() as session:  # type: ignore[attr-defined]
        first = await SourceRepository(session).claim_next(
            now=_NOW,
            lease_seconds=1,
        )
        assert first is not None
        assert first.lease_token

    async with retention_database.session_factory() as session:  # type: ignore[attr-defined]
        second = await SourceRepository(session).claim_next(
            now=_NOW + timedelta(seconds=2),
            lease_seconds=10,
        )
        assert second is not None
        assert second.lease_token != first.lease_token

    async with retention_database.session_factory() as session:  # type: ignore[attr-defined]
        repository = SourceRepository(session)
        assert await repository.acknowledge(first, now=_NOW + timedelta(seconds=3)) is False
        assert await repository.acknowledge(second, now=_NOW + timedelta(seconds=3)) is True
        assert await repository.cursor() == second.source_seq
        row = await session.get(SourceOutboxModel, second.source_seq)
        assert row is not None and row.state == OUTBOX_COMPLETED


@pytest.mark.asyncio
async def test_contiguous_cursor_skips_tombstone_and_completes_next_source(
    retention_database: object,
) -> None:
    scope = await _scope(retention_database)
    first = await _message(
        retention_database,
        scope=scope,
        message_id="message-first",
        expires_at=_NOW + timedelta(days=1),
    )
    second = await _message(
        retention_database,
        scope=scope,
        message_id="message-second",
        expires_at=_NOW + timedelta(days=1),
        sequence=2,
    )
    async with retention_database.session_factory() as session:  # type: ignore[attr-defined]
        repository = SourceRepository(session)
        first_outbox = await repository.enqueue(SOURCE_MESSAGE, first.row_id)
        second_outbox = await repository.enqueue(SOURCE_MESSAGE, second.row_id)
        assert second_outbox.source_seq > first_outbox.source_seq
        await session.commit()
        await repository.mark_tombstone(SOURCE_MESSAGE, first.row_id)
        claim = await repository.claim_next(now=_NOW)
        assert claim is not None and claim.source_id == second.row_id
        assert await repository.acknowledge(claim) is True
        assert await repository.cursor() == second_outbox.source_seq


def _legacy_payload() -> dict[str, object]:
    return {
        "version": 2,
        "memories": [
            {
                "kind": "profile",
                "text": "플레이어는 밤을 싫어한다",
                "importance": 3,
                "created_at": "2026-08-01T00:00:00Z",
            }
        ],
    }


async def _legacy_db_row(database: object, player_key: str) -> None:
    async with database.session_factory() as session:  # type: ignore[attr-defined]
        session.add(
            EpisodicMemoryModel(
                row_id=str(uuid4()),
                player_key=player_key,
                kind="profile",
                text="플레이어는 밤을 싫어한다",
                importance=6,
                source_key=None,
                created_at=datetime(2026, 8, 1, tzinfo=UTC),
                recalled_at=None,
                recall_count=0,
                embedding=None,
                embedding_model=None,
            )
        )
        await session.commit()


@pytest.mark.asyncio
async def test_valid_legacy_file_is_quarantined_then_deleted_after_seven_days(
    retention_database: object,
    tmp_path: Path,
) -> None:
    memory_directory = tmp_path / "memories"
    quarantine_directory = tmp_path / "quarantine"
    memory_directory.mkdir()
    path = memory_directory / ("a" * 64 + ".json")
    path.write_text(json.dumps(_legacy_payload(), ensure_ascii=False), encoding="utf-8")
    await _legacy_db_row(retention_database, path.stem)
    maintenance = LegacyMemoryMaintenance(
        retention_database,  # type: ignore[arg-type]
        memory_directory=memory_directory,
        quarantine_directory=quarantine_directory,
    )

    quarantined, deleted, _reports = await maintenance.run(now=_NOW)
    assert (quarantined, deleted) == (1, 0)
    assert not path.exists()
    files = tuple(quarantine_directory.glob("*.json"))
    assert len(files) == 1

    later = _NOW + timedelta(days=8)
    quarantined, deleted, _reports = await maintenance.run(now=later)
    assert (quarantined, deleted) == (0, 1)
    assert not files[0].exists()
    async with retention_database.session_factory() as session:  # type: ignore[attr-defined]
        report = (
            await session.execute(select(LegacyImportReportModel))
        ).scalar_one()
        assert report.status == "deleted"
        assert report.error_code is None


@pytest.mark.asyncio
async def test_malformed_and_mismatched_legacy_files_are_reported_in_place(
    retention_database: object,
    tmp_path: Path,
) -> None:
    memory_directory = tmp_path / "memories"
    quarantine_directory = tmp_path / "quarantine"
    memory_directory.mkdir()
    malformed = memory_directory / "bad.json"
    malformed.write_text("{not-json", encoding="utf-8")
    mismatch = memory_directory / ("b" * 64 + ".json")
    mismatch.write_text(json.dumps(_legacy_payload(), ensure_ascii=False), encoding="utf-8")

    maintenance = LegacyMemoryMaintenance(
        retention_database,  # type: ignore[arg-type]
        memory_directory=memory_directory,
        quarantine_directory=quarantine_directory,
    )
    result = await maintenance.run(now=_NOW)
    assert result == (0, 0, 2)
    assert malformed.exists()
    assert mismatch.exists()
    async with retention_database.session_factory() as session:  # type: ignore[attr-defined]
        reports = tuple((await session.execute(select(LegacyImportReportModel))).scalars())
        assert {report.status for report in reports} == {"corrupt", "mismatch"}


@pytest.mark.asyncio
async def test_tampered_quarantine_file_is_not_deleted(
    retention_database: object,
    tmp_path: Path,
) -> None:
    memory_directory = tmp_path / "memories"
    quarantine_directory = tmp_path / "quarantine"
    memory_directory.mkdir()
    path = memory_directory / ("c" * 64 + ".json")
    path.write_text(json.dumps(_legacy_payload(), ensure_ascii=False), encoding="utf-8")
    await _legacy_db_row(retention_database, path.stem)
    maintenance = LegacyMemoryMaintenance(
        retention_database,  # type: ignore[arg-type]
        memory_directory=memory_directory,
        quarantine_directory=quarantine_directory,
    )
    await maintenance.run(now=_NOW)
    destination = next(quarantine_directory.glob("*.json"))
    destination.write_text("tampered", encoding="utf-8")

    await maintenance.run(now=_NOW + timedelta(days=8))
    assert destination.exists()
    async with retention_database.session_factory() as session:  # type: ignore[attr-defined]
        report = (await session.execute(select(LegacyImportReportModel))).scalar_one()
        assert report.status == "quarantine_modified"


def test_legacy_transcript_quarantine_deletes_only_hash_verified_expired_file(
    tmp_path: Path,
) -> None:
    quarantine = tmp_path / "transcript-quarantine"
    quarantine.mkdir()
    source = quarantine / "legacy.jsonl"
    source.write_text('{"speaker":"player","text":"안녕"}\n', encoding="utf-8")
    digest = hashlib.sha256(source.read_bytes()).hexdigest()
    report_path = quarantine / "import-report-apply.json"
    report_path.write_text(
        json.dumps(
            [
                {
                    "filename": "legacy.jsonl",
                    "sha256": digest,
                    "status": "quarantined",
                    "quarantine_path": str(source),
                    "quarantine_delete_after": (_NOW - timedelta(seconds=1)).isoformat(),
                }
            ]
        ),
        encoding="utf-8",
    )

    assert _sweep_transcript_quarantine(quarantine, _NOW) == (1, 1)
    assert not source.exists()
    report = json.loads(report_path.read_text(encoding="utf-8"))
    assert report[0]["status"] == "deleted"
    assert report[0]["sha256"] == digest
