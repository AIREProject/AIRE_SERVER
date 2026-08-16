"""Scoped retrieval for source-backed long-term memories.

This module only reads memories accepted by the P3 source-validation boundary.  It
does not create, archive, or delete memory rows; those transitions belong to later
tasks so a failed recall can never change what the system believes about a player.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import select, update

from app.db.connection import Database
from app.db.models import GameEventModel, MemoryModel, MemorySourceModel, MessageModel
from app.db.source_repository import SOURCE_EVENT, SOURCE_MESSAGE, SourceScope

MAX_RECALL_BONUS = 5
MAX_PROMPT_MEMORIES = 3
MAX_PROMPT_MEMORY_CHARS = 360
MIN_SEMANTIC_RELEVANCE = 0.75
_HALF_LIFE_DAYS = 30.0
_TYPE_ORDER = {
    "ProfileFact": 0,
    "Preference": 1,
    "Promise": 2,
    "RelationshipEvidence": 3,
    "Episode": 4,
}


@dataclass(frozen=True, slots=True)
class SourceBackedMemory:
    memory_id: str
    text: str
    memory_type: str
    importance: int
    pinned: bool
    created_at: datetime
    recalled_at: datetime | None
    recall_count: int
    source_modes: tuple[str, ...]
    occurred_at: datetime
    embedding: tuple[float, ...] | None
    embedding_model: str | None


@dataclass(frozen=True, slots=True)
class RecalledMemory:
    trace_id: str
    memory_id: str
    text: str


def _tokens(text: str) -> tuple[str, ...]:
    return tuple(token for token in text.casefold().replace(".", " ").split() if len(token) >= 2)


def _normalize_embedding(values: Sequence[float] | None) -> tuple[float, ...] | None:
    if values is None:
        return None
    vector = tuple(float(value) for value in values)
    if not vector or not all(math.isfinite(value) for value in vector):
        return None
    length = math.sqrt(sum(value * value for value in vector))
    if length <= 0.0:
        return None
    return tuple(value / length for value in vector)


def _utc(value: datetime) -> datetime:
    return value if value.tzinfo is not None else value.replace(tzinfo=UTC)


def _semantic_similarity(
    memory: SourceBackedMemory,
    query_embedding: tuple[float, ...] | None,
    embedding_model: str | None,
) -> float | None:
    if (
        query_embedding is None
        or memory.embedding is None
        or not embedding_model
        or memory.embedding_model != embedding_model
        or len(memory.embedding) != len(query_embedding)
    ):
        return None
    return sum(left * right for left, right in zip(memory.embedding, query_embedding, strict=True))


def _decayed_strength(memory: SourceBackedMemory, now: datetime) -> float:
    reference = memory.recalled_at or memory.occurred_at
    age_days = max((now - _utc(reference)).total_seconds(), 0.0) / 86_400.0
    return memory.importance * 0.3 * math.pow(0.5, age_days / _HALF_LIFE_DAYS) + min(
        memory.recall_count, MAX_RECALL_BONUS
    ) * 0.5


class SourceBackedMemoryStore:
    def __init__(self, database: Database) -> None:
        self._database = database

    async def recall(
        self,
        scope: SourceScope | None,
        *,
        query: str,
        source_mode: str | None,
        query_embedding: Sequence[float] | None = None,
        embedding_model: str | None = None,
        limit: int = MAX_PROMPT_MEMORIES,
        now: datetime | None = None,
    ) -> tuple[RecalledMemory, ...]:
        if scope is None or not query.strip() or limit <= 0:
            return ()
        moment = now or datetime.now(UTC)
        memories = await self._active_memories(scope)
        normalized_embedding = _normalize_embedding(query_embedding)
        query_tokens = set(_tokens(query))
        ranked: list[tuple[float, SourceBackedMemory]] = []
        for memory in memories:
            keyword_hits = len(query_tokens.intersection(_tokens(memory.text)))
            semantic = _semantic_similarity(memory, normalized_embedding, embedding_model)
            if keyword_hits == 0 and (semantic is None or semantic < MIN_SEMANTIC_RELEVANCE):
                continue
            mode_bonus = (
                0.25 if source_mode is not None and source_mode in memory.source_modes else 0.0
            )
            score = keyword_hits * 2.0 + (semantic or 0.0) * 2.0 + _decayed_strength(memory, moment)
            score += mode_bonus + (0.5 if memory.pinned else 0.0)
            ranked.append((score, memory))
        ranked.sort(
            key=lambda item: (
                -item[0],
                -_utc(item[1].occurred_at).timestamp(),
                _TYPE_ORDER.get(item[1].memory_type, len(_TYPE_ORDER)),
                item[1].memory_id,
            )
        )
        selected_list: list[SourceBackedMemory] = []
        used_chars = 0
        for _, memory in ranked:
            if len(selected_list) == min(limit, MAX_PROMPT_MEMORIES):
                break
            prompt_text = f"[M{len(selected_list)}] {memory.text}"
            if used_chars + len(prompt_text) > MAX_PROMPT_MEMORY_CHARS:
                continue
            selected_list.append(memory)
            used_chars += len(prompt_text)
        selected = tuple(selected_list)
        if not selected:
            return ()
        await self._record_recall(scope, tuple(item.memory_id for item in selected), moment)
        return tuple(
            RecalledMemory(trace_id=f"M{index}", memory_id=item.memory_id, text=item.text)
            for index, item in enumerate(selected)
        )

    async def archive_candidates(
        self, scope: SourceScope, *, threshold: float, now: datetime | None = None
    ) -> tuple[str, ...]:
        moment = now or datetime.now(UTC)
        return tuple(
            memory.memory_id
            for memory in await self._active_memories(scope)
            if not memory.pinned and _decayed_strength(memory, moment) < threshold
        )

    async def _active_memories(self, scope: SourceScope) -> tuple[SourceBackedMemory, ...]:
        async with self._database.session_factory() as session:
            result = await session.execute(
                select(MemoryModel).where(
                    MemoryModel.profile_id == scope.profile_id,
                    MemoryModel.save_slot_row_id == scope.save_slot_row_id,
                    MemoryModel.companion_id == scope.companion_id,
                    MemoryModel.status == "Active",
                )
            )
            rows = tuple(result.scalars())
            if not rows:
                return ()
            source_result = await session.execute(
                select(MemorySourceModel).where(
                    MemorySourceModel.memory_id.in_(tuple(row.memory_id for row in rows))
                )
            )
            source_rows = tuple(source_result.scalars())
            valid = await self._valid_sources(session, source_rows, scope)
        by_memory: dict[str, list[MemorySourceModel]] = {}
        for source in source_rows:
            if (source.source_type, source.source_id) in valid:
                by_memory.setdefault(source.memory_id, []).append(source)
        return tuple(
            SourceBackedMemory(
                memory_id=row.memory_id,
                text=row.text,
                memory_type=row.memory_type,
                importance=row.importance,
                pinned=row.pinned,
                created_at=_utc(row.created_at),
                recalled_at=None if row.recalled_at is None else _utc(row.recalled_at),
                recall_count=row.recall_count,
                source_modes=tuple(
                    sorted({source.source_mode for source in by_memory[row.memory_id]})
                ),
                occurred_at=max(_utc(source.occurred_at) for source in by_memory[row.memory_id]),
                embedding=_normalize_embedding(row.embedding),
                embedding_model=row.embedding_model,
            )
            for row in rows
            if row.memory_id in by_memory
        )

    @staticmethod
    async def _valid_sources(
        session: object, sources: Sequence[MemorySourceModel], scope: SourceScope
    ) -> set[tuple[str, str]]:
        message_ids = tuple(
            source.source_id for source in sources if source.source_type == SOURCE_MESSAGE
        )
        event_ids = tuple(
            source.source_id for source in sources if source.source_type == SOURCE_EVENT
        )
        valid: set[tuple[str, str]] = set()
        if message_ids:
            result = await session.execute(  # type: ignore[attr-defined]
                select(MessageModel).where(
                    MessageModel.row_id.in_(message_ids),
                    MessageModel.profile_id == scope.profile_id,
                    MessageModel.save_slot_row_id == scope.save_slot_row_id,
                    MessageModel.companion_id == scope.companion_id,
                    MessageModel.content_deleted_at.is_(None),
                    MessageModel.content.is_not(None),
                )
            )
            valid.update((SOURCE_MESSAGE, row.row_id) for row in result.scalars())
        if event_ids:
            result = await session.execute(  # type: ignore[attr-defined]
                select(GameEventModel).where(
                    GameEventModel.row_id.in_(event_ids),
                    GameEventModel.profile_id == scope.profile_id,
                    GameEventModel.save_slot_row_id == scope.save_slot_row_id,
                    GameEventModel.companion_id == scope.companion_id,
                    GameEventModel.content_deleted_at.is_(None),
                )
            )
            valid.update((SOURCE_EVENT, row.row_id) for row in result.scalars())
        return valid

    async def _record_recall(
        self, scope: SourceScope, memory_ids: tuple[str, ...], now: datetime
    ) -> None:
        async with self._database.session_factory() as session:
            await session.execute(
                update(MemoryModel)
                .where(
                    MemoryModel.memory_id.in_(memory_ids),
                    MemoryModel.profile_id == scope.profile_id,
                    MemoryModel.save_slot_row_id == scope.save_slot_row_id,
                    MemoryModel.companion_id == scope.companion_id,
                    MemoryModel.status == "Active",
                )
                .values(recalled_at=now, recall_count=MemoryModel.recall_count + 1)
            )
            await session.commit()
