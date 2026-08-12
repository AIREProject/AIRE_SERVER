"""테이블별 관리자 CRUD. 라우터 전체가 `get_admin_token`(app/dependencies.py) 뒤에 있다.

`docs/handoff.md` §6-4 가 지적한 빈틈(게임 데이터 읽기 엔드포인트 없음, 디바이스/오프라인
태스크에 수정·삭제 없음)을 메우는 것이 목적이다. `app/routes/devices.py`/`offline_tasks.py`
의 채팅·페어링 전용 엔드포인트와는 별개 표면이다 — 저기는 게임 클라이언트/모바일이 쓰고,
여기는 고정 관리자 토큰을 아는 운영자만 쓴다.
"""

from typing import Annotated

from fastapi import APIRouter, Depends, Query

from app.admin_models import (
    AdminDeviceResponse,
    AdminOfflineTaskCreateRequest,
    AdminOfflineTaskResponse,
    AdminOfflineTaskUpdateRequest,
    AdminPairingCodeResponse,
    BulkCreateRequest,
    BulkCreateResponse,
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
from app.admin_registry import ADMIN_RESOURCES_BY_NAME
from app.admin_repository import AdminRepository
from app.admin_service import AdminCrudService
from app.dependencies import DatabaseSession, get_admin_token

router = APIRouter(
    prefix="/api/v1/admin",
    tags=["Admin"],
    dependencies=[Depends(get_admin_token)],
)

Limit = Annotated[int, Query(ge=1, le=200)]
Offset = Annotated[int, Query(ge=0)]


# --- profiles -------------------------------------------------------------------------


def _profiles_service(session: DatabaseSession) -> AdminCrudService[ProfileResponse]:
    return AdminCrudService(ADMIN_RESOURCES_BY_NAME["profiles"], AdminRepository(session))


@router.post("/profiles", response_model=ProfileResponse)
async def create_profile(body: ProfileCreateRequest, session: DatabaseSession) -> ProfileResponse:
    return await _profiles_service(session).create_resource(body)


@router.post("/profiles/bulk", response_model=BulkCreateResponse[ProfileResponse])
async def create_profiles_bulk(
    body: BulkCreateRequest[ProfileCreateRequest], session: DatabaseSession
) -> BulkCreateResponse[ProfileResponse]:
    created = await _profiles_service(session).create_resources(body.items)
    return BulkCreateResponse(created=created)


@router.get("/profiles", response_model=list[ProfileResponse])
async def list_profiles(
    session: DatabaseSession, limit: Limit = 50, offset: Offset = 0
) -> list[ProfileResponse]:
    return await _profiles_service(session).list_resources(limit=limit, offset=offset)


@router.get("/profiles/{profile_id}", response_model=ProfileResponse)
async def get_profile(profile_id: str, session: DatabaseSession) -> ProfileResponse:
    return await _profiles_service(session).get_resource(profile_id)


@router.patch("/profiles/{profile_id}", response_model=ProfileResponse)
async def update_profile(
    profile_id: str, body: ProfileUpdateRequest, session: DatabaseSession
) -> ProfileResponse:
    return await _profiles_service(session).update_resource(profile_id, body)


@router.delete("/profiles/{profile_id}", status_code=204)
async def delete_profile(profile_id: str, session: DatabaseSession) -> None:
    await _profiles_service(session).delete_resource(profile_id)


# --- devices ----------------------------------------------------------------------


def _devices_service(session: DatabaseSession) -> AdminCrudService[AdminDeviceResponse]:
    return AdminCrudService(ADMIN_RESOURCES_BY_NAME["devices"], AdminRepository(session))


@router.post("/devices", response_model=AdminDeviceResponse)
async def create_device(
    body: DeviceCreateRequest, session: DatabaseSession
) -> AdminDeviceResponse:
    return await _devices_service(session).create_resource(body)


@router.post("/devices/bulk", response_model=BulkCreateResponse[AdminDeviceResponse])
async def create_devices_bulk(
    body: BulkCreateRequest[DeviceCreateRequest], session: DatabaseSession
) -> BulkCreateResponse[AdminDeviceResponse]:
    created = await _devices_service(session).create_resources(body.items)
    return BulkCreateResponse(created=created)


@router.get("/devices", response_model=list[AdminDeviceResponse])
async def list_devices_admin(
    session: DatabaseSession, limit: Limit = 50, offset: Offset = 0
) -> list[AdminDeviceResponse]:
    return await _devices_service(session).list_resources(limit=limit, offset=offset)


@router.get("/devices/{device_id}", response_model=AdminDeviceResponse)
async def get_device_admin(device_id: str, session: DatabaseSession) -> AdminDeviceResponse:
    return await _devices_service(session).get_resource(device_id)


@router.patch("/devices/{device_id}", response_model=AdminDeviceResponse)
async def update_device_admin(
    device_id: str, body: DeviceUpdateRequest, session: DatabaseSession
) -> AdminDeviceResponse:
    return await _devices_service(session).update_resource(device_id, body)


@router.delete("/devices/{device_id}", status_code=204)
async def delete_device_admin(device_id: str, session: DatabaseSession) -> None:
    await _devices_service(session).delete_resource(device_id)


# --- pairing-codes ------------------------------------------------------------------


def _pairing_codes_service(session: DatabaseSession) -> AdminCrudService[AdminPairingCodeResponse]:
    return AdminCrudService(ADMIN_RESOURCES_BY_NAME["pairing-codes"], AdminRepository(session))


@router.post("/pairing-codes", response_model=AdminPairingCodeResponse)
async def create_pairing_code_admin(
    body: PairingCodeCreateRequest, session: DatabaseSession
) -> AdminPairingCodeResponse:
    return await _pairing_codes_service(session).create_resource(body)


@router.post("/pairing-codes/bulk", response_model=BulkCreateResponse[AdminPairingCodeResponse])
async def create_pairing_codes_bulk(
    body: BulkCreateRequest[PairingCodeCreateRequest], session: DatabaseSession
) -> BulkCreateResponse[AdminPairingCodeResponse]:
    created = await _pairing_codes_service(session).create_resources(body.items)
    return BulkCreateResponse(created=created)


@router.get("/pairing-codes", response_model=list[AdminPairingCodeResponse])
async def list_pairing_codes_admin(
    session: DatabaseSession, limit: Limit = 50, offset: Offset = 0
) -> list[AdminPairingCodeResponse]:
    return await _pairing_codes_service(session).list_resources(limit=limit, offset=offset)


@router.get("/pairing-codes/{pairing_code_id}", response_model=AdminPairingCodeResponse)
async def get_pairing_code_admin(
    pairing_code_id: str, session: DatabaseSession
) -> AdminPairingCodeResponse:
    return await _pairing_codes_service(session).get_resource(pairing_code_id)


@router.patch("/pairing-codes/{pairing_code_id}", response_model=AdminPairingCodeResponse)
async def update_pairing_code_admin(
    pairing_code_id: str, body: PairingCodeUpdateRequest, session: DatabaseSession
) -> AdminPairingCodeResponse:
    return await _pairing_codes_service(session).update_resource(pairing_code_id, body)


@router.delete("/pairing-codes/{pairing_code_id}", status_code=204)
async def delete_pairing_code_admin(pairing_code_id: str, session: DatabaseSession) -> None:
    await _pairing_codes_service(session).delete_resource(pairing_code_id)


# --- save-slots -----------------------------------------------------------------------


def _save_slots_service(session: DatabaseSession) -> AdminCrudService[SaveSlotResponse]:
    return AdminCrudService(ADMIN_RESOURCES_BY_NAME["save-slots"], AdminRepository(session))


@router.post("/save-slots", response_model=SaveSlotResponse)
async def create_save_slot_admin(
    body: SaveSlotCreateRequest, session: DatabaseSession
) -> SaveSlotResponse:
    return await _save_slots_service(session).create_resource(body)


@router.post("/save-slots/bulk", response_model=BulkCreateResponse[SaveSlotResponse])
async def create_save_slots_bulk(
    body: BulkCreateRequest[SaveSlotCreateRequest], session: DatabaseSession
) -> BulkCreateResponse[SaveSlotResponse]:
    created = await _save_slots_service(session).create_resources(body.items)
    return BulkCreateResponse(created=created)


@router.get("/save-slots", response_model=list[SaveSlotResponse])
async def list_save_slots_admin(
    session: DatabaseSession, limit: Limit = 50, offset: Offset = 0
) -> list[SaveSlotResponse]:
    return await _save_slots_service(session).list_resources(limit=limit, offset=offset)


@router.get("/save-slots/{row_id}", response_model=SaveSlotResponse)
async def get_save_slot_admin(row_id: str, session: DatabaseSession) -> SaveSlotResponse:
    return await _save_slots_service(session).get_resource(row_id)


@router.patch("/save-slots/{row_id}", response_model=SaveSlotResponse)
async def update_save_slot_admin(
    row_id: str, body: SaveSlotUpdateRequest, session: DatabaseSession
) -> SaveSlotResponse:
    return await _save_slots_service(session).update_resource(row_id, body)


@router.delete("/save-slots/{row_id}", status_code=204)
async def delete_save_slot_admin(row_id: str, session: DatabaseSession) -> None:
    await _save_slots_service(session).delete_resource(row_id)


# --- items ------------------------------------------------------------------------


def _items_service(session: DatabaseSession) -> AdminCrudService[ItemResponse]:
    return AdminCrudService(ADMIN_RESOURCES_BY_NAME["items"], AdminRepository(session))


@router.post("/items", response_model=ItemResponse)
async def create_item(body: ItemCreateRequest, session: DatabaseSession) -> ItemResponse:
    return await _items_service(session).create_resource(body)


@router.post("/items/bulk", response_model=BulkCreateResponse[ItemResponse])
async def create_items_bulk(
    body: BulkCreateRequest[ItemCreateRequest], session: DatabaseSession
) -> BulkCreateResponse[ItemResponse]:
    created = await _items_service(session).create_resources(body.items)
    return BulkCreateResponse(created=created)


@router.get("/items", response_model=list[ItemResponse])
async def list_items(
    session: DatabaseSession, limit: Limit = 50, offset: Offset = 0
) -> list[ItemResponse]:
    return await _items_service(session).list_resources(limit=limit, offset=offset)


@router.get("/items/{item_id}", response_model=ItemResponse)
async def get_item(item_id: str, session: DatabaseSession) -> ItemResponse:
    return await _items_service(session).get_resource(item_id)


@router.patch("/items/{item_id}", response_model=ItemResponse)
async def update_item(
    item_id: str, body: ItemUpdateRequest, session: DatabaseSession
) -> ItemResponse:
    return await _items_service(session).update_resource(item_id, body)


@router.delete("/items/{item_id}", status_code=204)
async def delete_item(item_id: str, session: DatabaseSession) -> None:
    await _items_service(session).delete_resource(item_id)


# --- recipes ------------------------------------------------------------------------


def _recipes_service(session: DatabaseSession) -> AdminCrudService[RecipeResponse]:
    return AdminCrudService(ADMIN_RESOURCES_BY_NAME["recipes"], AdminRepository(session))


@router.post("/recipes", response_model=RecipeResponse, response_model_by_alias=False)
async def create_recipe(body: RecipeCreateRequest, session: DatabaseSession) -> RecipeResponse:
    return await _recipes_service(session).create_resource(body)


@router.post(
    "/recipes/bulk",
    response_model=BulkCreateResponse[RecipeResponse],
    response_model_by_alias=False,
)
async def create_recipes_bulk(
    body: BulkCreateRequest[RecipeCreateRequest], session: DatabaseSession
) -> BulkCreateResponse[RecipeResponse]:
    created = await _recipes_service(session).create_resources(body.items)
    return BulkCreateResponse(created=created)


@router.get("/recipes", response_model=list[RecipeResponse], response_model_by_alias=False)
async def list_recipes(
    session: DatabaseSession, limit: Limit = 50, offset: Offset = 0
) -> list[RecipeResponse]:
    return await _recipes_service(session).list_resources(limit=limit, offset=offset)


@router.get(
    "/recipes/{recipe_id}", response_model=RecipeResponse, response_model_by_alias=False
)
async def get_recipe(recipe_id: str, session: DatabaseSession) -> RecipeResponse:
    return await _recipes_service(session).get_resource(recipe_id)


@router.patch(
    "/recipes/{recipe_id}", response_model=RecipeResponse, response_model_by_alias=False
)
async def update_recipe(
    recipe_id: str, body: RecipeUpdateRequest, session: DatabaseSession
) -> RecipeResponse:
    return await _recipes_service(session).update_resource(recipe_id, body)


@router.delete("/recipes/{recipe_id}", status_code=204)
async def delete_recipe(recipe_id: str, session: DatabaseSession) -> None:
    await _recipes_service(session).delete_resource(recipe_id)


# --- smelting-recipes ----------------------------------------------------------------


def _smelting_recipes_service(
    session: DatabaseSession,
) -> AdminCrudService[SmeltingRecipeResponse]:
    return AdminCrudService(ADMIN_RESOURCES_BY_NAME["smelting-recipes"], AdminRepository(session))


@router.post(
    "/smelting-recipes",
    response_model=SmeltingRecipeResponse,
    response_model_by_alias=False,
)
async def create_smelting_recipe(
    body: SmeltingRecipeCreateRequest, session: DatabaseSession
) -> SmeltingRecipeResponse:
    return await _smelting_recipes_service(session).create_resource(body)


@router.post(
    "/smelting-recipes/bulk",
    response_model=BulkCreateResponse[SmeltingRecipeResponse],
    response_model_by_alias=False,
)
async def create_smelting_recipes_bulk(
    body: BulkCreateRequest[SmeltingRecipeCreateRequest], session: DatabaseSession
) -> BulkCreateResponse[SmeltingRecipeResponse]:
    created = await _smelting_recipes_service(session).create_resources(body.items)
    return BulkCreateResponse(created=created)


@router.get(
    "/smelting-recipes",
    response_model=list[SmeltingRecipeResponse],
    response_model_by_alias=False,
)
async def list_smelting_recipes(
    session: DatabaseSession, limit: Limit = 50, offset: Offset = 0
) -> list[SmeltingRecipeResponse]:
    return await _smelting_recipes_service(session).list_resources(limit=limit, offset=offset)


@router.get(
    "/smelting-recipes/{smelt_id}",
    response_model=SmeltingRecipeResponse,
    response_model_by_alias=False,
)
async def get_smelting_recipe(smelt_id: str, session: DatabaseSession) -> SmeltingRecipeResponse:
    return await _smelting_recipes_service(session).get_resource(smelt_id)


@router.patch(
    "/smelting-recipes/{smelt_id}",
    response_model=SmeltingRecipeResponse,
    response_model_by_alias=False,
)
async def update_smelting_recipe(
    smelt_id: str, body: SmeltingRecipeUpdateRequest, session: DatabaseSession
) -> SmeltingRecipeResponse:
    return await _smelting_recipes_service(session).update_resource(smelt_id, body)


@router.delete("/smelting-recipes/{smelt_id}", status_code=204)
async def delete_smelting_recipe(smelt_id: str, session: DatabaseSession) -> None:
    await _smelting_recipes_service(session).delete_resource(smelt_id)


# --- enemies ------------------------------------------------------------------------


def _enemies_service(session: DatabaseSession) -> AdminCrudService[EnemyResponse]:
    return AdminCrudService(ADMIN_RESOURCES_BY_NAME["enemies"], AdminRepository(session))


@router.post("/enemies", response_model=EnemyResponse)
async def create_enemy(body: EnemyCreateRequest, session: DatabaseSession) -> EnemyResponse:
    return await _enemies_service(session).create_resource(body)


@router.post("/enemies/bulk", response_model=BulkCreateResponse[EnemyResponse])
async def create_enemies_bulk(
    body: BulkCreateRequest[EnemyCreateRequest], session: DatabaseSession
) -> BulkCreateResponse[EnemyResponse]:
    created = await _enemies_service(session).create_resources(body.items)
    return BulkCreateResponse(created=created)


@router.get("/enemies", response_model=list[EnemyResponse])
async def list_enemies(
    session: DatabaseSession, limit: Limit = 50, offset: Offset = 0
) -> list[EnemyResponse]:
    return await _enemies_service(session).list_resources(limit=limit, offset=offset)


@router.get("/enemies/{enemy_id}", response_model=EnemyResponse)
async def get_enemy(enemy_id: str, session: DatabaseSession) -> EnemyResponse:
    return await _enemies_service(session).get_resource(enemy_id)


@router.patch("/enemies/{enemy_id}", response_model=EnemyResponse)
async def update_enemy(
    enemy_id: str, body: EnemyUpdateRequest, session: DatabaseSession
) -> EnemyResponse:
    return await _enemies_service(session).update_resource(enemy_id, body)


@router.delete("/enemies/{enemy_id}", status_code=204)
async def delete_enemy(enemy_id: str, session: DatabaseSession) -> None:
    await _enemies_service(session).delete_resource(enemy_id)


# --- locations ----------------------------------------------------------------------


def _locations_service(session: DatabaseSession) -> AdminCrudService[LocationResponse]:
    return AdminCrudService(ADMIN_RESOURCES_BY_NAME["locations"], AdminRepository(session))


@router.post("/locations", response_model=LocationResponse, response_model_by_alias=False)
async def create_location(
    body: LocationCreateRequest, session: DatabaseSession
) -> LocationResponse:
    return await _locations_service(session).create_resource(body)


@router.post(
    "/locations/bulk",
    response_model=BulkCreateResponse[LocationResponse],
    response_model_by_alias=False,
)
async def create_locations_bulk(
    body: BulkCreateRequest[LocationCreateRequest], session: DatabaseSession
) -> BulkCreateResponse[LocationResponse]:
    created = await _locations_service(session).create_resources(body.items)
    return BulkCreateResponse(created=created)


@router.get(
    "/locations", response_model=list[LocationResponse], response_model_by_alias=False
)
async def list_locations(
    session: DatabaseSession, limit: Limit = 50, offset: Offset = 0
) -> list[LocationResponse]:
    return await _locations_service(session).list_resources(limit=limit, offset=offset)


@router.get(
    "/locations/{location_id}",
    response_model=LocationResponse,
    response_model_by_alias=False,
)
async def get_location(location_id: str, session: DatabaseSession) -> LocationResponse:
    return await _locations_service(session).get_resource(location_id)


@router.patch(
    "/locations/{location_id}",
    response_model=LocationResponse,
    response_model_by_alias=False,
)
async def update_location(
    location_id: str, body: LocationUpdateRequest, session: DatabaseSession
) -> LocationResponse:
    return await _locations_service(session).update_resource(location_id, body)


@router.delete("/locations/{location_id}", status_code=204)
async def delete_location(location_id: str, session: DatabaseSession) -> None:
    await _locations_service(session).delete_resource(location_id)


# --- episodic-memories ----------------------------------------------------------------


def _episodic_memories_service(
    session: DatabaseSession,
) -> AdminCrudService[EpisodicMemoryResponse]:
    return AdminCrudService(
        ADMIN_RESOURCES_BY_NAME["episodic-memories"], AdminRepository(session)
    )


@router.post("/episodic-memories", response_model=EpisodicMemoryResponse)
async def create_episodic_memory(
    body: EpisodicMemoryCreateRequest, session: DatabaseSession
) -> EpisodicMemoryResponse:
    return await _episodic_memories_service(session).create_resource(body)


@router.post("/episodic-memories/bulk", response_model=BulkCreateResponse[EpisodicMemoryResponse])
async def create_episodic_memories_bulk(
    body: BulkCreateRequest[EpisodicMemoryCreateRequest], session: DatabaseSession
) -> BulkCreateResponse[EpisodicMemoryResponse]:
    created = await _episodic_memories_service(session).create_resources(body.items)
    return BulkCreateResponse(created=created)


@router.get("/episodic-memories", response_model=list[EpisodicMemoryResponse])
async def list_episodic_memories(
    session: DatabaseSession, limit: Limit = 50, offset: Offset = 0
) -> list[EpisodicMemoryResponse]:
    return await _episodic_memories_service(session).list_resources(limit=limit, offset=offset)


@router.get("/episodic-memories/{row_id}", response_model=EpisodicMemoryResponse)
async def get_episodic_memory(row_id: str, session: DatabaseSession) -> EpisodicMemoryResponse:
    return await _episodic_memories_service(session).get_resource(row_id)


@router.patch("/episodic-memories/{row_id}", response_model=EpisodicMemoryResponse)
async def update_episodic_memory(
    row_id: str, body: EpisodicMemoryUpdateRequest, session: DatabaseSession
) -> EpisodicMemoryResponse:
    return await _episodic_memories_service(session).update_resource(row_id, body)


@router.delete("/episodic-memories/{row_id}", status_code=204)
async def delete_episodic_memory(row_id: str, session: DatabaseSession) -> None:
    await _episodic_memories_service(session).delete_resource(row_id)


# --- offline-tasks --------------------------------------------------------------------


def _offline_tasks_service(session: DatabaseSession) -> AdminCrudService[AdminOfflineTaskResponse]:
    return AdminCrudService(ADMIN_RESOURCES_BY_NAME["offline-tasks"], AdminRepository(session))


@router.post("/offline-tasks", response_model=AdminOfflineTaskResponse)
async def create_offline_task_admin(
    body: AdminOfflineTaskCreateRequest, session: DatabaseSession
) -> AdminOfflineTaskResponse:
    return await _offline_tasks_service(session).create_resource(body)


@router.post("/offline-tasks/bulk", response_model=BulkCreateResponse[AdminOfflineTaskResponse])
async def create_offline_tasks_bulk(
    body: BulkCreateRequest[AdminOfflineTaskCreateRequest], session: DatabaseSession
) -> BulkCreateResponse[AdminOfflineTaskResponse]:
    created = await _offline_tasks_service(session).create_resources(body.items)
    return BulkCreateResponse(created=created)


@router.get("/offline-tasks", response_model=list[AdminOfflineTaskResponse])
async def list_offline_tasks_admin(
    session: DatabaseSession, limit: Limit = 50, offset: Offset = 0
) -> list[AdminOfflineTaskResponse]:
    return await _offline_tasks_service(session).list_resources(limit=limit, offset=offset)


@router.get("/offline-tasks/{task_id}", response_model=AdminOfflineTaskResponse)
async def get_offline_task_admin(
    task_id: str, session: DatabaseSession
) -> AdminOfflineTaskResponse:
    return await _offline_tasks_service(session).get_resource(task_id)


@router.patch("/offline-tasks/{task_id}", response_model=AdminOfflineTaskResponse)
async def update_offline_task_admin(
    task_id: str, body: AdminOfflineTaskUpdateRequest, session: DatabaseSession
) -> AdminOfflineTaskResponse:
    return await _offline_tasks_service(session).update_resource(task_id, body)


@router.delete("/offline-tasks/{task_id}", status_code=204)
async def delete_offline_task_admin(task_id: str, session: DatabaseSession) -> None:
    await _offline_tasks_service(session).delete_resource(task_id)
