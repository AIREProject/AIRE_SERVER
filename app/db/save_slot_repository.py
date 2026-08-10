"""세이브슬롯 get-or-create.

`cd0be55` 에는 없던 모듈이다 — 그 트리에서는 이 로직이 되살리지 않는
`chat_repository.py` 안에 있었다. `save_slot_id` 는 프로필 범위 안에서만 고유하면
되므로, 대리키(`row_id`)를 기본키로 두고 (profile_id, save_slot_id) 를 조회 기준으로 쓴다.
"""

from datetime import UTC, datetime
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import SaveSlotModel


class SaveSlotRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get_or_create(self, *, profile_id: str, save_slot_id: str) -> SaveSlotModel:
        existing = await self._find(profile_id, save_slot_id)
        if existing is not None:
            return existing

        slot = SaveSlotModel(
            row_id=f"save-slot-{uuid4()}",
            save_slot_id=save_slot_id,
            profile_id=profile_id,
            created_at=datetime.now(UTC),
        )
        self._session.add(slot)
        try:
            await self._session.commit()
        except IntegrityError:
            # 동시 요청이 같은 슬롯을 먼저 만들었다 — 그 결과를 그대로 쓴다.
            await self._session.rollback()
            existing = await self._find(profile_id, save_slot_id)
            if existing is None:
                raise
            return existing
        return slot

    async def _find(self, profile_id: str, save_slot_id: str) -> SaveSlotModel | None:
        result = await self._session.execute(
            select(SaveSlotModel).where(
                SaveSlotModel.profile_id == profile_id,
                SaveSlotModel.save_slot_id == save_slot_id,
            )
        )
        return result.scalar_one_or_none()
