"""Durable source retention references and memory-extraction outbox.

The canonical Message/Event rows are owned by the chat and event repositories.  This
module deliberately only owns the small bit of lifecycle state which is shared by the
future memory pipeline: references, a leased outbox, and a durable cursor.  Keeping
that state in SQLite means a process restart cannot make an already-claimed source
disappear or make a tombstoned source eligible for extraction again.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, cast
from uuid import uuid4

from sqlalchemy import CursorResult, delete, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import (
    GameEventModel,
    MessageModel,
    SourceCursorModel,
    SourceOutboxModel,
    SourceRetentionReferenceModel,
)

SOURCE_MESSAGE = "Message"
SOURCE_EVENT = "Event"
SUPPORTED_SOURCE_TYPES = frozenset({SOURCE_MESSAGE, SOURCE_EVENT})

OUTBOX_PENDING = "Pending"
OUTBOX_CLAIMED = "Claimed"
OUTBOX_COMPLETED = "Completed"
OUTBOX_TOMBSTONE = "Tombstone"


class SourceNotFoundError(LookupError):
    """The requested source or outbox item does not exist."""


class SourceContentDeletedError(RuntimeError):
    """A tombstoned source must never be promoted or re-enqueued for extraction."""


class SourceScopeMismatchError(PermissionError):
    """A caller attempted to mutate a source outside its authenticated scope."""


@dataclass(frozen=True, slots=True)
class SourceScope:
    profile_id: str
    save_slot_row_id: str
    companion_id: str


@dataclass(frozen=True, slots=True)
class ClaimedSource:
    source_seq: int
    source_type: str
    source_id: str
    lease_token: str
    lease_expires_at: datetime


def _utc(value: datetime | None) -> datetime:
    if value is None:
        return datetime.now(UTC)
    return value if value.tzinfo is not None else value.replace(tzinfo=UTC)


class SourceRepository:
    """Async repository for source reference and outbox state.

    Mutating operations commit by default because the retention worker and the P3
    source consumer normally own a short independent transaction.  Callers that
    need to include the operation in a larger transaction can pass ``commit=False``;
    the repository still flushes the changes so a following query sees them.
    """

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get_source(
        self,
        source_type: str,
        source_id: str,
        *,
        scope: SourceScope | None = None,
    ) -> MessageModel | GameEventModel:
        model = self._model_for(source_type)
        identifier = self._identifier_for(source_type)
        result = await self._session.execute(
            select(model).where(getattr(model, identifier) == source_id)
        )
        source = result.scalar_one_or_none()
        if source is None:
            raise SourceNotFoundError(f"Unknown {source_type} source: {source_id}")
        if scope is not None and not self._matches_scope(source, scope):
            raise SourceScopeMismatchError(f"Source is outside the requested scope: {source_id}")
        return cast(MessageModel | GameEventModel, source)

    async def promote(
        self,
        source_type: str,
        source_id: str,
        reference_id: str,
        *,
        scope: SourceScope | None = None,
        now: datetime | None = None,
        commit: bool = True,
    ) -> MessageModel | GameEventModel:
        """Attach an active memory reference and make the source non-expiring.

        Inserting the same reference again is idempotent.  A tombstone is a terminal
        state for content, so this method intentionally never recreates its payload.
        """

        if not reference_id:
            raise ValueError("reference_id must not be empty")
        source = await self.get_source(source_type, source_id, scope=scope)
        content_deleted = source.content_deleted_at is not None
        message = cast(MessageModel, source) if source_type == SOURCE_MESSAGE else None
        missing_message_content = message is not None and message.content is None
        if content_deleted or missing_message_content:
            raise SourceContentDeletedError(f"Source content is unavailable: {source_id}")

        existing = await self._session.execute(
            select(SourceRetentionReferenceModel).where(
                SourceRetentionReferenceModel.source_type == source_type,
                SourceRetentionReferenceModel.source_id == source_id,
                SourceRetentionReferenceModel.reference_id == reference_id,
            )
        )
        reference = existing.scalar_one_or_none()
        if reference is None:
            self._session.add(
                SourceRetentionReferenceModel(
                    row_id=str(uuid4()),
                    source_type=source_type,
                    source_id=source_id,
                    reference_id=reference_id,
                    created_at=_utc(now),
                )
            )
            await self._session.flush()

        source.storage_class = "MemorySource"
        source.expires_at = None
        source.retention_reason = "MemoryReference"
        if commit:
            await self._session.commit()
        return source

    async def release(
        self,
        source_type: str,
        source_id: str,
        reference_id: str,
        *,
        scope: SourceScope | None = None,
        now: datetime | None = None,
        commit: bool = True,
    ) -> bool:
        """Remove one reference and expire a source when it was the last one."""

        source = await self.get_source(source_type, source_id, scope=scope)
        result = await self._session.execute(
            delete(SourceRetentionReferenceModel).where(
                SourceRetentionReferenceModel.source_type == source_type,
                SourceRetentionReferenceModel.source_id == source_id,
                SourceRetentionReferenceModel.reference_id == reference_id,
            )
        )
        removed = bool(cast(CursorResult[Any], result).rowcount)
        if removed:
            remaining = await self._session.scalar(
                select(SourceRetentionReferenceModel.row_id)
                .where(
                    SourceRetentionReferenceModel.source_type == source_type,
                    SourceRetentionReferenceModel.source_id == source_id,
                )
                .limit(1)
            )
            if remaining is None:
                source.storage_class = "Transient"
                source.expires_at = _utc(now)
                source.retention_reason = "MemoryReferenceReleased"
        if commit:
            await self._session.commit()
        return removed

    async def enqueue(
        self,
        source_type: str,
        source_id: str,
        *,
        now: datetime | None = None,
        state: str = OUTBOX_PENDING,
        commit: bool = False,
    ) -> SourceOutboxModel:
        """Enqueue one source exactly once.

        ``commit=False`` is intentional: Chat/Event persistence must add this row in
        the same transaction as the canonical source.  A duplicate lookup makes
        replay safe without relying on a database-specific upsert syntax.
        """

        self._model_for(source_type)
        existing_result = await self._session.execute(
            select(SourceOutboxModel).where(
                SourceOutboxModel.source_type == source_type,
                SourceOutboxModel.source_id == source_id,
            )
        )
        existing = existing_result.scalar_one_or_none()
        if existing is not None:
            if commit:
                await self._session.commit()
            return existing
        row = SourceOutboxModel(
            source_type=source_type,
            source_id=source_id,
            state=state,
            lease_token=None,
            lease_expires_at=None,
            attempt_count=0,
            created_at=_utc(now),
            completed_at=None,
        )
        self._session.add(row)
        await self._session.flush()
        if commit:
            await self._session.commit()
        return row

    async def mark_tombstone(
        self,
        source_type: str,
        source_id: str,
        *,
        now: datetime | None = None,
        commit: bool = True,
    ) -> bool:
        """Prevent a purged source from being claimed by a later consumer."""

        self._model_for(source_type)
        result = await self._session.execute(
            select(SourceOutboxModel).where(
                SourceOutboxModel.source_type == source_type,
                SourceOutboxModel.source_id == source_id,
            )
        )
        row = result.scalar_one_or_none()
        if row is None:
            row = await self.enqueue(
                source_type,
                source_id,
                now=now,
                state=OUTBOX_TOMBSTONE,
                commit=False,
            )
        else:
            row.state = OUTBOX_TOMBSTONE
            row.lease_token = None
            row.lease_expires_at = None
        await self._advance_all_cursors()
        if commit:
            await self._session.commit()
        return True

    async def repair_expired_claims(
        self,
        *,
        now: datetime | None = None,
        commit: bool = True,
    ) -> int:
        moment = _utc(now)
        result = await self._session.execute(
            update(SourceOutboxModel)
            .where(
                SourceOutboxModel.state == OUTBOX_CLAIMED,
                SourceOutboxModel.lease_expires_at.is_not(None),
                SourceOutboxModel.lease_expires_at <= moment,
            )
            .values(
                state=OUTBOX_PENDING,
                lease_token=None,
                lease_expires_at=None,
            )
        )
        if commit:
            await self._session.commit()
        return int(cast(CursorResult[Any], result).rowcount or 0)

    async def claim_next(
        self,
        *,
        consumer: str = "memory",
        now: datetime | None = None,
        lease_seconds: float = 60.0,
    ) -> ClaimedSource | None:
        if not consumer:
            raise ValueError("consumer must not be empty")
        if lease_seconds <= 0:
            raise ValueError("lease_seconds must be positive")
        moment = _utc(now)
        await self.repair_expired_claims(now=moment, commit=False)
        cursor = await self._get_cursor(consumer, moment)
        while True:
            await self._advance_cursor(cursor)
            result = await self._session.execute(
                select(SourceOutboxModel)
                .where(SourceOutboxModel.source_seq > cursor.last_completed_seq)
                .order_by(SourceOutboxModel.source_seq)
                .limit(1)
            )
            row = result.scalar_one_or_none()
            if row is None:
                cursor.updated_at = moment
                await self._session.commit()
                return None
            if row.state in {OUTBOX_COMPLETED, OUTBOX_TOMBSTONE}:
                cursor.last_completed_seq = row.source_seq
                cursor.updated_at = moment
                continue
            if row.state != OUTBOX_PENDING:
                cursor.updated_at = moment
                await self._session.commit()
                return None
            token = str(uuid4())
            expires = moment + timedelta(seconds=lease_seconds)
            row.state = OUTBOX_CLAIMED
            row.lease_token = token
            row.lease_expires_at = expires
            row.attempt_count += 1
            await self._session.commit()
            return ClaimedSource(
                source_seq=row.source_seq,
                source_type=row.source_type,
                source_id=row.source_id,
                lease_token=token,
                lease_expires_at=expires,
            )

    async def acknowledge(
        self,
        claim: ClaimedSource,
        *,
        consumer: str = "memory",
        now: datetime | None = None,
    ) -> bool:
        moment = _utc(now)
        row = await self._session.get(SourceOutboxModel, claim.source_seq)
        if (
            row is None
            or row.state != OUTBOX_CLAIMED
            or row.lease_token != claim.lease_token
        ):
            await self._session.rollback()
            return False
        row.state = OUTBOX_COMPLETED
        row.lease_token = None
        row.lease_expires_at = None
        row.completed_at = moment
        cursor = await self._get_cursor(consumer, moment)
        await self._advance_cursor(cursor)
        await self._session.commit()
        return True

    async def cursor(self, consumer: str = "memory") -> int:
        row = await self._session.get(SourceCursorModel, consumer)
        return 0 if row is None else row.last_completed_seq

    async def _get_cursor(self, consumer: str, now: datetime) -> SourceCursorModel:
        row = await self._session.get(SourceCursorModel, consumer)
        if row is None:
            row = SourceCursorModel(
                consumer=consumer,
                last_completed_seq=0,
                updated_at=now,
            )
            self._session.add(row)
            await self._session.flush()
        return row

    async def _advance_cursor(self, cursor: SourceCursorModel) -> None:
        while True:
            result = await self._session.execute(
                select(SourceOutboxModel)
                .where(SourceOutboxModel.source_seq > cursor.last_completed_seq)
                .order_by(SourceOutboxModel.source_seq)
                .limit(1)
            )
            row = result.scalar_one_or_none()
            if row is None or row.state not in {OUTBOX_COMPLETED, OUTBOX_TOMBSTONE}:
                return
            cursor.last_completed_seq = row.source_seq

    async def _advance_all_cursors(self) -> None:
        result = await self._session.execute(select(SourceCursorModel))
        for cursor in result.scalars():
            await self._advance_cursor(cursor)

    @staticmethod
    def _model_for(source_type: str) -> Any:
        if source_type not in SUPPORTED_SOURCE_TYPES:
            raise ValueError(f"Unsupported source_type: {source_type}")
        return MessageModel if source_type == SOURCE_MESSAGE else GameEventModel

    @staticmethod
    def _identifier_for(source_type: str) -> str:
        return "row_id"

    @staticmethod
    def _matches_scope(source: object, scope: SourceScope) -> bool:
        return all(
            getattr(source, field, None) == value
            for field, value in (
                ("profile_id", scope.profile_id),
                ("save_slot_row_id", scope.save_slot_row_id),
                ("companion_id", scope.companion_id),
            )
        )
