"""Versioned GameEvent and Command Result API contracts."""

from datetime import datetime
from enum import StrEnum
from typing import Literal, Self

from pydantic import AwareDatetime, Field, JsonValue, model_validator

from app.model_types import StableId, StrictModel
from app.models import CommandType, TimeContext, TimeSource


class GameEventType(StrEnum):
    COMBAT_STARTED = "Event.Combat.Started"
    COMBAT_ENDED = "Event.Combat.Ended"
    DANGER_DETECTED = "Event.Danger.Detected"
    RESCUE_COMPLETED = "Event.Rescue.Completed"
    DISCOVERY_FOUND = "Event.Discovery.Found"
    COMPANION_RETURNED = "Event.Companion.Returned"


class EventImportance(StrEnum):
    NORMAL = "Normal"
    HIGH = "High"


class CommandResultStatus(StrEnum):
    ACCEPTED = "Accepted"
    RUNNING = "Running"
    SUCCEEDED = "Succeeded"
    REJECTED = "Rejected"
    FAILED = "Failed"
    CANCELLED = "Cancelled"
    EXPIRED = "Expired"


class CommandResultReason(StrEnum):
    NONE = "None"
    LEASE_COMPLETED = "LeaseCompleted"
    MALFORMED_CANDIDATE = "MalformedCandidate"
    REQUEST_MISMATCH = "RequestMismatch"
    DUPLICATE_COMMAND = "DuplicateCommand"
    MULTIPLE_CANDIDATES_NOT_SUPPORTED = "MultipleCandidatesNotSupported"
    INVALID_LIFETIME = "InvalidLifetime"
    INVALID_PARAMETERS = "InvalidParameters"
    UNSUPPORTED_EXECUTION = "UnsupportedExecution"
    TARGET_IDENTITY_UNAVAILABLE = "TargetIdentityUnavailable"
    HIGHER_PRIORITY_BEHAVIOR_ACTIVE = "HigherPriorityBehaviorActive"
    PLAYER_UNAVAILABLE = "PlayerUnavailable"
    NAVIGATION_FAILED = "NavigationFailed"
    WORK_ORDER_UNAVAILABLE = "WorkOrderUnavailable"
    WORK_ORDER_CANCELLATION_FAILED = "WorkOrderCancellationFailed"
    RECIPE_UNAVAILABLE = "RecipeUnavailable"
    MATERIALS_UNAVAILABLE = "MaterialsUnavailable"
    WORKBENCH_UNAVAILABLE = "WorkbenchUnavailable"
    WORK_ORDER_FAILED = "WorkOrderFailed"
    THREAT_UNAVAILABLE = "ThreatUnavailable"
    THREAT_TARGET_LOST = "ThreatTargetLost"
    REPLACED_BY_NEW_COMMAND = "ReplacedByNewCommand"
    PREEMPTED_BY_LOCAL_BEHAVIOR = "PreemptedByLocalBehavior"
    OWNER_ENDING_PLAY = "OwnerEndingPlay"
    RESOURCE_UNAVAILABLE = "ResourceUnavailable"


class CreateGameEventRequest(StrictModel):
    schema_version: Literal[1]
    event_id: StableId
    session_id: StableId
    save_slot_id: StableId
    companion_id: StableId
    profile_id: StableId | None = None
    device_id: StableId | None = None
    type: GameEventType
    occurred_at: AwareDatetime
    time_context: TimeContext
    actor_id: StableId
    target_ids: list[StableId] = Field(default_factory=list, max_length=8)
    payload: dict[str, JsonValue] = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_event(self) -> Self:
        if self.time_context.source is not TimeSource.GAME_WORLD:
            raise ValueError("GameEvent time_context must use GameWorld time.")
        if len(self.target_ids) != len(set(self.target_ids)):
            raise ValueError("GameEvent target_ids must be unique.")
        if self.payload:
            raise ValueError("GameEvent v1 payload must be empty.")
        return self


class GameEventResponse(StrictModel):
    request_id: StableId
    event_id: StableId
    schema_version: Literal[1]
    session_id: StableId
    save_slot_id: StableId
    companion_id: StableId
    type: GameEventType
    importance: EventImportance
    occurred_at: datetime
    time_context: TimeContext
    accepted_at: datetime


class CreateCommandResultRequest(StrictModel):
    schema_version: Literal[1]
    operation_id: StableId
    session_id: StableId
    save_slot_id: StableId
    companion_id: StableId
    profile_id: StableId | None = None
    device_id: StableId | None = None
    command_id: StableId
    request_id: StableId
    type: CommandType
    status: CommandResultStatus
    reason: CommandResultReason
    occurred_at: AwareDatetime
    time_context: TimeContext

    @model_validator(mode="after")
    def validate_result(self) -> Self:
        if self.time_context.source is not TimeSource.GAME_WORLD:
            raise ValueError("Command Result time_context must use GameWorld time.")
        if self.status in {CommandResultStatus.ACCEPTED, CommandResultStatus.RUNNING}:
            if self.reason is not CommandResultReason.NONE:
                raise ValueError("Accepted and Running results require reason=None.")
        return self


class CommandResultResponse(StrictModel):
    request_id: StableId
    operation_id: StableId
    schema_version: Literal[1]
    session_id: StableId
    save_slot_id: StableId
    companion_id: StableId
    command_id: StableId
    command_request_id: StableId
    type: CommandType
    status: CommandResultStatus
    reason: CommandResultReason
    occurred_at: datetime
    time_context: TimeContext
    accepted_at: datetime
