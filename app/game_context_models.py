import json
from enum import StrEnum
from typing import Literal, Self

from pydantic import Field, model_validator

from app.model_types import StableId, StrictModel

MAX_GAME_CONTEXT_BYTES = 8 * 1024
MAX_NEARBY_RESOURCE_TYPES = 8
MAX_AVAILABLE_WORKSTATIONS = 8
MAX_INVENTORY_ITEM_TYPES = 16


class ThreatContext(StrictModel):
    present: bool
    count: int = Field(ge=0, le=32)
    nearest_kind: StableId | None

    @model_validator(mode="after")
    def validate_presence(self) -> Self:
        if self.present != (self.count > 0):
            raise ValueError("Threat presence must match whether count is greater than zero.")
        if self.count == 0 and self.nearest_kind is not None:
            raise ValueError("An empty threat context cannot contain a nearest kind.")
        return self


class NearbyResourceContext(StrictModel):
    kind: StableId
    count: int = Field(ge=1, le=32)


class WorkType(StrEnum):
    CRAFTING = "Crafting"
    HARVESTING = "Harvesting"
    STORAGE_TRANSFER = "StorageTransfer"


class WorkState(StrEnum):
    REQUESTED = "Requested"
    MOVING = "Moving"
    WORKING = "Working"
    PAUSED_BY_COMBAT = "PausedByCombat"


class CurrentWorkContext(StrictModel):
    type: WorkType
    state: WorkState


class InventoryItemTotal(StrictModel):
    item_id: StableId
    count: int = Field(ge=1, le=4_950)


class InventoryContainerId(StrEnum):
    MAKO = "AIRE.Inventory.MAKO"
    SHARED_STORAGE = "AIRE.Inventory.SharedStorage"


class InventoryContext(StrictModel):
    container_id: InventoryContainerId
    free_slots: int = Field(ge=0, le=50)
    item_totals: list[InventoryItemTotal] = Field(max_length=MAX_INVENTORY_ITEM_TYPES)
    truncated: bool

    @model_validator(mode="after")
    def validate_capacity_and_items(self) -> Self:
        capacity = 20 if self.container_id is InventoryContainerId.MAKO else 50
        if self.free_slots > capacity:
            raise ValueError("Inventory free slots exceed the container capacity.")
        if len(self.item_totals) != len({item.item_id for item in self.item_totals}):
            raise ValueError("Inventory item totals must use unique item ids.")
        if sum(item.count for item in self.item_totals) > capacity * 99:
            raise ValueError("Inventory item totals exceed the container item bound.")
        return self


class GameContextV1(StrictModel):
    schema_version: Literal[1]
    location_id: StableId | None
    threat: ThreatContext
    nearby_resources: list[NearbyResourceContext] = Field(max_length=MAX_NEARBY_RESOURCE_TYPES)
    available_workstations: list[StableId] = Field(max_length=MAX_AVAILABLE_WORKSTATIONS)
    current_work: CurrentWorkContext | None
    inventories: list[InventoryContext] = Field(max_length=2)

    @model_validator(mode="before")
    @classmethod
    def validate_input_size(cls, value: object) -> object:
        if not isinstance(value, dict):
            return value
        try:
            compact = json.dumps(value, ensure_ascii=False, separators=(",", ":"))
        except (TypeError, ValueError):
            return value
        if len(compact.encode("utf-8")) > MAX_GAME_CONTEXT_BYTES:
            raise ValueError("Game context exceeds the 8 KiB compact JSON limit.")
        return value

    @model_validator(mode="after")
    def validate_collections_and_size(self) -> Self:
        resource_kinds = [resource.kind for resource in self.nearby_resources]
        if len(resource_kinds) != len(set(resource_kinds)):
            raise ValueError("Nearby resources must use unique kinds.")
        if len(self.available_workstations) != len(set(self.available_workstations)):
            raise ValueError("Available workstations must be unique.")
        container_ids = [inventory.container_id for inventory in self.inventories]
        if len(container_ids) != len(set(container_ids)):
            raise ValueError("Inventory summaries must use unique container ids.")
        if len(self.model_dump_json().encode("utf-8")) > MAX_GAME_CONTEXT_BYTES:
            raise ValueError("Game context exceeds the 8 KiB compact JSON limit.")
        return self
