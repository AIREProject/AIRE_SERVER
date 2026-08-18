"""Single-process leased consumer for canonical memory sources."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import UTC, datetime

from app.db.connection import Database
from app.db.models import GameEventModel, MessageModel
from app.db.source_repository import SOURCE_EVENT, SOURCE_MESSAGE, SourceRepository, SourceScope
from app.memory_candidate_service import (
    MemoryCandidate,
    MemoryCandidateService,
    render_event_memory,
)
from app.service import CompanionService

logger = logging.getLogger("aire.backend")
_RELATIONSHIP_EVENTS = frozenset({"Event.Danger.Detected", "Event.Rescue.Completed"})


@dataclass(frozen=True, slots=True)
class MemoryWorkerStatus:
    running: bool
    last_success_at: datetime | None
    last_error_at: datetime | None


class MemoryWorker:
    """Classify metadata only and bind accepted memories to canonical source text."""

    def __init__(
        self,
        database: Database,
        companion: CompanionService,
        *,
        lease_seconds: float,
        max_attempts: int,
        batch_size: int,
    ) -> None:
        self._database = database
        self._companion = companion
        self._lease_seconds = lease_seconds
        self._max_attempts = max_attempts
        self._batch_size = batch_size
        self._running = False
        self._last_success_at: datetime | None = None
        self._last_error_at: datetime | None = None

    @property
    def status(self) -> MemoryWorkerStatus:
        return MemoryWorkerStatus(self._running, self._last_success_at, self._last_error_at)

    async def drain_once(self) -> int:
        processed = 0
        self._running = True
        try:
            for _ in range(self._batch_size):
                async with self._database.session_factory() as session:
                    repository = SourceRepository(session)
                    claim = await repository.claim_next(lease_seconds=self._lease_seconds)
                    if claim is None:
                        break
                    source = await repository.get_source(claim.source_type, claim.source_id)
                try:
                    candidate = await self._candidate(source, claim.source_type, claim.source_id)
                    if candidate is None:
                        async with self._database.session_factory() as session:
                            await SourceRepository(session).acknowledge(claim)
                    else:
                        await MemoryCandidateService(self._database).accept_and_acknowledge(
                            claim, candidate
                        )
                    processed += 1
                    self._last_success_at = datetime.now(UTC)
                except Exception:
                    self._last_error_at = datetime.now(UTC)
                    if claim.attempt_count >= self._max_attempts:
                        async with self._database.session_factory() as session:
                            await SourceRepository(session).acknowledge(claim)
                        logger.exception(
                            "memory_source_discarded_after_retries",
                            extra={
                                "event": "memory_source_discarded_after_retries",
                                "source_type": claim.source_type,
                                "attempt_count": claim.attempt_count,
                            },
                        )
                        processed += 1
                        continue
                    logger.exception(
                        "memory_source_retry_scheduled",
                        extra={
                            "event": "memory_source_retry_scheduled",
                            "source_type": claim.source_type,
                            "attempt_count": claim.attempt_count,
                        },
                    )
                    break
            return processed
        finally:
            self._running = False

    async def _candidate(
        self, source: MessageModel | GameEventModel, source_type: str, source_id: str
    ) -> MemoryCandidate | None:
        scope = SourceScope(source.profile_id, source.save_slot_row_id, source.companion_id)
        if source_type == SOURCE_MESSAGE:
            message = source
            if (
                not isinstance(message, MessageModel)
                or message.speaker != "player"
                or message.source_mode not in {"RealWorld", "GameWorld", "LegacyUnknown"}
                or message.content is None
            ):
                return None
            classification = await self._companion.classify_memory(message.content)
            if classification.decision == "Reject":
                return None
            embedding, embedding_model = await self._companion.embed_memory_text(message.content)
            return MemoryCandidate(
                memory_type=classification.decision,
                text=message.content,
                importance=classification.importance,
                scope=scope,
                source_type=SOURCE_MESSAGE,
                source_id=source_id,
                embedding=embedding,
                embedding_model=embedding_model,
            )
        if source_type == SOURCE_EVENT:
            event = source
            if (
                not isinstance(event, GameEventModel)
                or event.event_type not in _RELATIONSHIP_EVENTS
            ):
                return None
            importance = 9 if event.event_type == "Event.Rescue.Completed" else 7
            text = render_event_memory(event)
            embedding, embedding_model = await self._companion.embed_memory_text(text)
            return MemoryCandidate(
                memory_type="RelationshipEvidence",
                text=text,
                importance=importance,
                scope=scope,
                source_type=SOURCE_EVENT,
                source_id=source_id,
                embedding=embedding,
                embedding_model=embedding_model,
            )
        return None
