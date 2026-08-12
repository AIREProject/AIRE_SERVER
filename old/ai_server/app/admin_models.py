"""`/api/v1/admin` 관리자 CRUD 가 테이블별로 주고받는 계약.

민감 필드(자격 증명 해시)는 여기 없는 것으로 보호된다 — `extra="forbid"`라 요청 바디에
넣으면 422, 응답 스키마에 없는 컬럼은 애초에 직렬화되지 않는다. `docs/AI_RE.sql` 이 아니라
`app/db/models.py` 가 스키마의 유일한 권위다.

`recipes.ingredients`/`smelting_recipes.input_item`·`fuel`/`enemies.weakness`/
`locations.coordinates` 는 DB 에는 마이그레이션 0002 가 쓴 것과 같은 JSON 키
(`ItemId`/`Amount`/`weak_element`/`weak_part`/`ai_advice`/`X`/`Y`/`Z`)로 저장돼야 `game_data_loader`
가 읽는다. 여기 스키마는 파이썬다운 snake_case 를 쓰고, `app/admin_registry.py` 의
`prepare_create`/`prepare_update` 훅이 저장 직전에 그 JSON 모양으로 옮긴다.
"""

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from app.identity import DeviceRole
from app.models import StableId, StrictModel
from app.offline_task_models import OfflineTaskStatus, OfflineTaskType

MAX_BULK_ITEMS = 100


class BulkCreateRequest[T: BaseModel](StrictModel):
    items: list[T] = Field(min_length=1, max_length=MAX_BULK_ITEMS)


class BulkCreateResponse[T: BaseModel](StrictModel):
    created: list[T]


class IngredientPayload(StrictModel):
    """DB JSON 컬럼은 마이그레이션 0002 가 쓴 `ItemId`/`Amount` 키를 그대로 쓴다.

    `populate_by_name`이라 요청 바디는 `item_id`/`amount` 로 보내도 되고, `model_dump
    (by_alias=True)` 로 저장하면 기존 시드 행과 같은 JSON 모양이 나온다.
    """

    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    item_id: StableId = Field(alias="ItemId")
    amount: int = Field(ge=1, alias="Amount")


class WeaknessPayload(StrictModel):
    weak_element: str = Field(min_length=1, max_length=64)
    weak_part: str = Field(min_length=1, max_length=64)
    ai_advice: str = Field(min_length=1, max_length=500)


class CoordinatesPayload(StrictModel):
    """DB JSON 컬럼은 마이그레이션 0002 가 쓴 `X`/`Y`/`Z` 키를 그대로 쓴다."""

    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    x: float = Field(alias="X")
    y: float = Field(alias="Y")
    z: float = Field(alias="Z")


# --- profiles -------------------------------------------------------------------------


class ProfileCreateRequest(StrictModel):
    profile_id: StableId


class ProfileUpdateRequest(StrictModel):
    created_at: datetime | None = None


class ProfileResponse(StrictModel):
    profile_id: StableId
    created_at: datetime


# --- devices ----------------------------------------------------------------------
# token_hash/token_lookup_id 는 의도적으로 어디에도 없다 — §3 가드레일.


class DeviceCreateRequest(StrictModel):
    device_id: StableId
    profile_id: StableId
    role: DeviceRole
    creation_request_id: StableId
    game_registration_key: StableId | None = None


class DeviceUpdateRequest(StrictModel):
    role: DeviceRole | None = None
    game_registration_key: StableId | None = None
    last_used_at: datetime | None = None
    revoked_at: datetime | None = None


class AdminDeviceResponse(StrictModel):
    device_id: StableId
    profile_id: StableId
    role: str
    creation_request_id: StableId
    game_registration_key: StableId | None = None
    created_at: datetime
    last_used_at: datetime | None = None
    revoked_at: datetime | None = None


# --- pairing_codes ------------------------------------------------------------------
# code_hash 는 의도적으로 어디에도 없다. redeemed_request_id/used_at/paired_device_id 는
# 페어링 흐름이 쓰는 생애주기 필드라 Response 에만 노출하고 Create/Update 에서는 뺀다.


class PairingCodeCreateRequest(StrictModel):
    pairing_code_id: StableId
    profile_id: StableId
    issuing_device_id: StableId
    issue_request_id: StableId
    expires_at: datetime


class PairingCodeUpdateRequest(StrictModel):
    expires_at: datetime | None = None


class AdminPairingCodeResponse(StrictModel):
    pairing_code_id: StableId
    profile_id: StableId
    issuing_device_id: StableId
    issue_request_id: StableId
    expires_at: datetime
    created_at: datetime
    used_at: datetime | None = None
    redeemed_request_id: StableId | None = None
    paired_device_id: StableId | None = None


# --- save_slots -----------------------------------------------------------------------


class SaveSlotCreateRequest(StrictModel):
    row_id: StableId
    save_slot_id: StableId
    profile_id: StableId


class SaveSlotUpdateRequest(StrictModel):
    save_slot_id: StableId | None = None


class SaveSlotResponse(StrictModel):
    row_id: StableId
    save_slot_id: StableId
    profile_id: StableId
    created_at: datetime


# --- items ------------------------------------------------------------------------


class ItemCreateRequest(StrictModel):
    item_id: StableId
    item_type: str = Field(min_length=1, max_length=32)
    name_ko: str = Field(min_length=1, max_length=128)
    aliases: list[str] = Field(default_factory=list)
    description: str = Field(min_length=1, max_length=1000)


class ItemUpdateRequest(StrictModel):
    item_type: str | None = Field(default=None, min_length=1, max_length=32)
    name_ko: str | None = Field(default=None, min_length=1, max_length=128)
    aliases: list[str] | None = None
    description: str | None = Field(default=None, min_length=1, max_length=1000)


class ItemResponse(StrictModel):
    item_id: StableId
    item_type: str
    name_ko: str
    aliases: list[str]
    description: str


# --- recipes ------------------------------------------------------------------------


class RecipeCreateRequest(StrictModel):
    recipe_id: StableId
    result_item_id: StableId
    result_amount: int = Field(ge=1)
    required_workbench: str = Field(min_length=1, max_length=64)
    duration_seconds: float = Field(ge=0)
    ingredients: list[IngredientPayload]


class RecipeUpdateRequest(StrictModel):
    result_item_id: StableId | None = None
    result_amount: int | None = Field(default=None, ge=1)
    required_workbench: str | None = Field(default=None, min_length=1, max_length=64)
    duration_seconds: float | None = Field(default=None, ge=0)
    ingredients: list[IngredientPayload] | None = None


class RecipeResponse(StrictModel):
    recipe_id: StableId
    result_item_id: StableId
    result_amount: int
    required_workbench: str
    duration_seconds: float
    ingredients: list[IngredientPayload]


# --- smelting_recipes ----------------------------------------------------------------


class SmeltingRecipeCreateRequest(StrictModel):
    smelt_id: StableId
    result_item_id: StableId
    result_amount: int = Field(ge=1)
    required_workbench: str = Field(min_length=1, max_length=64)
    duration_seconds: float = Field(ge=0)
    input_item: IngredientPayload
    fuel: IngredientPayload


class SmeltingRecipeUpdateRequest(StrictModel):
    result_item_id: StableId | None = None
    result_amount: int | None = Field(default=None, ge=1)
    required_workbench: str | None = Field(default=None, min_length=1, max_length=64)
    duration_seconds: float | None = Field(default=None, ge=0)
    input_item: IngredientPayload | None = None
    fuel: IngredientPayload | None = None


class SmeltingRecipeResponse(StrictModel):
    smelt_id: StableId
    result_item_id: StableId
    result_amount: int
    required_workbench: str
    duration_seconds: float
    input_item: IngredientPayload
    fuel: IngredientPayload


# --- enemies ------------------------------------------------------------------------


class EnemyCreateRequest(StrictModel):
    enemy_id: StableId
    name_ko: str = Field(min_length=1, max_length=128)
    aliases: list[str] = Field(default_factory=list)
    description: str = Field(min_length=1, max_length=2000)
    weakness: WeaknessPayload


class EnemyUpdateRequest(StrictModel):
    name_ko: str | None = Field(default=None, min_length=1, max_length=128)
    aliases: list[str] | None = None
    description: str | None = Field(default=None, min_length=1, max_length=2000)
    weakness: WeaknessPayload | None = None


class EnemyResponse(StrictModel):
    enemy_id: StableId
    name_ko: str
    aliases: list[str]
    description: str
    weakness: WeaknessPayload


# --- locations ----------------------------------------------------------------------


class LocationCreateRequest(StrictModel):
    location_id: StableId
    coordinates: CoordinatesPayload


class LocationUpdateRequest(StrictModel):
    coordinates: CoordinatesPayload | None = None


class LocationResponse(StrictModel):
    location_id: StableId
    coordinates: CoordinatesPayload


# --- episodic_memories ----------------------------------------------------------------


class EpisodicMemoryCreateRequest(StrictModel):
    row_id: StableId
    player_key: StableId
    kind: str = Field(min_length=1, max_length=16)
    text: str = Field(min_length=1)
    importance: int = Field(ge=1, le=10)
    source_key: StableId | None = None
    recall_count: int = Field(default=0, ge=0)
    embedding: list[float] | None = None
    embedding_model: StableId | None = None


class EpisodicMemoryUpdateRequest(StrictModel):
    kind: str | None = Field(default=None, min_length=1, max_length=16)
    text: str | None = Field(default=None, min_length=1)
    importance: int | None = Field(default=None, ge=1, le=10)
    source_key: StableId | None = None
    recalled_at: datetime | None = None
    recall_count: int | None = Field(default=None, ge=0)
    embedding: list[float] | None = None
    embedding_model: StableId | None = None


class EpisodicMemoryResponse(StrictModel):
    row_id: StableId
    player_key: StableId
    kind: str
    text: str
    importance: int
    source_key: StableId | None = None
    created_at: datetime
    recalled_at: datetime | None = None
    recall_count: int
    embedding: list[float] | None = None
    embedding_model: StableId | None = None


# --- offline_tasks --------------------------------------------------------------------


class AdminOfflineTaskCreateRequest(StrictModel):
    task_id: StableId
    profile_id: StableId
    save_slot_row_id: StableId
    issuing_device_id: StableId
    item_id: StableId | None = None
    task_type: OfflineTaskType
    status: OfflineTaskStatus
    started_at: datetime
    creation_request_id: StableId
    quantity: int | None = None
    result_quantity: int | None = None


class AdminOfflineTaskUpdateRequest(StrictModel):
    item_id: StableId | None = None
    task_type: OfflineTaskType | None = None
    status: OfflineTaskStatus | None = None
    started_at: datetime | None = None
    quantity: int | None = None
    result_quantity: int | None = None


class AdminOfflineTaskResponse(StrictModel):
    task_id: StableId
    profile_id: StableId
    save_slot_row_id: StableId
    issuing_device_id: StableId
    item_id: StableId | None = None
    task_type: str
    status: str
    started_at: datetime
    creation_request_id: StableId
    quantity: int | None = None
    result_quantity: int | None = None
