"""Authenticated review workflow for pending long-term memory candidates."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import MemoryCandidateModel, MemoryCorrectionModel, SaveSlotModel
from app.db.source_repository import SourceRepository, SourceScope
from app.errors import MemoryCandidateNotFoundError, MemoryCandidateTransitionError
from app.identity import AuthenticatedDevice
from app.memory_candidate_service import (
    MemoryCandidate,
    MemoryCandidateRejectedError,
    MemoryCandidateService,
)
from app.models import MemoryCandidateView, ReviewMemoryCandidateRequest


@dataclass(frozen=True, slots=True)
class CandidateDecision:
    candidate_id: str
    decision: str
    memory_id: str | None


class MemoryCandidateReviewService:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def list(
        self, identity: AuthenticatedDevice, *, save_slot_id: str, companion_id: str
    ) -> list[MemoryCandidateView]:
        slot = await self._slot(identity.profile_id, save_slot_id)
        if slot is None:
            return []
        now = datetime.now(UTC)
        result = await self._session.execute(
            select(MemoryCandidateModel)
            .where(
                MemoryCandidateModel.profile_id == identity.profile_id,
                MemoryCandidateModel.save_slot_row_id == slot.row_id,
                MemoryCandidateModel.companion_id == companion_id,
                MemoryCandidateModel.status == "PendingReview",
                MemoryCandidateModel.expires_at > now,
            )
            .order_by(MemoryCandidateModel.created_at, MemoryCandidateModel.candidate_id)
        )
        return [self._view(row, save_slot_id) for row in result.scalars()]

    async def get(
        self,
        identity: AuthenticatedDevice,
        candidate_id: str,
        *,
        save_slot_id: str,
        companion_id: str,
    ) -> MemoryCandidateView:
        row, save_slot_id = await self._owned_pending(
            identity,
            candidate_id,
            save_slot_id=save_slot_id,
            companion_id=companion_id,
        )
        return self._view(row, save_slot_id)

    async def decide(
        self,
        identity: AuthenticatedDevice,
        candidate_id: str,
        request: ReviewMemoryCandidateRequest,
        *,
        save_slot_id: str,
        companion_id: str,
    ) -> CandidateDecision:
        result = await self._session.execute(
            select(MemoryCandidateModel)
            .join(SaveSlotModel, SaveSlotModel.row_id == MemoryCandidateModel.save_slot_row_id)
            .where(
                MemoryCandidateModel.candidate_id == candidate_id,
                MemoryCandidateModel.profile_id == identity.profile_id,
                SaveSlotModel.profile_id == identity.profile_id,
                SaveSlotModel.save_slot_id == save_slot_id,
                MemoryCandidateModel.companion_id == companion_id,
            )
            .with_for_update()
        )
        row = result.scalar_one_or_none()
        if row is None or row.status == "Expired":
            raise MemoryCandidateNotFoundError()
        requested_status = "Approved" if request.decision == "Approve" else "Rejected"
        if row.status != "PendingReview":
            if row.status == requested_status:
                return CandidateDecision(row.candidate_id, request.decision, None)
            raise MemoryCandidateTransitionError()
        now = datetime.now(UTC)
        expires_at = row.expires_at
        if expires_at.tzinfo is None:
            expires_at = expires_at.replace(tzinfo=UTC)
        if expires_at <= now:
            raise MemoryCandidateNotFoundError()
        scope = SourceScope(row.profile_id, row.save_slot_row_id, row.companion_id)
        repository = SourceRepository(self._session)
        if request.decision == "Reject":
            row.status = "Rejected"
            row.decided_at = now
            row.decision_reason = request.reason.strip()
            await repository.release(
                row.source_type,
                row.source_id,
                row.candidate_id,
                scope=scope,
                now=now,
                commit=False,
            )
            await repository.mark_tombstone(
                row.source_type, row.source_id, now=now, commit=False
            )
            await self._session.commit()
            return CandidateDecision(row.candidate_id, "Reject", None)

        candidate = MemoryCandidate(
            memory_type=request.memory_type or row.memory_type,
            text=row.text,
            importance=request.importance or row.importance,
            pinned=row.pinned if request.pinned is None else request.pinned,
            confidence=row.confidence,
            scope=scope,
            source_type=row.source_type,
            source_id=row.source_id,
            embedding=None if row.embedding is None else tuple(row.embedding),
            embedding_model=row.embedding_model,
        )
        try:
            accepted = await MemoryCandidateService.accept_reviewed(
                self._session, candidate, now=now
            )
        except MemoryCandidateRejectedError as error:
            raise MemoryCandidateTransitionError() from error
        if request.corrected_text is not None:
            self._session.add(
                MemoryCorrectionModel(
                    row_id=str(uuid4()),
                    memory_id=accepted.memory_id,
                    corrected_text=request.corrected_text.strip(),
                    reason=request.reason.strip(),
                    created_at=now,
                )
            )
        await repository.release(
            row.source_type,
            row.source_id,
            row.candidate_id,
            scope=scope,
            now=now,
            commit=False,
        )
        row.status = "Approved"
        row.decided_at = now
        row.decision_reason = request.reason.strip()
        row.approved_memory_id = accepted.memory_id
        await self._session.commit()
        return CandidateDecision(row.candidate_id, "Approve", accepted.memory_id)

    async def _owned_pending(
        self,
        identity: AuthenticatedDevice,
        candidate_id: str,
        *,
        save_slot_id: str,
        companion_id: str,
    ) -> tuple[MemoryCandidateModel, str]:
        now = datetime.now(UTC)
        result = await self._session.execute(
            select(MemoryCandidateModel, SaveSlotModel.save_slot_id)
            .join(SaveSlotModel, SaveSlotModel.row_id == MemoryCandidateModel.save_slot_row_id)
            .where(
                MemoryCandidateModel.candidate_id == candidate_id,
                MemoryCandidateModel.profile_id == identity.profile_id,
                SaveSlotModel.profile_id == identity.profile_id,
                SaveSlotModel.save_slot_id == save_slot_id,
                MemoryCandidateModel.companion_id == companion_id,
                MemoryCandidateModel.status == "PendingReview",
                MemoryCandidateModel.expires_at > now,
            )
        )
        row = result.one_or_none()
        if row is None:
            raise MemoryCandidateNotFoundError()
        return row[0], row[1]

    async def _slot(self, profile_id: str, save_slot_id: str) -> SaveSlotModel | None:
        result = await self._session.execute(
            select(SaveSlotModel).where(
                SaveSlotModel.profile_id == profile_id,
                SaveSlotModel.save_slot_id == save_slot_id,
            )
        )
        return result.scalar_one_or_none()

    @staticmethod
    def _view(row: MemoryCandidateModel, save_slot_id: str) -> MemoryCandidateView:
        return MemoryCandidateView(
            candidate_id=row.candidate_id,
            save_slot_id=save_slot_id,
            companion_id=row.companion_id,
            memory_type=row.memory_type,
            text=row.text,
            source_mode=row.source_mode,
            occurred_at=row.occurred_at,
            review_reason=row.review_reason,
            created_at=row.created_at,
        )
