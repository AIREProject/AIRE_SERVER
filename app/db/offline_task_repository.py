"""프로필·세이브슬롯 범위의 Offline_Task 저장소."""

from datetime import datetime
from typing import Any, cast

from sqlalchemy import CursorResult, delete, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import (
    ItemModel,
    OfflineTaskModel,
    OfflineTaskPolicyModel,
    SaveSlotModel,
)
from app.db.save_slot_repository import SaveSlotRepository


class SqlAlchemyOfflineTaskRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def find_slot(self, profile_id: str, save_slot_id: str) -> SaveSlotModel | None:
        result = await self._session.execute(
            select(SaveSlotModel).where(
                SaveSlotModel.profile_id == profile_id,
                SaveSlotModel.save_slot_id == save_slot_id,
            )
        )
        return result.scalar_one_or_none()

    async def find_slot_by_row_id(self, row_id: str) -> SaveSlotModel | None:
        return await self._session.get(SaveSlotModel, row_id)

    async def get_or_create_slot(self, *, profile_id: str, save_slot_id: str) -> SaveSlotModel:
        return await SaveSlotRepository(self._session).get_or_create(
            profile_id=profile_id,
            save_slot_id=save_slot_id,
        )

    async def item_exists(self, item_id: str) -> bool:
        result = await self._session.execute(
            select(ItemModel.item_id).where(ItemModel.item_id == item_id)
        )
        return result.scalar_one_or_none() is not None

    async def find_by_creation_request(
        self,
        *,
        profile_id: str,
        save_slot_row_id: str,
        request_id: str,
    ) -> OfflineTaskModel | None:
        result = await self._session.execute(
            select(OfflineTaskModel).where(
                OfflineTaskModel.profile_id == profile_id,
                OfflineTaskModel.save_slot_row_id == save_slot_row_id,
                OfflineTaskModel.creation_request_id == request_id,
            )
        )
        return result.scalar_one_or_none()

    async def find_policy(
        self,
        *,
        task_type: str,
        item_id: str | None,
    ) -> OfflineTaskPolicyModel | None:
        if item_id is None:
            return None
        result = await self._session.execute(
            select(OfflineTaskPolicyModel).where(
                OfflineTaskPolicyModel.task_type == task_type,
                OfflineTaskPolicyModel.item_id == item_id,
            )
        )
        return result.scalar_one_or_none()

    async def create_task(self, **values: object) -> OfflineTaskModel:
        task = OfflineTaskModel(**values)
        self._session.add(task)
        return task

    async def list_tasks(
        self,
        *,
        profile_id: str,
        save_slot_row_id: str,
        status: str | None,
    ) -> list[OfflineTaskModel]:
        statement = select(OfflineTaskModel).where(
            OfflineTaskModel.profile_id == profile_id,
            OfflineTaskModel.save_slot_row_id == save_slot_row_id,
        )
        if status is not None:
            statement = statement.where(OfflineTaskModel.status == status)
        result = await self._session.execute(
            statement.order_by(OfflineTaskModel.started_at, OfflineTaskModel.task_id)
        )
        return list(result.scalars())

    async def get_owned_task(
        self, *, task_id: str, profile_id: str
    ) -> OfflineTaskModel | None:
        result = await self._session.execute(
            select(OfflineTaskModel).where(
                OfflineTaskModel.task_id == task_id,
                OfflineTaskModel.profile_id == profile_id,
            )
        )
        return result.scalar_one_or_none()

    async def transition(
        self,
        *,
        task_id: str,
        profile_id: str,
        expected_status: str,
        new_status: str,
        **extra_values: object,
    ) -> bool:
        result = cast(
            "CursorResult[Any]",
            await self._session.execute(
                update(OfflineTaskModel)
                .where(
                    OfflineTaskModel.task_id == task_id,
                    OfflineTaskModel.profile_id == profile_id,
                    OfflineTaskModel.status == expected_status,
                )
                .values(status=new_status, **extra_values)
            ),
        )
        return result.rowcount == 1

    async def delete_owned_if_status(
        self,
        *,
        task_id: str,
        profile_id: str,
        allowed_statuses: tuple[str, ...],
    ) -> bool:
        result = cast(
            "CursorResult[Any]",
            await self._session.execute(
                delete(OfflineTaskModel).where(
                    OfflineTaskModel.task_id == task_id,
                    OfflineTaskModel.profile_id == profile_id,
                    OfflineTaskModel.status.in_(allowed_statuses),
                )
            ),
        )
        return result.rowcount == 1

    async def commit(self) -> None:
        await self._session.commit()

    async def rollback(self) -> None:
        await self._session.rollback()

    @staticmethod
    def as_utc(value: datetime) -> datetime:
        from datetime import UTC

        if value.tzinfo is None:
            return value.replace(tzinfo=UTC)
        return value.astimezone(UTC)
