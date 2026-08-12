"""SQLite 장기기억 행을 읽고 쓰는 저장소."""

from collections.abc import Sequence
from datetime import datetime
from uuid import uuid4

from sqlalchemy import delete, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.brain.memory import LongTermMemory
from app.db.models import EpisodicMemoryModel


class SqlAlchemyEpisodicMemoryRepository:
    """한 세션 안에서 장기기억 행을 조작한다."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def list_for_player(self, player_key: str) -> tuple[EpisodicMemoryModel, ...]:
        result = await self._session.execute(
            select(EpisodicMemoryModel)
            .where(EpisodicMemoryModel.player_key == player_key)
            .order_by(EpisodicMemoryModel.created_at, EpisodicMemoryModel.row_id)
        )
        return tuple(result.scalars())

    async def replace_all(self, player_key: str, memories: Sequence[LongTermMemory]) -> None:
        await self._session.execute(
            delete(EpisodicMemoryModel).where(EpisodicMemoryModel.player_key == player_key)
        )
        self._session.add_all(
            [self._model(player_key, memory) for memory in memories]
        )

    async def record_recall(
        self,
        player_key: str,
        texts: Sequence[str],
        *,
        recalled_at: datetime,
    ) -> tuple[EpisodicMemoryModel, ...]:
        if not texts:
            return ()
        wanted = set(texts)
        await self._session.execute(
            update(EpisodicMemoryModel)
            .where(
                EpisodicMemoryModel.player_key == player_key,
                EpisodicMemoryModel.text.in_(wanted),
            )
            .values(
                recalled_at=recalled_at,
                recall_count=EpisodicMemoryModel.recall_count + 1,
            )
        )
        rows = await self.list_for_player(player_key)
        return tuple(row for row in rows if row.text in wanted)

    async def commit(self) -> None:
        await self._session.commit()

    async def rollback(self) -> None:
        await self._session.rollback()

    @staticmethod
    def _model(player_key: str, memory: LongTermMemory) -> EpisodicMemoryModel:
        return EpisodicMemoryModel(
            row_id=str(uuid4()),
            player_key=player_key,
            kind=memory.kind,
            text=memory.text,
            importance=memory.importance,
            source_key=memory.source_key,
            created_at=memory.created_at,
            recalled_at=memory.recalled_at,
            recall_count=memory.recall_count,
            embedding=None if memory.embedding is None else list(memory.embedding),
            embedding_model=memory.embedding_model,
        )
