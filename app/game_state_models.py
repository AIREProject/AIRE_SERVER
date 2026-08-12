"""Versioned, bounded Game State Snapshot API contract."""

from datetime import datetime
from typing import Literal, Self

from pydantic import AwareDatetime, Field, model_validator

from app.models import StableId, StrictModel


class GameStateEquipment(StrictModel):
    equipped_item_id: StableId | None = None


class GameStateStack(StrictModel):
    slot_index: int = Field(ge=0, le=109)
    item_id: StableId
    count: int = Field(ge=1, le=99)


class GameStatePlayerInventory(StrictModel):
    capacity: Literal[30]
    revision: int = Field(ge=0)
    stacks: list[GameStateStack] = Field(max_length=40)
    equipment: GameStateEquipment

    @model_validator(mode="after")
    def validate_slots(self) -> Self:
        slots = [stack.slot_index for stack in self.stacks]
        if len(slots) != len(set(slots)):
            raise ValueError("Player inventory slots must be unique.")
        if any(not (0 <= slot < 30 or 100 <= slot < 110) for slot in slots):
            raise ValueError("Player inventory slot is outside the supported ranges.")
        return self


class GameStateContainer(StrictModel):
    container_id: Literal[
        "AIRE.Inventory.MAKO",
        "AIRE.Inventory.SharedStorage",
    ]
    capacity: int
    revision: int = Field(ge=0)
    stacks: list[GameStateStack] = Field(max_length=50)
    equipment: GameStateEquipment

    @model_validator(mode="after")
    def validate_container(self) -> Self:
        expected_capacity = 20 if self.container_id == "AIRE.Inventory.MAKO" else 50
        if self.capacity != expected_capacity:
            raise ValueError("Container capacity does not match its stable container ID.")
        if len(self.stacks) > self.capacity:
            raise ValueError("Container has more stacks than slots.")
        slots = [stack.slot_index for stack in self.stacks]
        if len(slots) != len(set(slots)):
            raise ValueError("Container slots must be unique.")
        if any(slot >= self.capacity for slot in slots):
            raise ValueError("Container slot is outside its capacity.")
        if (
            self.container_id == "AIRE.Inventory.SharedStorage"
            and self.equipment.equipped_item_id is not None
        ):
            raise ValueError("Shared Storage cannot have equipped items.")
        return self


class GameStateInventory(StrictModel):
    player: GameStatePlayerInventory
    containers: list[GameStateContainer] = Field(min_length=2, max_length=2)

    @model_validator(mode="after")
    def validate_containers(self) -> Self:
        actual = {container.container_id for container in self.containers}
        expected = {"AIRE.Inventory.MAKO", "AIRE.Inventory.SharedStorage"}
        if actual != expected:
            raise ValueError("Inventory must contain MAKO and Shared Storage exactly once.")
        return self


class PutGameStateRequest(StrictModel):
    schema_version: Literal[1]
    content_version: Literal[1]
    operation_id: StableId
    state_version: int = Field(ge=1)
    world_session_id: StableId
    captured_at: AwareDatetime
    save_slot_id: StableId
    companion_id: StableId
    inventory: GameStateInventory


class GameStateResponse(StrictModel):
    request_id: StableId
    operation_id: StableId
    schema_version: Literal[1]
    content_version: Literal[1]
    state_version: int = Field(ge=1)
    world_session_id: StableId
    captured_at: datetime
    last_synced_at: datetime
    save_slot_id: StableId
    companion_id: StableId
    inventory: GameStateInventory
