"""장기기억 Protocol을 SQLite로 구현한 어댑터."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Sequence
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from typing import cast

from app.brain.memory import (
    MAX_MEMORIES_PER_PLAYER,
    LongTermMemory,
    LongTermStore,
    MemoryKind,
    build_memory,
    merge,
    rank,
)
from app.db.connection import Database
from app.db.episodic_memory_repository import SqlAlchemyEpisodicMemoryRepository
from app.db.models import EpisodicMemoryModel


class EpisodicMemoryStore(LongTermStore):
    """호출마다 DB 세션을 열어 요청 경로와 배경 루프 모두에서 쓴다."""

    def __init__(self, database: Database, *, embedding_model: str | None = None) -> None:
        self._database = database
        self._embedding_model = embedding_model
        self._locks: dict[str, asyncio.Lock] = {}
        self._lock_users: dict[str, int] = {}

    async def recall(
        self,
        player_key: str,
        *,
        query: str,
        limit: int,
        query_embedding: Sequence[float] | None = None,
        embedding_model: str | None = None,
    ) -> tuple[LongTermMemory, ...]:
        if not player_key or limit <= 0:
            return ()
        async with self._database.session_factory() as session:
            repository = SqlAlchemyEpisodicMemoryRepository(session)
            try:
                rows = await repository.list_for_player(player_key)
                memories = tuple(
                    memory for row in rows if (memory := self._memory(row)) is not None
                )
                picked = rank(
                    memories,
                    query=query,
                    limit=limit,
                    now=datetime.now(UTC),
                    query_embedding=query_embedding,
                    embedding_model=embedding_model or self._embedding_model,
                )
                if not query.strip() or not picked:
                    return picked
                async with self._player_lock(player_key):
                    updated_rows = await repository.record_recall(
                        player_key,
                        tuple(memory.text for memory in picked),
                        recalled_at=datetime.now(UTC),
                    )
                    await repository.commit()
                updated = {
                    row.text: memory
                    for row in updated_rows
                    if (memory := self._memory(row)) is not None
                }
                return tuple(updated.get(memory.text, memory) for memory in picked)
            except Exception:
                await repository.rollback()
                return ()

    async def remember(self, player_key: str, memories: Sequence[LongTermMemory]) -> None:
        if not player_key or not memories:
            return
        async with self._player_lock(player_key):
            async with self._database.session_factory() as session:
                repository = SqlAlchemyEpisodicMemoryRepository(session)
                current_rows = await repository.list_for_player(player_key)
                current = tuple(
                    memory for row in current_rows if (memory := self._memory(row)) is not None
                )
                merged = merge(current, memories)
                if merged == current:
                    return
                await repository.replace_all(player_key, merged)
                await repository.commit()

    async def replace_all(self, player_key: str, memories: Sequence[LongTermMemory]) -> None:
        if not player_key:
            return
        async with self._player_lock(player_key):
            async with self._database.session_factory() as session:
                repository = SqlAlchemyEpisodicMemoryRepository(session)
                replacement = tuple(memories)[:MAX_MEMORIES_PER_PLAYER]
                await repository.replace_all(player_key, replacement)
                await repository.commit()

    @asynccontextmanager
    async def _player_lock(self, player_key: str) -> AsyncIterator[None]:
        """한 프로세스 안의 같은 플레이어 read-modify-write를 직렬화한다."""

        lock = self._locks.get(player_key)
        if lock is None:
            lock = asyncio.Lock()
            self._locks[player_key] = lock
        self._lock_users[player_key] = self._lock_users.get(player_key, 0) + 1
        try:
            async with lock:
                yield
        finally:
            self._lock_users[player_key] -= 1
            if self._lock_users[player_key] == 0:
                del self._lock_users[player_key]
                del self._locks[player_key]

    @staticmethod
    def _memory(row: EpisodicMemoryModel) -> LongTermMemory | None:
        try:
            return build_memory(
                cast(MemoryKind, row.kind),
                row.text,
                importance=row.importance,
                source_key=row.source_key,
                created_at=row.created_at,
                recalled_at=row.recalled_at,
                recall_count=row.recall_count,
                embedding=row.embedding,
                embedding_model=row.embedding_model,
            )
        except (KeyError, TypeError, ValueError):
            return None
