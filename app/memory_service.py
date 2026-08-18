"""Authenticated user control for source-backed memories."""

from __future__ import annotations

import builtins
from datetime import UTC, datetime
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import MemoryCorrectionModel, MemoryModel, MemorySourceModel, SaveSlotModel
from app.db.source_repository import SourceRepository, SourceScope
from app.errors import MemoryNotFoundError
from app.identity import AuthenticatedDevice
from app.models import MemorySourceView, MemoryView, SearchMemoriesRequest, UpdateMemoryRequest
from app.relationship_service import RelationshipService


class MemoryService:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def list(
        self, identity: AuthenticatedDevice, *, save_slot_id: str, companion_id: str
    ) -> list[MemoryView]:
        slot = await self._slot(identity.profile_id, save_slot_id)
        if slot is None:
            return []
        rows = await self._active(scope=SourceScope(identity.profile_id, slot.row_id, companion_id))
        corrections = await self._latest_corrections(tuple(row.memory_id for row in rows))
        sources = await self._sources(tuple(row.memory_id for row in rows))
        return [
            self._view(row, save_slot_id, corrections.get(row.memory_id), sources[row.memory_id])
            for row in rows
        ]

    async def update(
        self,
        identity: AuthenticatedDevice,
        memory_id: str,
        request: UpdateMemoryRequest,
    ) -> MemoryView:
        memory, save_slot_id = await self._owned_active(identity, memory_id)
        now = datetime.now(UTC)
        if request.importance is not None:
            memory.importance = request.importance
        if request.pinned is not None:
            memory.pinned = request.pinned
        correction: MemoryCorrectionModel | None = None
        if request.corrected_text is not None:
            correction = MemoryCorrectionModel(
                row_id=str(uuid4()),
                memory_id=memory.memory_id,
                corrected_text=request.corrected_text.strip(),
                reason=request.correction_reason or "",
                created_at=now,
            )
            self._session.add(correction)
        await self._session.commit()
        if correction is None:
            correction = (await self._latest_corrections((memory.memory_id,))).get(
                memory.memory_id
            )
        sources = await self._sources((memory.memory_id,))
        return self._view(memory, save_slot_id, correction, sources[memory.memory_id])

    async def get(self, identity: AuthenticatedDevice, memory_id: str) -> MemoryView:
        memory, save_slot_id = await self._owned_active(identity, memory_id)
        correction = (await self._latest_corrections((memory.memory_id,))).get(memory.memory_id)
        sources = await self._sources((memory.memory_id,))
        return self._view(memory, save_slot_id, correction, sources[memory.memory_id])

    async def search(
        self, identity: AuthenticatedDevice, request: SearchMemoriesRequest
    ) -> builtins.list[MemoryView]:
        slot = await self._slot(identity.profile_id, request.save_slot_id)
        if slot is None:
            return []
        rows = await self._active(
            scope=SourceScope(identity.profile_id, slot.row_id, request.companion_id)
        )
        corrections = await self._latest_corrections(tuple(row.memory_id for row in rows))
        sources = await self._sources(tuple(row.memory_id for row in rows))
        query = request.query.strip().casefold()
        query_tokens = tuple(token for token in query.split() if token)
        ranked: list[tuple[int, MemoryModel, MemoryCorrectionModel | None]] = []
        for row in rows:
            correction = corrections.get(row.memory_id)
            text = row.text if correction is None else correction.corrected_text
            normalized = text.casefold()
            score = 2 if query in normalized else 0
            score += sum(token in normalized for token in query_tokens)
            if score:
                ranked.append((score, row, correction))
        ranked.sort(key=lambda item: (-item[0], item[1].created_at, item[1].memory_id))
        return [
            self._view(row, request.save_slot_id, correction, sources[row.memory_id])
            for _, row, correction in ranked[: request.limit]
        ]

    async def delete(self, identity: AuthenticatedDevice, memory_id: str, *, reason: str) -> bool:
        memory, _ = await self._owned_active(identity, memory_id)
        await self._archive(memory, reason=reason)
        await self._session.commit()
        return True

    async def reset(
        self,
        identity: AuthenticatedDevice,
        *,
        save_slot_id: str,
        companion_id: str,
        reason: str,
    ) -> int:
        slot = await self._slot(identity.profile_id, save_slot_id)
        if slot is None:
            return 0
        rows = await self._active(scope=SourceScope(identity.profile_id, slot.row_id, companion_id))
        for memory in rows:
            await self._archive(memory, reason=reason)
        await self._session.commit()
        return len(rows)

    async def _archive(self, memory: MemoryModel, *, reason: str) -> None:
        now = datetime.now(UTC)
        scope = SourceScope(memory.profile_id, memory.save_slot_row_id, memory.companion_id)
        sources = tuple(
            (
                await self._session.execute(
                    select(MemorySourceModel).where(MemorySourceModel.memory_id == memory.memory_id)
                )
            ).scalars()
        )
        repository = SourceRepository(self._session)
        memory.status = "Archived"
        memory.archived_at = now
        memory.archived_reason = reason
        for source in sources:
            await repository.release(
                source.source_type,
                source.source_id,
                memory.memory_id,
                scope=scope,
                now=now,
                commit=False,
            )
            # The outbox is an extraction cursor, not the source payload.  Keeping a
            # tombstone even for a shared source preserves current retrieval while
            # preventing a deleted user's fact from being distilled again.
            await repository.mark_tombstone(
                source.source_type, source.source_id, now=now, commit=False
            )
        await RelationshipService(self._session).refresh(scope, reason="SourceInvalidated")

    async def _owned_active(
        self, identity: AuthenticatedDevice, memory_id: str
    ) -> tuple[MemoryModel, str]:
        result = await self._session.execute(
            select(MemoryModel, SaveSlotModel.save_slot_id)
            .join(SaveSlotModel, SaveSlotModel.row_id == MemoryModel.save_slot_row_id)
            .where(
                MemoryModel.memory_id == memory_id,
                MemoryModel.profile_id == identity.profile_id,
                MemoryModel.status == "Active",
            )
        )
        row = result.one_or_none()
        if row is None:
            raise MemoryNotFoundError("Memory is not available in this authenticated scope.")
        return row[0], row[1]

    async def _slot(self, profile_id: str, save_slot_id: str) -> SaveSlotModel | None:
        result = await self._session.execute(
            select(SaveSlotModel).where(
                SaveSlotModel.profile_id == profile_id,
                SaveSlotModel.save_slot_id == save_slot_id,
            )
        )
        return result.scalar_one_or_none()

    async def _active(self, *, scope: SourceScope) -> tuple[MemoryModel, ...]:
        result = await self._session.execute(
            select(MemoryModel)
            .where(
                MemoryModel.profile_id == scope.profile_id,
                MemoryModel.save_slot_row_id == scope.save_slot_row_id,
                MemoryModel.companion_id == scope.companion_id,
                MemoryModel.status == "Active",
            )
            .order_by(MemoryModel.created_at, MemoryModel.memory_id)
        )
        return tuple(result.scalars())

    async def _latest_corrections(
        self, memory_ids: tuple[str, ...]
    ) -> dict[str, MemoryCorrectionModel]:
        if not memory_ids:
            return {}
        result = await self._session.execute(
            select(MemoryCorrectionModel)
            .where(MemoryCorrectionModel.memory_id.in_(memory_ids))
            .order_by(MemoryCorrectionModel.created_at, MemoryCorrectionModel.row_id)
        )
        return {row.memory_id: row for row in result.scalars()}

    async def _sources(
        self, memory_ids: tuple[str, ...]
    ) -> dict[str, builtins.list[MemorySourceView]]:
        grouped: dict[str, builtins.list[MemorySourceView]] = {
            memory_id: [] for memory_id in memory_ids
        }
        if not memory_ids:
            return grouped
        result = await self._session.execute(
            select(MemorySourceModel)
            .where(MemorySourceModel.memory_id.in_(memory_ids))
            .order_by(MemorySourceModel.occurred_at, MemorySourceModel.row_id)
        )
        for source in result.scalars():
            source_mode = source.source_mode
            public_type = "Legacy" if source_mode == "LegacyUnknown" else source.source_type
            grouped[source.memory_id].append(
                MemorySourceView(
                    source_type=public_type,
                    source_mode=source_mode,
                    occurred_at=source.occurred_at,
                )
            )
        return grouped

    @staticmethod
    def _view(
        memory: MemoryModel,
        save_slot_id: str,
        correction: MemoryCorrectionModel | None,
        sources: builtins.list[MemorySourceView],
    ) -> MemoryView:
        return MemoryView(
            memory_id=memory.memory_id,
            save_slot_id=save_slot_id,
            companion_id=memory.companion_id,
            memory_type=memory.memory_type,
            text=memory.text if correction is None else correction.corrected_text,
            importance=memory.importance,
            pinned=memory.pinned,
            corrected=correction is not None,
            created_at=memory.created_at,
            sources=sources,
        )
