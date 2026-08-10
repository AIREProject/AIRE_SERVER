"""관리자 CRUD 대상 11개 테이블을 한 곳에 배선한다.

`admin_service.AdminCrudService` 는 여기 있는 스펙만 보고 동작하는 공용 엔진이다 — 새 테이블을
추가하려면 스키마 3개(`app/admin_models.py`)를 쓰고 이 파일에 `AdminResourceSpec` 하나만
더하면 된다. 테이블별 특이사항(자동 타임스탬프, 자격 증명 필드의 사용 불가능한 자리값)은
`prepare_create`/`prepare_update` 훅으로만 들어온다 — 서비스/리포지토리는 그 존재조차 모른다.
"""

import secrets
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from pydantic import BaseModel

from app.admin_models import (
    AdminDeviceResponse,
    AdminOfflineTaskCreateRequest,
    AdminOfflineTaskResponse,
    AdminOfflineTaskUpdateRequest,
    AdminPairingCodeResponse,
    DeviceCreateRequest,
    DeviceUpdateRequest,
    EnemyCreateRequest,
    EnemyResponse,
    EnemyUpdateRequest,
    EpisodicMemoryCreateRequest,
    EpisodicMemoryResponse,
    EpisodicMemoryUpdateRequest,
    ItemCreateRequest,
    ItemResponse,
    ItemUpdateRequest,
    LocationCreateRequest,
    LocationResponse,
    LocationUpdateRequest,
    PairingCodeCreateRequest,
    PairingCodeUpdateRequest,
    ProfileCreateRequest,
    ProfileResponse,
    ProfileUpdateRequest,
    RecipeCreateRequest,
    RecipeResponse,
    RecipeUpdateRequest,
    SaveSlotCreateRequest,
    SaveSlotResponse,
    SaveSlotUpdateRequest,
    SmeltingRecipeCreateRequest,
    SmeltingRecipeResponse,
    SmeltingRecipeUpdateRequest,
)
from app.db.models import (
    DeviceModel,
    EnemyModel,
    EpisodicMemoryModel,
    ItemModel,
    LocationModel,
    OfflineTaskModel,
    PairingCodeModel,
    ProfileModel,
    RecipeModel,
    SaveSlotModel,
    SmeltingRecipeModel,
)

PrepareHook = Callable[[dict[str, object]], dict[str, object]]

# 실제 자격 증명 다이제스트와 절대 같을 수 없는 값 — `authenticate_device_token`
# (app/dependencies.py) 이 미존재 디바이스에 쓰는 것과 같은 sentinel이다. 관리자가 만든 행은
# 정상 페어링 절차를 다시 밟기 전엔 이 값으로 인증을 통과할 수 없다.
_UNUSABLE_CREDENTIAL_HASH = "0" * 64


def _compose(*hooks: PrepareHook) -> PrepareHook:
    def run(values: dict[str, object]) -> dict[str, object]:
        for hook in hooks:
            values = hook(values)
        return values

    return run


def _stamp_created_at(values: dict[str, object]) -> dict[str, object]:
    values.setdefault("created_at", datetime.now(UTC))
    return values


def _prepare_device_create(values: dict[str, object]) -> dict[str, object]:
    values["token_hash"] = _UNUSABLE_CREDENTIAL_HASH
    values["token_lookup_id"] = f"admin-created-{secrets.token_hex(16)}"
    return values


def _prepare_pairing_code_create(values: dict[str, object]) -> dict[str, object]:
    values["code_hash"] = _UNUSABLE_CREDENTIAL_HASH
    return values


@dataclass(frozen=True, slots=True)
class ChildReferenceSpec:
    """DB 에 FK 로 선언되지 않아 `PRAGMA foreign_keys`로도 안 걸리는 참조.

    지금은 `items → recipes.result_item_id` / `items → smelting_recipes.result_item_id`
    뿐이다(`app/db/models.py`: 두 `result_item_id` 컬럼 다 `ForeignKey`가 아니다).
    """

    child_model: type[Any]
    fk_column: str
    label: str


@dataclass(frozen=True, slots=True)
class AdminResourceSpec:
    name: str
    model: type[Any]
    pk_field: str
    create_schema: type[BaseModel]
    update_schema: type[BaseModel]
    response_schema: type[BaseModel]
    non_fk_children: tuple[ChildReferenceSpec, ...] = ()
    prepare_create: PrepareHook | None = None
    prepare_update: PrepareHook | None = None


ADMIN_RESOURCES: tuple[AdminResourceSpec, ...] = (
    AdminResourceSpec(
        name="profiles",
        model=ProfileModel,
        pk_field="profile_id",
        create_schema=ProfileCreateRequest,
        update_schema=ProfileUpdateRequest,
        response_schema=ProfileResponse,
        prepare_create=_stamp_created_at,
    ),
    AdminResourceSpec(
        name="devices",
        model=DeviceModel,
        pk_field="device_id",
        create_schema=DeviceCreateRequest,
        update_schema=DeviceUpdateRequest,
        response_schema=AdminDeviceResponse,
        prepare_create=_compose(_stamp_created_at, _prepare_device_create),
    ),
    AdminResourceSpec(
        name="pairing-codes",
        model=PairingCodeModel,
        pk_field="pairing_code_id",
        create_schema=PairingCodeCreateRequest,
        update_schema=PairingCodeUpdateRequest,
        response_schema=AdminPairingCodeResponse,
        prepare_create=_compose(_stamp_created_at, _prepare_pairing_code_create),
    ),
    AdminResourceSpec(
        name="save-slots",
        model=SaveSlotModel,
        pk_field="row_id",
        create_schema=SaveSlotCreateRequest,
        update_schema=SaveSlotUpdateRequest,
        response_schema=SaveSlotResponse,
        prepare_create=_stamp_created_at,
    ),
    AdminResourceSpec(
        name="items",
        model=ItemModel,
        pk_field="item_id",
        create_schema=ItemCreateRequest,
        update_schema=ItemUpdateRequest,
        response_schema=ItemResponse,
        non_fk_children=(
            ChildReferenceSpec(RecipeModel, "result_item_id", "recipes"),
            ChildReferenceSpec(SmeltingRecipeModel, "result_item_id", "smelting_recipes"),
        ),
    ),
    AdminResourceSpec(
        name="recipes",
        model=RecipeModel,
        pk_field="recipe_id",
        create_schema=RecipeCreateRequest,
        update_schema=RecipeUpdateRequest,
        response_schema=RecipeResponse,
    ),
    AdminResourceSpec(
        name="smelting-recipes",
        model=SmeltingRecipeModel,
        pk_field="smelt_id",
        create_schema=SmeltingRecipeCreateRequest,
        update_schema=SmeltingRecipeUpdateRequest,
        response_schema=SmeltingRecipeResponse,
    ),
    AdminResourceSpec(
        name="enemies",
        model=EnemyModel,
        pk_field="enemy_id",
        create_schema=EnemyCreateRequest,
        update_schema=EnemyUpdateRequest,
        response_schema=EnemyResponse,
    ),
    AdminResourceSpec(
        name="locations",
        model=LocationModel,
        pk_field="location_id",
        create_schema=LocationCreateRequest,
        update_schema=LocationUpdateRequest,
        response_schema=LocationResponse,
    ),
    AdminResourceSpec(
        name="episodic-memories",
        model=EpisodicMemoryModel,
        pk_field="row_id",
        create_schema=EpisodicMemoryCreateRequest,
        update_schema=EpisodicMemoryUpdateRequest,
        response_schema=EpisodicMemoryResponse,
        prepare_create=_stamp_created_at,
    ),
    AdminResourceSpec(
        name="offline-tasks",
        model=OfflineTaskModel,
        pk_field="task_id",
        create_schema=AdminOfflineTaskCreateRequest,
        update_schema=AdminOfflineTaskUpdateRequest,
        response_schema=AdminOfflineTaskResponse,
    ),
)

ADMIN_RESOURCES_BY_NAME: dict[str, AdminResourceSpec] = {
    spec.name: spec for spec in ADMIN_RESOURCES
}
