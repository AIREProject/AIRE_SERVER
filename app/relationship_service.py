"""Deterministic, source-audited relationship state transitions for CAI-P4."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Literal
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.connection import Database
from app.db.models import (
    GameEventModel,
    MemoryModel,
    MemorySourceModel,
    MessageModel,
    RelationshipStateAuditModel,
    RelationshipStateEvidenceModel,
    RelationshipStateModel,
)
from app.db.source_repository import SOURCE_MESSAGE, SourceScope

RelationshipState = Literal["Low", "Growing", "High"]

_LOW: RelationshipState = "Low"
_GROWING: RelationshipState = "Growing"
_HIGH: RelationshipState = "High"
_NIGHT_PERIOD = "night"
_PLAYER_TARGET_ID = "player"
_EVENT_TYPES = frozenset({"Event.Danger.Detected", "Event.Rescue.Completed"})
_EVENT_COOLDOWN = timedelta(hours=24)
_HIGH_WINDOW = timedelta(days=14)


@dataclass(frozen=True, slots=True)
class RelationshipEvaluation:
    state: RelationshipState
    evidence_accepted: int


def _utc(value: datetime) -> datetime:
    return value if value.tzinfo is not None else value.replace(tzinfo=UTC)


def _is_night_fear(text: str) -> bool:
    normalized = text.casefold()
    return "밤" in normalized and "무서" in normalized


class RelationshipPresentationStore:
    """Read-only bridge from persisted state to the dialogue layer."""

    def __init__(self, database: Database) -> None:
        self._database = database

    async def read(self, scope: SourceScope) -> RelationshipState:
        async with self._database.session_factory() as session:
            state = await session.scalar(
                select(RelationshipStateModel.state).where(
                    RelationshipStateModel.profile_id == scope.profile_id,
                    RelationshipStateModel.save_slot_row_id == scope.save_slot_row_id,
                    RelationshipStateModel.companion_id == scope.companion_id,
                )
            )
        return _as_state(state)


class RelationshipService:
    """Admit only canonical, active source pairs and derive the bounded state."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def observe_event(self, event: GameEventModel) -> RelationshipEvaluation:
        scope = SourceScope(event.profile_id, event.save_slot_row_id, event.companion_id)
        if not self._is_qualifying_event(event, scope):
            return RelationshipEvaluation(await self.refresh(scope), 0)
        event_occurred_at = event.occurred_at
        if event_occurred_at is None:
            return RelationshipEvaluation(await self.refresh(scope), 0)

        accepted = 0
        newest_evidence_id: str | None = None
        for memory, source in await self._matching_preferences(scope, event):
            if await self._already_admitted(scope, memory.memory_id, event.row_id):
                continue
            if await self._in_cooldown(scope, memory.memory_id, event):
                continue
            evidence = RelationshipStateEvidenceModel(
                row_id=str(uuid4()),
                profile_id=scope.profile_id,
                save_slot_row_id=scope.save_slot_row_id,
                companion_id=scope.companion_id,
                preference_memory_id=memory.memory_id,
                message_source_id=source.source_id,
                event_source_id=event.row_id,
                event_type=event.event_type or "",
                occurred_at=_utc(event_occurred_at),
                accepted_at=datetime.now(UTC),
            )
            self._session.add(evidence)
            accepted += 1
            newest_evidence_id = evidence.row_id

        state = await self.refresh(
            scope,
            reason="EvidenceAccepted" if accepted else None,
            evidence_row_id=newest_evidence_id,
        )
        return RelationshipEvaluation(state, accepted)

    async def refresh(
        self,
        scope: SourceScope,
        *,
        reason: Literal["EvidenceAccepted", "SourceInvalidated"] | None = None,
        evidence_row_id: str | None = None,
    ) -> RelationshipState:
        valid, invalid = await self._valid_evidence(scope)
        desired = self._derive_state(valid)
        state = await self._state(scope)
        previous = _LOW if state is None else _as_state(state.state)
        if previous == desired:
            return desired

        now = datetime.now(UTC)
        if state is None:
            state = RelationshipStateModel(
                row_id=str(uuid4()),
                profile_id=scope.profile_id,
                save_slot_row_id=scope.save_slot_row_id,
                companion_id=scope.companion_id,
                state=desired,
                created_at=now,
                updated_at=now,
            )
            self._session.add(state)
            await self._session.flush()
        else:
            state.state = desired
            state.updated_at = now

        if reason is None:
            reason = "SourceInvalidated"
        if evidence_row_id is None and invalid:
            evidence_row_id = invalid[-1].row_id
        occurred_at = (
            next(
                (evidence.occurred_at for evidence in valid if evidence.row_id == evidence_row_id),
                now,
            )
            if reason == "EvidenceAccepted"
            else now
        )
        self._session.add(
            RelationshipStateAuditModel(
                row_id=str(uuid4()),
                relationship_state_row_id=state.row_id,
                evidence_row_id=evidence_row_id,
                previous_state=previous,
                next_state=desired,
                reason=reason,
                occurred_at=occurred_at,
                created_at=now,
            )
        )
        return desired

    async def _matching_preferences(
        self, scope: SourceScope, event: GameEventModel
    ) -> tuple[tuple[MemoryModel, MemorySourceModel], ...]:
        result = await self._session.execute(
            select(MemoryModel, MemorySourceModel, MessageModel)
            .join(MemorySourceModel, MemorySourceModel.memory_id == MemoryModel.memory_id)
            .join(MessageModel, MessageModel.row_id == MemorySourceModel.source_id)
            .where(
                MemoryModel.profile_id == scope.profile_id,
                MemoryModel.save_slot_row_id == scope.save_slot_row_id,
                MemoryModel.companion_id == scope.companion_id,
                MemoryModel.memory_type == "Preference",
                MemoryModel.status == "Active",
                MemorySourceModel.source_type == SOURCE_MESSAGE,
                MessageModel.profile_id == scope.profile_id,
                MessageModel.save_slot_row_id == scope.save_slot_row_id,
                MessageModel.companion_id == scope.companion_id,
                MessageModel.speaker == "player",
                MessageModel.source_mode == "RealWorld",
                MessageModel.content.is_not(None),
                MessageModel.content_deleted_at.is_(None),
            )
            .order_by(
                MemoryModel.memory_id,
                MemorySourceModel.occurred_at,
                MemorySourceModel.row_id,
            )
        )
        event_occurred_at = event.occurred_at
        if event_occurred_at is None:
            return ()
        selected: dict[str, tuple[MemoryModel, MemorySourceModel]] = {}
        for memory, source, message in result.all():
            if not _is_night_fear(message.content or ""):
                continue
            if _utc(source.occurred_at) >= _utc(event_occurred_at):
                continue
            selected.setdefault(memory.memory_id, (memory, source))
        return tuple(selected.values())

    async def _already_admitted(
        self, scope: SourceScope, memory_id: str, event_source_id: str
    ) -> bool:
        evidence_id = await self._session.scalar(
            select(RelationshipStateEvidenceModel.row_id).where(
                RelationshipStateEvidenceModel.profile_id == scope.profile_id,
                RelationshipStateEvidenceModel.save_slot_row_id == scope.save_slot_row_id,
                RelationshipStateEvidenceModel.companion_id == scope.companion_id,
                RelationshipStateEvidenceModel.preference_memory_id == memory_id,
                RelationshipStateEvidenceModel.event_source_id == event_source_id,
            )
        )
        return evidence_id is not None

    async def _in_cooldown(self, scope: SourceScope, memory_id: str, event: GameEventModel) -> bool:
        result = await self._session.execute(
            select(RelationshipStateEvidenceModel.occurred_at).where(
                RelationshipStateEvidenceModel.profile_id == scope.profile_id,
                RelationshipStateEvidenceModel.save_slot_row_id == scope.save_slot_row_id,
                RelationshipStateEvidenceModel.companion_id == scope.companion_id,
                RelationshipStateEvidenceModel.preference_memory_id == memory_id,
                RelationshipStateEvidenceModel.event_type == event.event_type,
            )
        )
        event_occurred_at = event.occurred_at
        if event_occurred_at is None:
            return True
        occurred_at = _utc(event_occurred_at)
        return any(occurred_at - _utc(previous) < _EVENT_COOLDOWN for previous in result.scalars())

    async def _valid_evidence(
        self, scope: SourceScope
    ) -> tuple[
        tuple[RelationshipStateEvidenceModel, ...],
        tuple[RelationshipStateEvidenceModel, ...],
    ]:
        result = await self._session.execute(
            select(RelationshipStateEvidenceModel)
            .where(
                RelationshipStateEvidenceModel.profile_id == scope.profile_id,
                RelationshipStateEvidenceModel.save_slot_row_id == scope.save_slot_row_id,
                RelationshipStateEvidenceModel.companion_id == scope.companion_id,
            )
            .order_by(
                RelationshipStateEvidenceModel.occurred_at,
                RelationshipStateEvidenceModel.row_id,
            )
        )
        valid: list[RelationshipStateEvidenceModel] = []
        invalid: list[RelationshipStateEvidenceModel] = []
        for evidence in result.scalars():
            if await self._is_valid_evidence(evidence, scope):
                valid.append(evidence)
            else:
                invalid.append(evidence)
        return tuple(valid), tuple(invalid)

    async def _is_valid_evidence(
        self, evidence: RelationshipStateEvidenceModel, scope: SourceScope
    ) -> bool:
        memory = await self._session.get(MemoryModel, evidence.preference_memory_id)
        message = await self._session.get(MessageModel, evidence.message_source_id)
        event = await self._session.get(GameEventModel, evidence.event_source_id)
        if (
            memory is None
            or message is None
            or event is None
            or memory.profile_id != scope.profile_id
            or memory.save_slot_row_id != scope.save_slot_row_id
            or memory.companion_id != scope.companion_id
            or memory.memory_type != "Preference"
            or memory.status != "Active"
            or message.profile_id != scope.profile_id
            or message.save_slot_row_id != scope.save_slot_row_id
            or message.companion_id != scope.companion_id
            or message.speaker != "player"
            or message.source_mode != "RealWorld"
            or message.content is None
            or message.content_deleted_at is not None
            or not _is_night_fear(message.content)
            or event.profile_id != scope.profile_id
            or event.save_slot_row_id != scope.save_slot_row_id
            or event.companion_id != scope.companion_id
            or event.content_deleted_at is not None
            or not self._is_qualifying_event(event, scope)
            or event.event_type != evidence.event_type
            or event.occurred_at is None
        ):
            return False
        link_id = await self._session.scalar(
            select(MemorySourceModel.row_id).where(
                MemorySourceModel.memory_id == memory.memory_id,
                MemorySourceModel.source_type == SOURCE_MESSAGE,
                MemorySourceModel.source_id == message.row_id,
            )
        )
        return link_id is not None and _utc(message.created_at or evidence.occurred_at) < _utc(
            event.occurred_at
        )

    async def _state(self, scope: SourceScope) -> RelationshipStateModel | None:
        result = await self._session.execute(
            select(RelationshipStateModel).where(
                RelationshipStateModel.profile_id == scope.profile_id,
                RelationshipStateModel.save_slot_row_id == scope.save_slot_row_id,
                RelationshipStateModel.companion_id == scope.companion_id,
            )
        )
        return result.scalar_one_or_none()

    @staticmethod
    def _is_qualifying_event(event: GameEventModel, scope: SourceScope) -> bool:
        game_time = event.game_time
        period = game_time.get("period") if isinstance(game_time, dict) else None
        return (
            event.event_type in _EVENT_TYPES
            and event.occurred_at is not None
            and event.content_deleted_at is None
            and event.actor_id == scope.companion_id
            and _PLAYER_TARGET_ID in (event.target_ids or ())
            and isinstance(game_time, dict)
            and game_time.get("source") == "GameWorld"
            and isinstance(period, str)
            and period.casefold() == _NIGHT_PERIOD
        )

    @staticmethod
    def _derive_state(evidence: tuple[RelationshipStateEvidenceModel, ...]) -> RelationshipState:
        if not evidence:
            return _LOW
        by_preference: dict[str, list[RelationshipStateEvidenceModel]] = {}
        for item in evidence:
            by_preference.setdefault(item.preference_memory_id, []).append(item)
        for items in by_preference.values():
            ordered = sorted(items, key=lambda item: (_utc(item.occurred_at), item.row_id))
            for index, earlier in enumerate(ordered):
                for later in ordered[index + 1 :]:
                    elapsed = _utc(later.occurred_at) - _utc(earlier.occurred_at)
                    if elapsed > _HIGH_WINDOW:
                        break
                    if (
                        earlier.event_type != later.event_type
                        and _EVENT_COOLDOWN <= elapsed <= _HIGH_WINDOW
                    ):
                        return _HIGH
        return _GROWING


def _as_state(value: str | None) -> RelationshipState:
    if value == _GROWING:
        return _GROWING
    if value == _HIGH:
        return _HIGH
    return _LOW
