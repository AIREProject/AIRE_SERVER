"""Deterministic acceptance of source-backed long-term memory candidates."""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import uuid4

from sqlalchemy import select

from app.db.connection import Database
from app.db.models import GameEventModel, MemoryModel, MemorySourceModel, MessageModel
from app.db.source_repository import (
    ClaimedSource,
    SourceContentDeletedError,
    SourceNotFoundError,
    SourceRepository,
    SourceScope,
)

MEMORY_TYPES = frozenset(
    {"ProfileFact", "Preference", "Episode", "Promise", "RelationshipEvidence"}
)
_MESSAGE_ONLY_TYPES = frozenset({"ProfileFact", "Preference", "Promise"})
_EVENT_ONLY_TYPES = frozenset({"RelationshipEvidence"})
_WHITESPACE = re.compile(r"\s+")


class MemoryCandidateRejectedError(ValueError):
    """The candidate is not grounded by its claimed canonical source."""


@dataclass(frozen=True, slots=True)
class MemoryCandidate:
    memory_type: str
    text: str
    importance: int
    scope: SourceScope
    source_type: str
    source_id: str
    pinned: bool = False


@dataclass(frozen=True, slots=True)
class MemoryAcceptance:
    memory_id: str
    created: bool


def normalize_memory_text(text: str) -> str:
    return _WHITESPACE.sub(" ", text).strip().casefold()


def render_event_memory(event: GameEventModel) -> str:
    """Render the fixed, source-backed Event wording without LLM interpretation."""

    if event.event_type is None or event.actor_id is None:
        raise MemoryCandidateRejectedError("Event source content is unavailable.")
    targets = ", ".join(event.target_ids or ())
    if not targets:
        return f"{event.event_type}: {event.actor_id}"
    return f"{event.event_type}: {event.actor_id} -> {targets}"


class MemoryCandidateService:
    """Accept candidates only after validating one claimed Message/Event source."""

    def __init__(self, database: Database) -> None:
        self._database = database

    async def accept(self, claim: ClaimedSource, candidate: MemoryCandidate) -> MemoryAcceptance:
        if (claim.source_type, claim.source_id) != (candidate.source_type, candidate.source_id):
            raise MemoryCandidateRejectedError("Candidate source does not match its outbox claim.")
        if candidate.memory_type not in MEMORY_TYPES:
            raise MemoryCandidateRejectedError("Unsupported memory type.")
        if not 1 <= candidate.importance <= 10:
            raise MemoryCandidateRejectedError("Importance must be between 1 and 10.")

        normalized = normalize_memory_text(candidate.text)
        if not normalized:
            raise MemoryCandidateRejectedError("Memory text must not be blank.")

        async with self._database.session_factory() as session:
            sources = SourceRepository(session)
            try:
                source = await sources.get_source(
                    candidate.source_type, candidate.source_id, scope=candidate.scope
                )
            except (SourceNotFoundError, SourceContentDeletedError, PermissionError) as error:
                raise MemoryCandidateRejectedError(str(error)) from error
            self._validate_source(candidate, source, normalized)

            result = await session.execute(
                select(MemoryModel).where(
                    MemoryModel.profile_id == candidate.scope.profile_id,
                    MemoryModel.save_slot_row_id == candidate.scope.save_slot_row_id,
                    MemoryModel.companion_id == candidate.scope.companion_id,
                    MemoryModel.memory_type == candidate.memory_type,
                    MemoryModel.normalized_text == normalized,
                    MemoryModel.status == "Active",
                )
            )
            memory = result.scalar_one_or_none()
            created = memory is None
            now = datetime.now(UTC)
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
                )
                session.add(memory)
                await session.flush()

            await sources.promote(
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
                        source_mode=self._source_mode(source),
                        occurred_at=self._occurred_at(source),
                        created_at=now,
                    )
                )
            await session.commit()
            return MemoryAcceptance(memory.memory_id, created)

    async def accept_and_acknowledge(
        self, claim: ClaimedSource, candidate: MemoryCandidate
    ) -> MemoryAcceptance | None:
        """Acknowledge valid or deliberately rejected input; retry operational failures."""

        try:
            accepted = await self.accept(claim, candidate)
        except MemoryCandidateRejectedError:
            async with self._database.session_factory() as session:
                await SourceRepository(session).acknowledge(claim)
            return None
        async with self._database.session_factory() as session:
            await SourceRepository(session).acknowledge(claim)
        return accepted

    def _validate_source(
        self,
        candidate: MemoryCandidate,
        source: MessageModel | GameEventModel,
        normalized: str,
    ) -> None:
        if candidate.memory_type in _MESSAGE_ONLY_TYPES:
            is_direct_realworld_player = (
                isinstance(source, MessageModel)
                and source.speaker == "player"
                and source.source_mode == "RealWorld"
            )
            if not is_direct_realworld_player:
                raise MemoryCandidateRejectedError(
                    "Memory type requires a direct RealWorld player message."
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
        occurred_at = source.created_at if isinstance(source, MessageModel) else source.occurred_at
        if occurred_at is None:
            raise MemoryCandidateRejectedError("Source timestamp is unavailable.")
        return occurred_at if occurred_at.tzinfo is not None else occurred_at.replace(tzinfo=UTC)
