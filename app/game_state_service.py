"""Validation and atomic acceptance of Game State Snapshots."""

from datetime import UTC, datetime

from sqlalchemy.exc import IntegrityError

from app.db.game_state_repository import (
    GameStateWriteRaceError,
    SqlAlchemyGameStateRepository,
)
from app.db.models import GameStateOperationModel
from app.errors import (
    APIError,
    DeviceRoleNotAllowedError,
    DuplicateRequestError,
    ErrorCode,
    GameStateNotFoundError,
    GameStateVersionConflictError,
    UnknownCompanionError,
)
from app.game_state_models import GameStateResponse, PutGameStateRequest
from app.identity import AuthenticatedDevice, DeviceRole


class GameStateService:
    def __init__(self, repository: SqlAlchemyGameStateRepository) -> None:
        self._repository = repository

    async def put(
        self,
        request: PutGameStateRequest,
        identity: AuthenticatedDevice,
        *,
        body_hash: str,
        payload_size_bytes: int,
        base_state_version: int | None = None,
    ) -> GameStateResponse:
        if identity.role is not DeviceRole.GAME_CLIENT:
            raise DeviceRoleNotAllowedError
        self._validate_companion(request.companion_id)

        slot = await self._repository.find_slot(
            profile_id=identity.profile_id,
            save_slot_id=request.save_slot_id,
        )
        if slot is not None:
            existing_operation = await self._repository.find_operation(
                profile_id=identity.profile_id,
                save_slot_row_id=slot.row_id,
                companion_id=request.companion_id,
                operation_id=request.operation_id,
            )
            if existing_operation is not None:
                return self._replay(existing_operation, body_hash)

        await self._validate_items(request)
        if slot is None:
            slot = await self._repository.get_or_create_slot(
                profile_id=identity.profile_id,
                save_slot_id=request.save_slot_id,
            )
        save_slot_row_id = slot.row_id
        for _attempt in range(3):
            latest = await self._repository.find_latest(
                profile_id=identity.profile_id,
                save_slot_row_id=save_slot_row_id,
                companion_id=request.companion_id,
            )
            if (
                latest is not None
                and latest.operation_id.startswith(("craft-reserve-", "craft-refund-"))
                and base_state_version != latest.state_version
            ):
                raise GameStateVersionConflictError
            if latest is not None and request.state_version <= latest.state_version:
                raise GameStateVersionConflictError

            last_synced_at = datetime.now(UTC)
            response = self._make_response(
                request,
                request_id=request.operation_id,
                last_synced_at=last_synced_at,
            )
            payload = request.model_dump(mode="json")
            try:
                await self._repository.persist(
                    profile_id=identity.profile_id,
                    save_slot_row_id=save_slot_row_id,
                    companion_id=request.companion_id,
                    expected_snapshot=latest,
                    snapshot_values={
                        "schema_version": request.schema_version,
                        "content_version": request.content_version,
                        "operation_id": request.operation_id,
                        "state_version": request.state_version,
                        "world_session_id": request.world_session_id,
                        "captured_at": request.captured_at,
                        "last_synced_at": last_synced_at,
                        "payload": payload,
                        "payload_size_bytes": payload_size_bytes,
                    },
                    operation_id=request.operation_id,
                    body_hash=body_hash,
                    response_body=response.model_dump(mode="json"),
                )
                return response
            except (IntegrityError, GameStateWriteRaceError):
                await self._repository.rollback()
                operation = await self._repository.find_operation(
                    profile_id=identity.profile_id,
                    save_slot_row_id=save_slot_row_id,
                    companion_id=request.companion_id,
                    operation_id=request.operation_id,
                )
                if operation is not None:
                    return self._replay(operation, body_hash)

        raise GameStateVersionConflictError

    async def get(
        self,
        identity: AuthenticatedDevice,
        *,
        request_id: str,
        save_slot_id: str,
        companion_id: str,
    ) -> GameStateResponse:
        self._validate_companion(companion_id)
        slot = await self._repository.find_slot(
            profile_id=identity.profile_id,
            save_slot_id=save_slot_id,
        )
        if slot is None:
            raise GameStateNotFoundError
        latest = await self._repository.find_latest(
            profile_id=identity.profile_id,
            save_slot_row_id=slot.row_id,
            companion_id=companion_id,
        )
        if latest is None:
            raise GameStateNotFoundError
        payload = PutGameStateRequest.model_validate(latest.payload)
        return self._make_response(
            payload,
            request_id=request_id,
            last_synced_at=self._as_utc(latest.last_synced_at),
        )

    async def _validate_items(self, request: PutGameStateRequest) -> None:
        stacks = list(request.inventory.player.stacks)
        equipment_ids = [request.inventory.player.equipment.equipped_item_id]
        for container in request.inventory.containers:
            stacks.extend(container.stacks)
            equipment_ids.append(container.equipment.equipped_item_id)
        item_ids = {stack.item_id for stack in stacks}
        item_ids.update(item_id for item_id in equipment_ids if item_id is not None)
        item_types = await self._repository.item_types(item_ids)
        if set(item_types) != item_ids:
            raise APIError(
                status_code=400,
                code=ErrorCode.INVALID_REQUEST,
                message="Game State contains an unknown item ID.",
            )
        if any(item_types[stack.item_id] == "Weapon" and stack.count != 1 for stack in stacks):
            raise APIError(
                status_code=400,
                code=ErrorCode.INVALID_REQUEST,
                message="Weapon stacks must contain exactly one item.",
            )
        if any(
            item_id is not None and item_types[item_id] != "Weapon" for item_id in equipment_ids
        ):
            raise APIError(
                status_code=400,
                code=ErrorCode.INVALID_REQUEST,
                message="Equipped item IDs must identify weapons.",
            )

    @staticmethod
    def _replay(operation: GameStateOperationModel, body_hash: str) -> GameStateResponse:
        if operation.body_hash != body_hash:
            raise DuplicateRequestError
        return GameStateResponse.model_validate(operation.response_body)

    @staticmethod
    def _make_response(
        request: PutGameStateRequest,
        *,
        request_id: str,
        last_synced_at: datetime,
    ) -> GameStateResponse:
        return GameStateResponse(
            request_id=request_id,
            last_synced_at=last_synced_at,
            **request.model_dump(),
        )

    @staticmethod
    def _as_utc(value: datetime) -> datetime:
        if value.tzinfo is None:
            return value.replace(tzinfo=UTC)
        return value.astimezone(UTC)

    @staticmethod
    def _validate_companion(companion_id: str) -> None:
        if companion_id != "mako":
            raise UnknownCompanionError
