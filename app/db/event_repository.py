"""Transactional storage for canonical GameEvents and Command Results."""

from __future__ import annotations

from typing import Any
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import (
    CommandCandidateModel,
    CommandResultModel,
    GameEventModel,
    SaveSlotModel,
    SourceOutboxModel,
)
from app.db.save_slot_repository import SaveSlotRepository


class SqlAlchemyEventRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get_or_create_slot(self, *, profile_id: str, save_slot_id: str) -> SaveSlotModel:
        return await SaveSlotRepository(self._session).get_or_create(
            profile_id=profile_id, save_slot_id=save_slot_id
        )

    async def find_slot(self, *, profile_id: str, save_slot_id: str) -> SaveSlotModel | None:
        result = await self._session.execute(
            select(SaveSlotModel).where(
                SaveSlotModel.profile_id == profile_id,
                SaveSlotModel.save_slot_id == save_slot_id,
            )
        )
        return result.scalar_one_or_none()

    async def find_event(
        self, *, profile_id: str, save_slot_row_id: str, companion_id: str, event_id: str
    ) -> GameEventModel | None:
        result = await self._session.execute(
            select(GameEventModel).where(
                GameEventModel.profile_id == profile_id,
                GameEventModel.save_slot_row_id == save_slot_row_id,
                GameEventModel.companion_id == companion_id,
                GameEventModel.event_id == event_id,
            )
        )
        return result.scalar_one_or_none()

    async def find_result_operation(
        self, *, profile_id: str, save_slot_row_id: str, companion_id: str, operation_id: str
    ) -> CommandResultModel | None:
        result = await self._session.execute(
            select(CommandResultModel).where(
                CommandResultModel.profile_id == profile_id,
                CommandResultModel.save_slot_row_id == save_slot_row_id,
                CommandResultModel.companion_id == companion_id,
                CommandResultModel.operation_id == operation_id,
            )
        )
        return result.scalar_one_or_none()

    async def find_candidate(
        self,
        *,
        profile_id: str,
        save_slot_row_id: str,
        companion_id: str,
        session_id: str,
        command_id: str,
    ) -> CommandCandidateModel | None:
        result = await self._session.execute(
            select(CommandCandidateModel).where(
                CommandCandidateModel.command_id == command_id,
                CommandCandidateModel.profile_id == profile_id,
                CommandCandidateModel.save_slot_row_id == save_slot_row_id,
                CommandCandidateModel.companion_id == companion_id,
                CommandCandidateModel.session_id == session_id,
            )
        )
        return result.scalar_one_or_none()

    async def latest_result(self, *, candidate_row_id: str) -> CommandResultModel | None:
        result = await self._session.execute(
            select(CommandResultModel)
            .where(CommandResultModel.candidate_row_id == candidate_row_id)
            .order_by(CommandResultModel.received_at.desc(), CommandResultModel.row_id.desc())
            .limit(1)
        )
        return result.scalar_one_or_none()

    async def persist_event(self, values: dict[str, Any]) -> None:
        event_row_id = str(uuid4())
        self._session.add(GameEventModel(row_id=event_row_id, **values))
        self._session.add(
            SourceOutboxModel(
                source_type="Event",
                source_id=event_row_id,
                state="Pending",
                lease_token=None,
                lease_expires_at=None,
                attempt_count=0,
                created_at=values["received_at"],
                completed_at=None,
            )
        )
        await self._session.commit()

    async def persist_result(self, values: dict[str, Any]) -> None:
        self._session.add(CommandResultModel(row_id=str(uuid4()), **values))
        await self._session.commit()

    async def rollback(self) -> None:
        await self._session.rollback()
