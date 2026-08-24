"""Scoped retrieval for source-backed long-term memories.

This module only reads memories accepted by the P3 source-validation boundary.  It
does not create, archive, or delete memory rows; those transitions belong to later
tasks so a failed recall can never change what the system believes about a player.
"""

from __future__ import annotations

import math
import re
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy import select, update

from app.db.connection import Database
from app.db.models import (
    GameEventModel,
    MemoryCorrectionModel,
    MemoryModel,
    MemorySourceModel,
    MessageModel,
)
from app.db.source_repository import SOURCE_EVENT, SOURCE_MESSAGE, SourceScope

MAX_RECALL_BONUS = 5
MAX_PROMPT_MEMORIES = 3
MAX_PROMPT_MEMORY_CHARS = 360
MIN_SEMANTIC_RELEVANCE = 0.75
_HALF_LIFE_DAYS = 30.0
_CONTEXT_COOLDOWN = timedelta(minutes=10)
_TYPE_ORDER = {
    "ProfileFact": 0,
    "Preference": 1,
    "Promise": 2,
    "RelationshipEvidence": 3,
    "Episode": 4,
}
_PUNCTUATION_PATTERN = re.compile(r"[^\w\s]", flags=re.UNICODE)
_PARTICLE_SUFFIX = re.compile(
    r"(?:이랑|하고|에게|에서|처럼|보다|부터|까지|마다|라도|으로|한테|"
    r"을|를|은|는|이|가|도|만|와|과|랑|로|의|에)$"
)
_TOKEN_STOPWORDS = frozenset({"나는", "내가", "나를", "마코", "그리고", "하지만"})
_TOKEN_STEM_STOPWORDS = frozenset({"나", "내", "너"})
_QUERY_TERM_EXPANSIONS: tuple[tuple[re.Pattern[str], tuple[str, ...]], ...] = (
    # 한국어에서 두 단어를 앞글자로 줄인 합성어는 각 기억과 직접 매칭되지
    # 않을 수 있다. 질문 문장을 하드코딩하지 않고 검색어 의미만 확장한다.
    (re.compile(r"출퇴근"), ("출근", "퇴근")),
)
_EXPLICIT_MEMORY_TYPE_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("Promise", re.compile(r"(?:약속|하기로\s*한|하겠다고\s*한)")),
    ("Preference", re.compile(r"(?:취향|선호|좋아하는|싫어하는)")),
    ("ProfileFact", re.compile(r"(?:내\s*(?:이름|정보|프로필)|나에\s*대해)")),
    ("Episode", re.compile(r"(?:전에\s*(?:있었던|겪었던)|지난\s*일|추억)")),
)


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
    memory_type: str
    source_modes: tuple[str, ...]
    occurred_at: datetime
    priority: str
    required: bool


def render_prompt_memory(memory: RecalledMemory) -> str:
    modes = ",".join(memory.source_modes)
    return (
        f"[{memory.trace_id}] type={memory.memory_type}; source={modes}; "
        f"occurred_at={memory.occurred_at.isoformat()}; priority={memory.priority}; "
        f"{memory.text}"
    )


def _prompt_memory_length(index: int, memory: SourceBackedMemory) -> int:
    modes = ",".join(memory.source_modes)
    priority = "High" if memory.pinned or memory.importance >= 8 else "Normal"
    return len(
        f"[M{index}] type={memory.memory_type}; source={modes}; "
        f"occurred_at={memory.occurred_at.isoformat()}; priority={priority}; {memory.text}"
    )


def _tokens(text: str) -> tuple[str, ...]:
    normalized = " ".join(_PUNCTUATION_PATTERN.sub(" ", text.casefold()).split())
    tokens: list[str] = []
    for token in normalized.split():
        stem = _PARTICLE_SUFFIX.sub("", token)
        if not stem or token in _TOKEN_STOPWORDS or stem in _TOKEN_STEM_STOPWORDS or stem in tokens:
            continue
        tokens.append(stem)
    for pattern, expansions in _QUERY_TERM_EXPANSIONS:
        if pattern.search(normalized) is not None:
            tokens.extend(expansion for expansion in expansions if expansion not in tokens)
    return tuple(tokens)


def _lexical_hits(query_tokens: set[str], memory_text: str) -> int:
    """한국어 붙여쓰기까지 포함해 질의와 기억의 어휘 겹침을 센다."""

    memory_tokens = set(_tokens(memory_text))
    return sum(
        1
        for query_token in query_tokens
        if any(
            query_token == memory_token
            or (len(query_token) >= 2 and query_token in memory_token)
            or (len(memory_token) >= 2 and memory_token in query_token)
            for memory_token in memory_tokens
        )
    )


def _explicit_memory_types(query: str) -> frozenset[str]:
    """사용자가 기억 종류를 직접 지칭한 경우에만 어휘 불일치를 보완한다."""

    return frozenset(
        memory_type
        for memory_type, pattern in _EXPLICIT_MEMORY_TYPE_PATTERNS
        if pattern.search(query) is not None
    )


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
    importance = memory.importance * 0.5
    if not memory.pinned:
        importance *= math.pow(0.5, age_days / _HALF_LIFE_DAYS)
    return importance + min(memory.recall_count, MAX_RECALL_BONUS) * 0.5


class SourceBackedMemoryStore:
    def __init__(self, database: Database) -> None:
        self._database = database

    async def recall(
        self,
        scope: SourceScope | None,
        *,
        query: str,
        context_query: str = "",
        direct_recall: bool = False,
        source_mode: str | None,
        query_embedding: Sequence[float] | None = None,
        embedding_model: str | None = None,
        limit: int = MAX_PROMPT_MEMORIES,
        now: datetime | None = None,
    ) -> tuple[RecalledMemory, ...]:
        if scope is None or (not query.strip() and not context_query.strip()) or limit <= 0:
            return ()
        moment = now or datetime.now(UTC)
        memories = await self._active_memories(scope)
        normalized_embedding = _normalize_embedding(query_embedding)
        query_tokens = set(_tokens(query))
        context_tokens = set(_tokens(context_query))
        explicit_types = _explicit_memory_types(query)
        direct_recall = direct_recall or bool(explicit_types)
        ranked: list[tuple[float, SourceBackedMemory, bool]] = []
        for memory in memories:
            keyword_hits = _lexical_hits(query_tokens, memory.text)
            context_hits = _lexical_hits(context_tokens, memory.text)
            semantic = _semantic_similarity(memory, normalized_embedding, embedding_model)
            type_match = memory.memory_type in explicit_types
            # LLM은 주어진 후보를 자연스럽게 사용할지 판단하지만, 관련 없는 기억을 Prompt에
            # 넣어 추측의 재료로 만들지는 않는다. 직접 어휘, 검증된 embedding, 명시적인 기억
            # 종류 중 하나가 맞아야 후보가 된다.
            user_relevant = bool(keyword_hits or type_match) or (
                semantic is not None and semantic >= MIN_SEMANTIC_RELEVANCE
            )
            context_only = not user_relevant and context_hits > 0
            if not user_relevant and not context_only:
                continue
            if context_only and not (memory.importance >= 6 or memory.pinned):
                continue
            if (
                context_only
                and memory.recalled_at is not None
                and moment - _utc(memory.recalled_at) < _CONTEXT_COOLDOWN
            ):
                continue
            if (
                not keyword_hits
                and not type_match
                and not context_only
                and (semantic is None or semantic < MIN_SEMANTIC_RELEVANCE)
            ):
                continue
            semantic_score = (
                semantic if semantic is not None and semantic >= MIN_SEMANTIC_RELEVANCE else 0.0
            )
            mode_bonus = (
                0.25 if source_mode is not None and source_mode in memory.source_modes else 0.0
            )
            score = (
                keyword_hits * 10.0
                + semantic_score * 5.0
                + (8.0 if type_match else 0.0)
                + (memory.importance * 0.5 if direct_recall else _decayed_strength(memory, moment))
            )
            score += context_hits * 8.0 + mode_bonus + (1.5 if memory.pinned else 0.0)
            ranked.append((score, memory, context_only))
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
        selected_required: list[bool] = []
        for _, memory, context_only in ranked:
            if len(selected_list) == min(limit, MAX_PROMPT_MEMORIES):
                break
            prompt_length = _prompt_memory_length(len(selected_list), memory)
            if used_chars + prompt_length > MAX_PROMPT_MEMORY_CHARS:
                continue
            selected_list.append(memory)
            selected_required.append(direct_recall or (context_only and not selected_list[:-1]))
            used_chars += prompt_length
        selected = tuple(selected_list)
        if not selected:
            return ()
        return tuple(
            RecalledMemory(
                trace_id=f"M{index}",
                memory_id=item.memory_id,
                text=item.text,
                memory_type=item.memory_type,
                source_modes=item.source_modes,
                occurred_at=item.occurred_at,
                priority="High" if item.pinned or item.importance >= 8 else "Normal",
                required=selected_required[index] and index == 0,
            )
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
            correction_result = await session.execute(
                select(MemoryCorrectionModel)
                .where(MemoryCorrectionModel.memory_id.in_(tuple(row.memory_id for row in rows)))
                .order_by(MemoryCorrectionModel.created_at, MemoryCorrectionModel.row_id)
            )
            corrections = {row.memory_id: row.corrected_text for row in correction_result.scalars()}
            valid = await self._valid_sources(session, source_rows, scope)
        by_memory: dict[str, list[MemorySourceModel]] = {}
        for source in source_rows:
            if (source.source_type, source.source_id) in valid:
                by_memory.setdefault(source.memory_id, []).append(source)
        return tuple(
            SourceBackedMemory(
                memory_id=row.memory_id,
                text=corrections.get(row.memory_id, row.text),
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

    async def record_used(
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
