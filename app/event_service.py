"""Validation and durable idempotent acceptance of UE GameEvents/results."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from sqlalchemy.exc import IntegrityError

from app.db.event_repository import SqlAlchemyEventRepository
from app.db.models import CommandResultModel, GameEventModel
from app.errors import (
    CommandCandidateNotFoundError,
    CommandResultTransitionError,
    DeviceRoleNotAllowedError,
    DuplicateRequestError,
    IdempotencyRecordExpiredError,
    UnknownCompanionError,
)
from app.event_models import (
    CommandResultResponse,
    CommandResultStatus,
    CreateCommandResultRequest,
    CreateGameEventRequest,
    EventImportance,
    GameEventResponse,
    GameEventType,
)
from app.identity import AuthenticatedDevice, DeviceRole

_IMPORTANCE = {
    GameEventType.COMBAT_STARTED: EventImportance.NORMAL,
    GameEventType.COMBAT_ENDED: EventImportance.NORMAL,
    GameEventType.COMPANION_RETURNED: EventImportance.NORMAL,
    GameEventType.DANGER_DETECTED: EventImportance.HIGH,
    GameEventType.RESCUE_COMPLETED: EventImportance.HIGH,
    GameEventType.DISCOVERY_FOUND: EventImportance.HIGH,
}
_INITIAL = {CommandResultStatus.ACCEPTED, CommandResultStatus.REJECTED, CommandResultStatus.EXPIRED}
_NEXT = {
    CommandResultStatus.ACCEPTED: {CommandResultStatus.RUNNING},
    CommandResultStatus.RUNNING: {
        CommandResultStatus.SUCCEEDED,
        CommandResultStatus.FAILED,
        CommandResultStatus.CANCELLED,
        CommandResultStatus.EXPIRED,
    },
}


class EventService:
    def __init__(
        self,
        repository: SqlAlchemyEventRepository,
        *,
        event_retention_days: int,
        audit_retention_days: int,
    ) -> None:
        self._repository = repository
        self._event_retention = timedelta(days=event_retention_days)
        self._audit_retention = timedelta(days=audit_retention_days)

    async def create_event(
        self, request: CreateGameEventRequest, identity: AuthenticatedDevice, *, body_hash: str
    ) -> GameEventResponse:
        self._require_game(identity)
        identity.validate_claims(request.profile_id, request.device_id)
        self._validate_companion(request.companion_id)
        slot = await self._repository.get_or_create_slot(
            profile_id=identity.profile_id, save_slot_id=request.save_slot_id
        )
        existing = await self._repository.find_event(
            profile_id=identity.profile_id,
            save_slot_row_id=slot.row_id,
            companion_id=request.companion_id,
            event_id=request.event_id,
        )
        if existing is not None:
            return self._replay_event(existing, body_hash)

        now = datetime.now(UTC)
        response = GameEventResponse(
            request_id=request.event_id,
            event_id=request.event_id,
            schema_version=request.schema_version,
            session_id=request.session_id,
            save_slot_id=request.save_slot_id,
            companion_id=request.companion_id,
            type=request.type,
            importance=_IMPORTANCE[request.type],
            occurred_at=request.occurred_at,
            time_context=request.time_context,
            accepted_at=now,
        )
        try:
            await self._repository.persist_event(
                {
                    "event_id": request.event_id,
                    "profile_id": identity.profile_id,
                    "save_slot_row_id": slot.row_id,
                    "companion_id": request.companion_id,
                    "session_id": request.session_id,
                    "schema_version": request.schema_version,
                    "event_type": request.type.value,
                    "importance": response.importance.value,
                    "occurred_at": request.occurred_at,
                    "game_time": request.time_context.model_dump(mode="json"),
                    "actor_id": request.actor_id,
                    "target_ids": list(request.target_ids),
                    "body_hash": body_hash,
                    "storage_class": "Transient",
                    "retention_reason": "GameEventDefault",
                    "expires_at": now + self._event_retention,
                    "audit_expires_at": now + self._audit_retention,
                    "content_deleted_at": None,
                    "response_body": response.model_dump(mode="json"),
                    "received_at": now,
                }
            )
        except IntegrityError:
            await self._repository.rollback()
            existing = await self._repository.find_event(
                profile_id=identity.profile_id,
                save_slot_row_id=slot.row_id,
                companion_id=request.companion_id,
                event_id=request.event_id,
            )
            if existing is not None:
                return self._replay_event(existing, body_hash)
            raise
        return response

    async def create_command_result(
        self, request: CreateCommandResultRequest, identity: AuthenticatedDevice, *, body_hash: str
    ) -> CommandResultResponse:
        self._require_game(identity)
        identity.validate_claims(request.profile_id, request.device_id)
        self._validate_companion(request.companion_id)
        slot = await self._repository.find_slot(
            profile_id=identity.profile_id, save_slot_id=request.save_slot_id
        )
        if slot is None:
            raise CommandCandidateNotFoundError
        existing = await self._repository.find_result_operation(
            profile_id=identity.profile_id,
            save_slot_row_id=slot.row_id,
            companion_id=request.companion_id,
            operation_id=request.operation_id,
        )
        if existing is not None:
            return self._replay_result(existing, body_hash)
        candidate = await self._repository.find_candidate(
            profile_id=identity.profile_id,
            save_slot_row_id=slot.row_id,
            companion_id=request.companion_id,
            session_id=request.session_id,
            command_id=request.command_id,
        )
        if (
            candidate is None
            or candidate.request_id != request.request_id
            or candidate.command_type != request.type.value
        ):
            raise CommandCandidateNotFoundError
        latest = await self._repository.latest_result(candidate_row_id=candidate.row_id)
        if latest is None:
            if request.status not in _INITIAL:
                raise CommandResultTransitionError
        elif request.status not in _NEXT.get(CommandResultStatus(latest.status), set()):
            raise CommandResultTransitionError
        now = datetime.now(UTC)
        response = CommandResultResponse(
            request_id=request.operation_id,
            operation_id=request.operation_id,
            schema_version=request.schema_version,
            session_id=request.session_id,
            save_slot_id=request.save_slot_id,
            companion_id=request.companion_id,
            command_id=request.command_id,
            command_request_id=request.request_id,
            type=request.type,
            status=request.status,
            reason=request.reason,
            occurred_at=request.occurred_at,
            time_context=request.time_context,
            accepted_at=now,
        )
        try:
            await self._repository.persist_result(
                {
                    "operation_id": request.operation_id,
                    "profile_id": identity.profile_id,
                    "save_slot_row_id": slot.row_id,
                    "companion_id": request.companion_id,
                    "schema_version": request.schema_version,
                    "candidate_row_id": candidate.row_id,
                    "command_id": candidate.command_id,
                    "request_id": request.request_id,
                    "command_type": request.type.value,
                    "status": request.status.value,
                    "reason": request.reason.value,
                    "occurred_at": request.occurred_at,
                    "game_time": request.time_context.model_dump(mode="json"),
                    "body_hash": body_hash,
                    "response_body": response.model_dump(mode="json"),
                    "received_at": now,
                    "audit_expires_at": now + self._audit_retention,
                }
            )
        except IntegrityError:
            await self._repository.rollback()
            existing = await self._repository.find_result_operation(
                profile_id=identity.profile_id,
                save_slot_row_id=slot.row_id,
                companion_id=request.companion_id,
                operation_id=request.operation_id,
            )
            if existing is not None:
                return self._replay_result(existing, body_hash)
            raise
        return response

    @staticmethod
    def _replay_event(event: GameEventModel, body_hash: str) -> GameEventResponse:
        if event.body_hash != body_hash:
            raise DuplicateRequestError
        if event.response_body is None:
            raise IdempotencyRecordExpiredError
        return GameEventResponse.model_validate(event.response_body)

    @staticmethod
    def _replay_result(result: CommandResultModel, body_hash: str) -> CommandResultResponse:
        if result.body_hash != body_hash:
            raise DuplicateRequestError
        return CommandResultResponse.model_validate(result.response_body)

    @staticmethod
    def _require_game(identity: AuthenticatedDevice) -> None:
        if identity.role is not DeviceRole.GAME_CLIENT:
            raise DeviceRoleNotAllowedError

    @staticmethod
    def _validate_companion(companion_id: str) -> None:
        if companion_id != "mako":
            raise UnknownCompanionError(companion_id)
