"""Transactional persistence for the latest approved Game State Snapshot."""

from collections.abc import Iterable
from datetime import datetime
from typing import Any, cast
from uuid import uuid4

from sqlalchemy import CursorResult, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import (
    GameStateOperationModel,
    GameStateSnapshotModel,
    ItemModel,
    SaveSlotModel,
)
from app.db.save_slot_repository import SaveSlotRepository


class GameStateWriteRaceError(RuntimeError):
    pass


class SqlAlchemyGameStateRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get_or_create_slot(self, *, profile_id: str, save_slot_id: str) -> SaveSlotModel:
        return await SaveSlotRepository(self._session).get_or_create(
            profile_id=profile_id,
            save_slot_id=save_slot_id,
        )

    async def find_slot(self, *, profile_id: str, save_slot_id: str) -> SaveSlotModel | None:
        result = await self._session.execute(
            select(SaveSlotModel).where(
                SaveSlotModel.profile_id == profile_id,
                SaveSlotModel.save_slot_id == save_slot_id,
            )
        )
        return result.scalar_one_or_none()

    async def find_operation(
        self,
        *,
        profile_id: str,
        save_slot_row_id: str,
        companion_id: str,
        operation_id: str,
    ) -> GameStateOperationModel | None:
        result = await self._session.execute(
            select(GameStateOperationModel).where(
                GameStateOperationModel.profile_id == profile_id,
                GameStateOperationModel.save_slot_row_id == save_slot_row_id,
                GameStateOperationModel.companion_id == companion_id,
                GameStateOperationModel.operation_id == operation_id,
            )
        )
        return result.scalar_one_or_none()

    async def find_latest(
        self,
        *,
        profile_id: str,
        save_slot_row_id: str,
        companion_id: str,
    ) -> GameStateSnapshotModel | None:
        result = await self._session.execute(
            select(GameStateSnapshotModel).where(
                GameStateSnapshotModel.profile_id == profile_id,
                GameStateSnapshotModel.save_slot_row_id == save_slot_row_id,
                GameStateSnapshotModel.companion_id == companion_id,
            )
        )
        return result.scalar_one_or_none()

    async def item_types(self, item_ids: Iterable[str]) -> dict[str, str]:
        unique_ids = set(item_ids)
        if not unique_ids:
            return {}
        result = await self._session.execute(
            select(ItemModel.item_id, ItemModel.item_type).where(ItemModel.item_id.in_(unique_ids))
        )
        return dict(result.tuples().all())

    async def persist(
        self,
        *,
        profile_id: str,
        save_slot_row_id: str,
        companion_id: str,
        expected_snapshot: GameStateSnapshotModel | None,
        snapshot_values: dict[str, object],
        operation_id: str,
        body_hash: str,
        response_body: dict[str, object],
    ) -> None:
        if expected_snapshot is None:
            self._session.add(
                GameStateSnapshotModel(
                    row_id=str(uuid4()),
                    profile_id=profile_id,
                    save_slot_row_id=save_slot_row_id,
                    companion_id=companion_id,
                    **snapshot_values,
                )
            )
        else:
            result = cast(
                "CursorResult[Any]",
                await self._session.execute(
                    update(GameStateSnapshotModel)
                    .where(
                        GameStateSnapshotModel.row_id == expected_snapshot.row_id,
                        GameStateSnapshotModel.state_version == expected_snapshot.state_version,
                    )
                    .values(**snapshot_values)
                ),
            )
            if result.rowcount != 1:
                raise GameStateWriteRaceError

        completed_at = cast(datetime, snapshot_values["last_synced_at"])
        self._session.add(
            GameStateOperationModel(
                row_id=str(uuid4()),
                profile_id=profile_id,
                save_slot_row_id=save_slot_row_id,
                companion_id=companion_id,
                operation_id=operation_id,
                body_hash=body_hash,
                response_status=200,
                response_body=response_body,
                created_at=completed_at,
                completed_at=completed_at,
            )
        )
        await self._session.commit()

    async def rollback(self) -> None:
        await self._session.rollback()
