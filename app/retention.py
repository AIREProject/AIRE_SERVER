"""Restart-safe retention and legacy memory-file maintenance.

The canonical rows remain the source of truth for new Message/Event data.  This
service only removes content once the policy says it is due, turns the associated
outbox item into a tombstone, and handles the old JSON memory files conservatively.
It never treats a malformed or scope-mismatched legacy file as safe to delete.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import TYPE_CHECKING
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.brain.memory import MAX_IMPORTANCE, read_memory_file
from app.db.connection import Database
from app.db.models import (
    ChatOperationModel,
    CommandCandidateModel,
    CommandResultModel,
    ConversationModel,
    EpisodicMemoryModel,
    GameEventModel,
    LegacyImportReportModel,
    MessageModel,
    SourceOutboxModel,
    SourceRetentionReferenceModel,
)
from app.db.source_repository import SOURCE_EVENT, SOURCE_MESSAGE, SourceRepository

if TYPE_CHECKING:
    from app.brain.transcript import TranscriptStore
    from app.settings import Settings


@dataclass(frozen=True, slots=True)
class RetentionSweepResult:
    message_content_purged: int = 0
    event_content_purged: int = 0
    transcript_files_removed: int = 0
    legacy_files_quarantined: int = 0
    legacy_files_deleted: int = 0
    legacy_reports_updated: int = 0
    audit_records_deleted: int = 0


@dataclass(frozen=True, slots=True)
class _LegacyFileScan:
    file_hash: str
    row_count: int
    status: str
    error_code: str | None
    expected_rows: tuple[tuple[object, ...], ...]


def _utc(value: datetime | None) -> datetime:
    if value is None:
        return datetime.now(UTC)
    return value if value.tzinfo is not None else value.replace(tzinfo=UTC)


def _timestamp(value: datetime | None) -> str | None:
    if value is None:
        return None
    return _utc(value).isoformat()


def _legacy_row(
    *,
    kind: str,
    text: str,
    importance: int,
    source_key: str | None,
    created_at: datetime,
    recalled_at: datetime | None,
    recall_count: int,
    embedding: Sequence[float] | None,
    embedding_model: str | None,
) -> tuple[object, ...]:
    return (
        kind,
        text,
        min(importance * 2, MAX_IMPORTANCE),
        source_key,
        _timestamp(created_at),
        _timestamp(recalled_at),
        max(recall_count, 0),
        None if embedding is None else tuple(float(value) for value in embedding),
        embedding_model,
    )


def _memory_row(row: EpisodicMemoryModel) -> tuple[object, ...]:
    embedding = None if row.embedding is None else tuple(float(v) for v in row.embedding)
    return (
        row.kind,
        row.text,
        row.importance,
        row.source_key,
        _timestamp(row.created_at),
        _timestamp(row.recalled_at),
        row.recall_count,
        embedding,
        row.embedding_model,
    )


def _scan_legacy_file(path: Path) -> _LegacyFileScan:
    """Read and validate a legacy file without silently accepting bad rows."""

    try:
        raw = path.read_bytes()
    except OSError:
        return _LegacyFileScan("0" * 64, 0, "corrupt", "ReadFailed", ())
    file_hash = hashlib.sha256(raw).hexdigest()
    try:
        payload = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return _LegacyFileScan(file_hash, 0, "corrupt", "InvalidJSON", ())
    if not isinstance(payload, dict) or payload.get("version") not in {1, 2}:
        return _LegacyFileScan(file_hash, 0, "corrupt", "UnsupportedVersion", ())
    records = payload.get("memories")
    if not isinstance(records, list) or any(not isinstance(item, dict) for item in records):
        return _LegacyFileScan(file_hash, 0, "corrupt", "InvalidRecords", ())

    memories = read_memory_file(path)
    # read_memory_file intentionally drops malformed candidates for runtime safety;
    # import/quarantine must distinguish that from a valid empty file.
    if len(memories) != len(records):
        return _LegacyFileScan(file_hash, len(records), "corrupt", "InvalidMemoryRecord", ())
    expected = tuple(
        sorted(
            (
                _legacy_row(
                    kind=memory.kind,
                    text=memory.text,
                    importance=memory.importance,
                    source_key=memory.source_key,
                    created_at=memory.created_at,
                    recalled_at=memory.recalled_at,
                    recall_count=memory.recall_count,
                    embedding=memory.embedding,
                    embedding_model=memory.embedding_model,
                )
                for memory in memories
            ),
            key=repr,
        )
    )
    return _LegacyFileScan(file_hash, len(records), "valid", None, expected)


def _sha256_file(path: Path) -> str | None:
    try:
        return hashlib.sha256(path.read_bytes()).hexdigest()
    except OSError:
        return None


def _quarantine_path(directory: Path, file_name: str, file_hash: str) -> Path:
    safe_name = Path(file_name).name
    return directory / f"{safe_name}.{file_hash[:16]}.quarantine.json"


def _move_to_quarantine(path: Path, destination: Path, expected_hash: str) -> bool:
    """Atomically move a verified source, without overwriting another hash."""

    destination.parent.mkdir(parents=True, exist_ok=True)
    if destination.exists():
        destination_hash = _sha256_file(destination)
        if destination_hash != expected_hash:
            return False
        try:
            path.unlink(missing_ok=True)
        except OSError:
            return False
        return True
    try:
        path.replace(destination)
    except OSError:
        return False
    return True


class LegacyMemoryMaintenance:
    """Validate, quarantine, and eventually delete old ``*.json`` memory files."""

    def __init__(
        self,
        database: Database,
        *,
        memory_directory: Path,
        quarantine_directory: Path,
        quarantine_days: int = 7,
    ) -> None:
        self._database = database
        self._memory_directory = memory_directory
        self._quarantine_directory = quarantine_directory
        self._quarantine_days = quarantine_days

    async def run(self, *, now: datetime | None = None) -> tuple[int, int, int]:
        moment = _utc(now)
        reports_updated = await self._reconcile_quarantine(moment)
        quarantined = 0
        try:
            files = sorted(self._memory_directory.glob("*.json"))
        except OSError:
            files = []
        for path in files:
            was_quarantined, report_updated = await self._process_file(path, moment)
            quarantined += int(was_quarantined)
            reports_updated += int(report_updated)
        deleted, changed = await self._delete_expired(moment)
        return quarantined, deleted, reports_updated + changed

    async def _process_file(self, path: Path, now: datetime) -> tuple[bool, bool]:
        scan = await asyncio.to_thread(_scan_legacy_file, path)
        async with self._database.session_factory() as session:
            actual_rows = await self._rows_for_player(session, path.stem)
            matches = scan.status == "valid" and scan.expected_rows == actual_rows
            status = (
                "verified"
                if matches
                else ("mismatch" if scan.status == "valid" else scan.status)
            )
            error_code = None if matches else (scan.error_code or "RowMismatch")
            report = await self._upsert_report(
                session,
                file_name=path.name,
                file_hash=scan.file_hash,
                imported_count=scan.row_count,
                status=status,
                error_code=error_code,
                now=now,
            )
            await session.commit()
        if not matches:
            return False, True

        destination = _quarantine_path(
            self._quarantine_directory,
            report.file_name,
            report.file_hash,
        )
        moved = await asyncio.to_thread(
            _move_to_quarantine,
            path,
            destination,
            report.file_hash,
        )
        if not moved:
            return False, True
        async with self._database.session_factory() as session:
            stored = await session.get(LegacyImportReportModel, report.row_id)
            if stored is not None:
                stored.status = "quarantined"
                stored.quarantined_at = now
                stored.delete_after = now + timedelta(days=self._quarantine_days)
                stored.error_code = None
                stored.updated_at = now
            await session.commit()
        return True, True

    async def _reconcile_quarantine(self, now: datetime) -> int:
        changed = 0
        async with self._database.session_factory() as session:
            result = await session.execute(
                select(LegacyImportReportModel).where(
                    LegacyImportReportModel.status.in_({"verified", "quarantined"})
                )
            )
            reports = tuple(result.scalars())
            for report in reports:
                destination = _quarantine_path(
                    self._quarantine_directory,
                    report.file_name,
                    report.file_hash,
                )
                actual_hash = await asyncio.to_thread(_sha256_file, destination)
                if actual_hash == report.file_hash:
                    if report.status != "quarantined":
                        report.status = "quarantined"
                        report.quarantined_at = report.quarantined_at or now
                        report.delete_after = report.delete_after or (
                            report.quarantined_at + timedelta(days=self._quarantine_days)
                        )
                        report.updated_at = now
                        changed += 1
                elif report.status == "quarantined" and actual_hash is None:
                    report.status = "quarantine_missing"
                    report.error_code = "QuarantineMissing"
                    report.updated_at = now
                    changed += 1
                elif report.status == "quarantined":
                    report.status = "quarantine_modified"
                    report.error_code = "QuarantineHashMismatch"
                    report.updated_at = now
                    changed += 1
            if changed:
                await session.commit()
        return changed

    async def _delete_expired(self, now: datetime) -> tuple[int, int]:
        deleted = 0
        changed = 0
        async with self._database.session_factory() as session:
            result = await session.execute(
                select(LegacyImportReportModel).where(
                    LegacyImportReportModel.status == "quarantined",
                    LegacyImportReportModel.delete_after.is_not(None),
                    LegacyImportReportModel.delete_after <= now,
                )
            )
            reports = tuple(result.scalars())
            for report in reports:
                destination = _quarantine_path(
                    self._quarantine_directory,
                    report.file_name,
                    report.file_hash,
                )
                actual_hash = await asyncio.to_thread(_sha256_file, destination)
                if actual_hash != report.file_hash:
                    report.status = (
                        "quarantine_missing" if actual_hash is None else "quarantine_modified"
                    )
                    report.error_code = (
                        "QuarantineMissing"
                        if actual_hash is None
                        else "QuarantineHashMismatch"
                    )
                    report.updated_at = now
                    changed += 1
                    continue
                try:
                    await asyncio.to_thread(destination.unlink)
                except OSError:
                    continue
                report.status = "deleted"
                report.deleted_at = now
                report.updated_at = now
                deleted += 1
            if deleted or changed:
                await session.commit()
        return deleted, changed

    @staticmethod
    async def _rows_for_player(
        session: AsyncSession,
        player_key: str,
    ) -> tuple[tuple[object, ...], ...]:
        result = await session.execute(
            select(EpisodicMemoryModel)
            .where(EpisodicMemoryModel.player_key == player_key)
            .order_by(EpisodicMemoryModel.row_id)
        )
        return tuple(sorted((_memory_row(row) for row in result.scalars()), key=repr))

    @staticmethod
    async def _upsert_report(
        session: AsyncSession,
        *,
        file_name: str,
        file_hash: str,
        imported_count: int,
        status: str,
        error_code: str | None,
        now: datetime,
    ) -> LegacyImportReportModel:
        result = await session.execute(
            select(LegacyImportReportModel).where(
                LegacyImportReportModel.file_name == file_name,
                LegacyImportReportModel.file_hash == file_hash,
            )
        )
        report = result.scalar_one_or_none()
        if report is None:
            report = LegacyImportReportModel(
                row_id=str(uuid4()),
                file_name=file_name,
                file_hash=file_hash,
                status=status,
                imported_count=imported_count,
                quarantined_at=None,
                delete_after=None,
                deleted_at=None,
                error_code=error_code,
                updated_at=now,
            )
            session.add(report)
        else:
            report.file_name = file_name
            report.imported_count = imported_count
            report.status = status
            report.error_code = error_code
            report.updated_at = now
        await session.flush()
        return report


class RetentionService:
    """Apply source TTLs and optionally clean debug transcript/legacy files."""

    def __init__(
        self,
        database: Database,
        *,
        settings: Settings | None = None,
        transcript: TranscriptStore | None = None,
        user_message_retention_days: int = 7,
        companion_message_retention_days: int = 7,
        game_event_retention_days: int = 7,
        audit_retention_days: int = 30,
        transcript_retention_days: int = 1,
        transcript_enabled: bool = False,
        legacy_memory_directory: Path | None = None,
        legacy_quarantine_directory: Path | None = None,
        legacy_quarantine_days: int = 7,
    ) -> None:
        self._database = database
        if settings is not None:
            user_message_retention_days = settings.user_message_retention_days
            companion_message_retention_days = settings.companion_message_retention_days
            game_event_retention_days = settings.game_event_retention_days
            audit_retention_days = settings.audit_retention_days
            transcript_retention_days = settings.transcript_retention_days
            transcript_enabled = settings.transcript_enabled
            legacy_memory_directory = settings.long_term_memory_dir
            legacy_quarantine_directory = settings.legacy_memory_quarantine_dir
            legacy_quarantine_days = settings.legacy_memory_quarantine_days
        self._message_days = {
            "player": user_message_retention_days,
            "companion": companion_message_retention_days,
        }
        self._event_days = game_event_retention_days
        self._audit_days = audit_retention_days
        self._transcript = transcript
        self._transcript_enabled = transcript_enabled
        self._transcript_days = transcript_retention_days
        self._legacy = (
            None
            if legacy_memory_directory is None or legacy_quarantine_directory is None
            else LegacyMemoryMaintenance(
                database,
                memory_directory=legacy_memory_directory,
                quarantine_directory=legacy_quarantine_directory,
                quarantine_days=legacy_quarantine_days,
            )
        )

    async def sweep(self, *, now: datetime | None = None) -> RetentionSweepResult:
        moment = _utc(now)
        message_count, event_count = await self._purge_canonical(moment)
        audit_count = await self._purge_expired_audit(moment)
        transcript_count = 0
        if self._transcript_enabled and self._transcript is not None:
            transcript_count = await self._transcript.sweep(
                older_than=moment - timedelta(days=self._transcript_days)
            )
        quarantined = deleted = reports = 0
        if self._legacy is not None:
            quarantined, deleted, reports = await self._legacy.run(now=moment)
        return RetentionSweepResult(
            message_content_purged=message_count,
            event_content_purged=event_count,
            transcript_files_removed=transcript_count,
            legacy_files_quarantined=quarantined,
            legacy_files_deleted=deleted,
            legacy_reports_updated=reports,
            audit_records_deleted=audit_count,
        )

    async def sweep_once(self, *, now: datetime | None = None) -> RetentionSweepResult:
        return await self.sweep(now=now)

    async def _purge_canonical(self, now: datetime) -> tuple[int, int]:
        message_count = event_count = 0
        async with self._database.session_factory() as session:
            source_repository = SourceRepository(session)
            message_result = await session.execute(
                select(MessageModel).where(
                    MessageModel.storage_class == "Transient",
                    MessageModel.content_deleted_at.is_(None),
                    MessageModel.expires_at.is_not(None),
                    MessageModel.expires_at <= now,
                )
            )
            messages = tuple(message_result.scalars())
            for message in messages:
                if await self._has_reference(session, SOURCE_MESSAGE, message.row_id):
                    continue
                message.content = None
                message.time_context = None
                message.speaker = None
                message.source_mode = None
                message.created_at = None
                message.delivered_at = None
                message.content_deleted_at = now
                message.retention_reason = "TTL"
                await source_repository.mark_tombstone(
                    SOURCE_MESSAGE,
                    message.row_id,
                    now=now,
                    commit=False,
                )
                message_count += 1

            event_result = await session.execute(
                select(GameEventModel).where(
                    GameEventModel.storage_class == "Transient",
                    GameEventModel.content_deleted_at.is_(None),
                    GameEventModel.expires_at.is_not(None),
                    GameEventModel.expires_at <= now,
                )
            )
            events = tuple(event_result.scalars())
            for event in events:
                if await self._has_reference(session, SOURCE_EVENT, event.row_id):
                    continue
                event.occurred_at = None
                event.event_type = None
                event.importance = None
                event.game_time = None
                event.actor_id = None
                event.target_ids = None
                event.response_body = None
                event.received_at = None
                event.content_deleted_at = now
                event.retention_reason = "TTL"
                await source_repository.mark_tombstone(
                    SOURCE_EVENT,
                    event.row_id,
                    now=now,
                    commit=False,
                )
                event_count += 1
            await session.commit()
        return message_count, event_count

    async def _purge_expired_audit(self, now: datetime) -> int:
        deleted = 0
        async with self._database.session_factory() as session:
            for model in (CommandResultModel, ChatOperationModel):
                result = await session.execute(
                    select(model).where(model.audit_expires_at <= now)
                )
                for row in result.scalars():
                    await session.delete(row)
                    deleted += 1

            candidate_result = await session.execute(
                select(CommandCandidateModel).where(
                    CommandCandidateModel.audit_expires_at <= now,
                    ~select(CommandResultModel.row_id)
                    .where(
                        CommandResultModel.candidate_row_id
                        == CommandCandidateModel.row_id
                    )
                    .exists(),
                )
            )
            for candidate in candidate_result.scalars():
                await session.delete(candidate)
                deleted += 1

            expired_source_ids: list[tuple[str, str]] = []
            message_result = await session.execute(
                select(MessageModel).where(
                    MessageModel.storage_class == "Transient",
                    MessageModel.content_deleted_at.is_not(None),
                    MessageModel.audit_expires_at.is_not(None),
                    MessageModel.audit_expires_at <= now,
                )
            )
            for message in message_result.scalars():
                expired_source_ids.append((SOURCE_MESSAGE, message.row_id))
                await session.delete(message)
                deleted += 1

            event_result = await session.execute(
                select(GameEventModel).where(
                    GameEventModel.storage_class == "Transient",
                    GameEventModel.content_deleted_at.is_not(None),
                    GameEventModel.audit_expires_at.is_not(None),
                    GameEventModel.audit_expires_at <= now,
                )
            )
            for event in event_result.scalars():
                expired_source_ids.append((SOURCE_EVENT, event.row_id))
                await session.delete(event)
                deleted += 1

            for source_type, source_id in expired_source_ids:
                outbox = await session.scalar(
                    select(SourceOutboxModel).where(
                        SourceOutboxModel.source_type == source_type,
                        SourceOutboxModel.source_id == source_id,
                    )
                )
                if outbox is not None:
                    await session.delete(outbox)

            conversation_result = await session.execute(
                select(ConversationModel).where(
                    ~select(MessageModel.row_id)
                    .where(MessageModel.conversation_row_id == ConversationModel.row_id)
                    .exists()
                )
            )
            for conversation in conversation_result.scalars():
                await session.delete(conversation)
            await session.commit()
        return deleted

    @staticmethod
    async def _has_reference(
        session: AsyncSession,
        source_type: str,
        source_id: str,
    ) -> bool:
        reference_id = await session.scalar(
            select(SourceRetentionReferenceModel.reference_id)
            .where(
                SourceRetentionReferenceModel.source_type == source_type,
                SourceRetentionReferenceModel.source_id == source_id,
            )
            .limit(1)
        )
        return reference_id is not None
