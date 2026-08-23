"""Deterministic acceptance and review of source-backed memory candidates."""

from __future__ import annotations

import logging
import math
import re
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from difflib import SequenceMatcher
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.connection import Database
from app.db.models import (
    GameEventModel,
    MemoryCandidateModel,
    MemoryModel,
    MemorySourceModel,
    MessageModel,
    SourceOutboxModel,
)
from app.db.source_repository import (
    OUTBOX_CLAIMED,
    ClaimedSource,
    SourceContentDeletedError,
    SourceNotFoundError,
    SourceRepository,
    SourceScope,
)
from app.relationship_service import RelationshipService

MEMORY_TYPES = frozenset(
    {"ProfileFact", "Preference", "Episode", "Promise", "RelationshipEvidence"}
)
_MESSAGE_ONLY_TYPES = frozenset({"ProfileFact", "Preference", "Promise"})
_EVENT_ONLY_TYPES = frozenset({"RelationshipEvidence"})
_WHITESPACE = re.compile(r"\s+")
_SIMILARITY_HOLD_THRESHOLD = 0.6
_EMBEDDING_HOLD_THRESHOLD = 0.85
_AUTO_APPROVE_CONFIDENCE = 0.8
_REVIEW_TTL = timedelta(days=30)
_NEGATIONS = ("아니", "않", "못", "없", "싫", "안 ", "not ", "never ", "no ")
logger = logging.getLogger("aire.backend")


class MemoryCandidateRejectedError(ValueError):
    """The candidate is not grounded by its claimed canonical source."""


class MemoryCandidatePendingError(ValueError):
    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


@dataclass(frozen=True, slots=True)
class MemoryCandidate:
    memory_type: str
    text: str
    importance: int
    scope: SourceScope
    source_type: str
    source_id: str
    pinned: bool = False
    embedding: tuple[float, ...] | None = None
    embedding_model: str | None = None
    confidence: float = 1.0


@dataclass(frozen=True, slots=True)
class MemoryAcceptance:
    memory_id: str
    created: bool


def normalize_memory_text(text: str) -> str:
    return _WHITESPACE.sub(" ", text).strip().casefold()


def render_event_memory(event: GameEventModel) -> str:
    if event.event_type is None or event.actor_id is None:
        raise MemoryCandidateRejectedError("Event source content is unavailable.")
    targets = ", ".join(event.target_ids or ())
    return f"{event.event_type}: {event.actor_id}" if not targets else (
        f"{event.event_type}: {event.actor_id} -> {targets}"
    )


def _cosine(left: list[float] | None, right: tuple[float, ...] | None) -> float:
    if left is None or right is None or len(left) != len(right) or not left:
        return 0.0
    left_length = math.sqrt(sum(value * value for value in left))
    right_length = math.sqrt(sum(value * value for value in right))
    if left_length == 0 or right_length == 0:
        return 0.0
    return sum(a * b for a, b in zip(left, right, strict=True)) / (
        left_length * right_length
    )


def _has_negation(text: str) -> bool:
    lowered = text.casefold()
    return any(token in lowered for token in _NEGATIONS)


class MemoryCandidateService:
    def __init__(self, database: Database) -> None:
        self._database = database

    async def accept(
        self,
        claim: ClaimedSource,
        candidate: MemoryCandidate,
        *,
        now: datetime | None = None,
    ) -> MemoryAcceptance:
        moment = now or datetime.now(UTC)
        self._validate_candidate(candidate)
        if (claim.source_type, claim.source_id) != (candidate.source_type, candidate.source_id):
            raise MemoryCandidateRejectedError("Candidate source does not match its outbox claim.")
        async with self._database.session_factory() as session:
            await self._validate_claim(session, claim, candidate, now=moment)
            source = await self._source(session, candidate)
            normalized = normalize_memory_text(candidate.text)
            self._validate_source(candidate, source, normalized)
            active = await self._active(session, candidate)
            exact = next((item for item in active if item.normalized_text == normalized), None)
            if exact is None:
                reason = self._review_reason(candidate, active)
                if reason is not None:
                    raise MemoryCandidatePendingError(reason)
            accepted = await self._persist_memory(
                session, candidate, source, active=active, now=moment
            )
            await session.commit()
            return accepted

    async def accept_and_acknowledge(
        self,
        claim: ClaimedSource,
        candidate: MemoryCandidate,
        *,
        now: datetime | None = None,
    ) -> MemoryAcceptance | None:
        moment = now or datetime.now(UTC)
        try:
            accepted = await self.accept(claim, candidate, now=moment)
        except MemoryCandidatePendingError as error:
            await self._hold_for_review(claim, candidate, error.reason, now=moment)
            logger.info(
                "memory_candidate_pending",
                extra={"event": "memory_candidate_pending", "count": 1, "reason": error.reason},
            )
            return None
        except MemoryCandidateRejectedError:
            async with self._database.session_factory() as session:
                await SourceRepository(session).acknowledge(claim)
            logger.info(
                "memory_candidate_rejected",
                extra={"event": "memory_candidate_rejected", "count": 1, "reason": "Invalid"},
            )
            return None
        async with self._database.session_factory() as session:
            await SourceRepository(session).acknowledge(claim)
        logger.info(
            "memory_candidate_auto_approved",
            extra={"event": "memory_candidate_auto_approved", "count": 1},
        )
        return accepted

    async def _hold_for_review(
        self,
        claim: ClaimedSource,
        candidate: MemoryCandidate,
        reason: str,
        *,
        now: datetime,
    ) -> None:
        self._validate_candidate(candidate)
        async with self._database.session_factory() as session:
            await self._validate_claim(session, claim, candidate, now=now)
            source = await self._source(session, candidate)
            normalized = normalize_memory_text(candidate.text)
            self._validate_source(candidate, source, normalized)
            existing = await session.scalar(
                select(MemoryCandidateModel).where(
                    MemoryCandidateModel.source_type == candidate.source_type,
                    MemoryCandidateModel.source_id == candidate.source_id,
                )
            )
            if existing is None:
                candidate_id = f"memory-candidate-{uuid4()}"
                existing = MemoryCandidateModel(
                    candidate_id=candidate_id,
                    profile_id=candidate.scope.profile_id,
                    save_slot_row_id=candidate.scope.save_slot_row_id,
                    companion_id=candidate.scope.companion_id,
                    memory_type=candidate.memory_type,
                    text=candidate.text.strip(),
                    normalized_text=normalized,
                    importance=candidate.importance,
                    pinned=candidate.pinned,
                    confidence=candidate.confidence,
                    embedding=None if candidate.embedding is None else list(candidate.embedding),
                    embedding_model=candidate.embedding_model,
                    source_type=candidate.source_type,
                    source_id=candidate.source_id,
                    source_mode=self._source_mode(source),
                    occurred_at=self._occurred_at(source),
                    review_reason=reason,
                    status="PendingReview",
                    created_at=now,
                    expires_at=now + _REVIEW_TTL,
                    decided_at=None,
                    decision_reason=None,
                    approved_memory_id=None,
                )
                session.add(existing)
                await session.flush()
                await SourceRepository(session).promote(
                    candidate.source_type,
                    candidate.source_id,
                    candidate_id,
                    scope=candidate.scope,
                    now=now,
                    commit=False,
                )
            await self._acknowledge_in_session(session, claim, now=now)
            await session.commit()

    @staticmethod
    async def _acknowledge_in_session(
        session: AsyncSession, claim: ClaimedSource, *, now: datetime
    ) -> None:
        row = await session.get(SourceOutboxModel, claim.source_seq)
        if row is None or row.state != OUTBOX_CLAIMED or row.lease_token != claim.lease_token:
            raise MemoryCandidateRejectedError("Outbox claim is missing, stale, or mismatched.")
        row.state = "Completed"
        row.lease_token = None
        row.lease_expires_at = None
        row.completed_at = now

    @staticmethod
    def _validate_candidate(candidate: MemoryCandidate) -> None:
        if candidate.memory_type not in MEMORY_TYPES:
            raise MemoryCandidateRejectedError("Unsupported memory type.")
        if not 1 <= candidate.importance <= 10:
            raise MemoryCandidateRejectedError("Importance must be between 1 and 10.")
        if not 0 <= candidate.confidence <= 1 or not math.isfinite(candidate.confidence):
            raise MemoryCandidateRejectedError("Confidence must be between 0 and 1.")
        if candidate.embedding is None:
            if candidate.embedding_model is not None:
                raise MemoryCandidateRejectedError("Embedding model requires an embedding.")
        elif (
            not candidate.embedding_model
            or not candidate.embedding
            or not all(math.isfinite(value) for value in candidate.embedding)
        ):
            raise MemoryCandidateRejectedError("Embedding and model must be valid together.")
        if not normalize_memory_text(candidate.text):
            raise MemoryCandidateRejectedError("Memory text must not be blank.")

    @staticmethod
    async def _validate_claim(
        session: AsyncSession,
        claim: ClaimedSource,
        candidate: MemoryCandidate,
        *,
        now: datetime,
    ) -> None:
        outbox = await session.get(SourceOutboxModel, claim.source_seq)
        expires_at = outbox.lease_expires_at if outbox is not None else None
        if expires_at is not None and expires_at.tzinfo is None:
            expires_at = expires_at.replace(tzinfo=UTC)
        if (
            outbox is None
            or outbox.state != OUTBOX_CLAIMED
            or outbox.source_type != candidate.source_type
            or outbox.source_id != candidate.source_id
            or outbox.lease_token != claim.lease_token
            or expires_at is None
            or expires_at <= now
        ):
            raise MemoryCandidateRejectedError("Outbox claim is missing, stale, or mismatched.")

    @staticmethod
    async def _source(
        session: AsyncSession, candidate: MemoryCandidate
    ) -> MessageModel | GameEventModel:
        try:
            return await SourceRepository(session).get_source(
                candidate.source_type, candidate.source_id, scope=candidate.scope
            )
        except (SourceNotFoundError, SourceContentDeletedError, PermissionError) as error:
            raise MemoryCandidateRejectedError(str(error)) from error

    @staticmethod
    async def _active(
        session: AsyncSession, candidate: MemoryCandidate
    ) -> tuple[MemoryModel, ...]:
        result = await session.execute(
            select(MemoryModel).where(
                MemoryModel.profile_id == candidate.scope.profile_id,
                MemoryModel.save_slot_row_id == candidate.scope.save_slot_row_id,
                MemoryModel.companion_id == candidate.scope.companion_id,
                MemoryModel.memory_type == candidate.memory_type,
                MemoryModel.status == "Active",
            )
        )
        return tuple(result.scalars())

    @staticmethod
    def _review_reason(
        candidate: MemoryCandidate, active: tuple[MemoryModel, ...]
    ) -> str | None:
        if candidate.confidence < _AUTO_APPROVE_CONFIDENCE:
            return "LowConfidence"
        normalized = normalize_memory_text(candidate.text)
        for item in active:
            string_similarity = SequenceMatcher(None, item.normalized_text, normalized).ratio()
            semantic_similarity = _cosine(item.embedding, candidate.embedding)
            if semantic_similarity >= _EMBEDDING_HOLD_THRESHOLD:
                if _has_negation(item.text) != _has_negation(candidate.text):
                    return "PossibleConflict"
                if item.normalized_text != normalized:
                    return "SemanticSimilarity"
            if string_similarity >= _SIMILARITY_HOLD_THRESHOLD:
                return "TextSimilarity"
        return None

    @classmethod
    async def _persist_memory(
        cls,
        session: AsyncSession,
        candidate: MemoryCandidate,
        source: MessageModel | GameEventModel,
        *,
        active: tuple[MemoryModel, ...],
        now: datetime,
    ) -> MemoryAcceptance:
        normalized = normalize_memory_text(candidate.text)
        memory = next((item for item in active if item.normalized_text == normalized), None)
        created = memory is None
        if memory is None:
            memory = MemoryModel(
                memory_id=f"memory-{uuid4()}",
                profile_id=candidate.scope.profile_id,
                save_slot_row_id=candidate.scope.save_slot_row_id,
                companion_id=candidate.scope.companion_id,
                memory_type=candidate.memory_type,
                text=candidate.text.strip(),
                normalized_text=normalized,
                importance=candidate.importance,
                pinned=candidate.pinned,
                status="Active",
                created_at=now,
                recalled_at=None,
                recall_count=0,
                embedding=None if candidate.embedding is None else list(candidate.embedding),
                embedding_model=candidate.embedding_model,
                archived_at=None,
                archived_reason=None,
            )
            session.add(memory)
            await session.flush()
        elif memory.embedding is None and candidate.embedding is not None:
            memory.embedding = list(candidate.embedding)
            memory.embedding_model = candidate.embedding_model
        await SourceRepository(session).promote(
            candidate.source_type,
            candidate.source_id,
            memory.memory_id,
            scope=candidate.scope,
            now=now,
            commit=False,
        )
        linked = await session.scalar(
            select(MemorySourceModel.row_id).where(
                MemorySourceModel.memory_id == memory.memory_id,
                MemorySourceModel.source_type == candidate.source_type,
                MemorySourceModel.source_id == candidate.source_id,
            )
        )
        if linked is None:
            session.add(
                MemorySourceModel(
                    row_id=str(uuid4()),
                    memory_id=memory.memory_id,
                    source_type=candidate.source_type,
                    source_id=candidate.source_id,
                    source_mode=cls._source_mode(source),
                    occurred_at=cls._occurred_at(source),
                    created_at=now,
                )
            )
        if candidate.memory_type == "Preference":
            await cls._refresh_relationship(session, candidate.scope)
        return MemoryAcceptance(memory.memory_id, created)

    @classmethod
    async def accept_reviewed(
        cls,
        session: AsyncSession,
        candidate: MemoryCandidate,
        *,
        now: datetime,
    ) -> MemoryAcceptance:
        """Promote a user-approved candidate inside the caller's transaction."""

        cls._validate_candidate(candidate)
        source = await cls._source(session, candidate)
        normalized = normalize_memory_text(candidate.text)
        cls._validate_source(candidate, source, normalized)
        active = await cls._active(session, candidate)
        return await cls._persist_memory(
            session, candidate, source, active=active, now=now
        )

    @staticmethod
    async def _refresh_relationship(session: AsyncSession, scope: SourceScope) -> None:
        result = await session.execute(
            select(GameEventModel)
            .where(
                GameEventModel.profile_id == scope.profile_id,
                GameEventModel.save_slot_row_id == scope.save_slot_row_id,
                GameEventModel.companion_id == scope.companion_id,
                GameEventModel.content_deleted_at.is_(None),
            )
            .order_by(GameEventModel.occurred_at, GameEventModel.row_id)
        )
        relationship = RelationshipService(session)
        for event in result.scalars():
            await relationship.observe_event(event)

    @staticmethod
    def _validate_source(
        candidate: MemoryCandidate,
        source: MessageModel | GameEventModel,
        normalized: str,
    ) -> None:
        if candidate.memory_type in _MESSAGE_ONLY_TYPES:
            if not (
                isinstance(source, MessageModel)
                and source.speaker == "player"
                and source.source_mode in {"RealWorld", "GameWorld", "LegacyUnknown"}
            ):
                raise MemoryCandidateRejectedError(
                    "Memory type requires a direct player message."
                )
        elif candidate.memory_type in _EVENT_ONLY_TYPES:
            if not isinstance(source, GameEventModel):
                raise MemoryCandidateRejectedError("Memory type requires a verified GameEvent.")
        elif candidate.memory_type == "Episode":
            if isinstance(source, MessageModel) and source.speaker != "player":
                raise MemoryCandidateRejectedError("Episode Message must be spoken by the player.")
        else:
            raise MemoryCandidateRejectedError("Unsupported memory type.")
        if isinstance(source, MessageModel):
            if source.content is None or normalize_memory_text(source.content) != normalized:
                raise MemoryCandidateRejectedError(
                    "Message candidate must match the direct source text."
                )
        elif normalize_memory_text(render_event_memory(source)) != normalized:
            raise MemoryCandidateRejectedError(
                "Event candidate must match the deterministic event text."
            )

    @staticmethod
    def _source_mode(source: MessageModel | GameEventModel) -> str:
        if isinstance(source, MessageModel):
            if source.source_mode is None:
                raise MemoryCandidateRejectedError("Message source mode is unavailable.")
            return source.source_mode
        return "GameWorld"

    @staticmethod
    def _occurred_at(source: MessageModel | GameEventModel) -> datetime:
        value = source.created_at if isinstance(source, MessageModel) else source.occurred_at
        if value is None:
            raise MemoryCandidateRejectedError("Source timestamp is unavailable.")
        return value if value.tzinfo is not None else value.replace(tzinfo=UTC)


async def expire_pending_candidates(session: AsyncSession, *, now: datetime) -> int:
    result = await session.execute(
        select(MemoryCandidateModel).where(
            MemoryCandidateModel.status == "PendingReview",
            MemoryCandidateModel.expires_at <= now,
        )
    )
    rows = tuple(result.scalars())
    repository = SourceRepository(session)
    for row in rows:
        scope = SourceScope(row.profile_id, row.save_slot_row_id, row.companion_id)
        row.status = "Expired"
        row.decided_at = now
        row.decision_reason = "ReviewExpired"
        await repository.release(
            row.source_type,
            row.source_id,
            row.candidate_id,
            scope=scope,
            now=now,
            commit=False,
        )
        await repository.mark_tombstone(row.source_type, row.source_id, now=now, commit=False)
    return len(rows)
