"""관리자 CRUD와 Offline Task 정책 운영 경계 검증.

`AdminCrudService`(app/admin_service.py)는 테이블 이름을 모르는 공용 엔진이므로, 여기서는
그 엔진이 각 테이블의 스펙(app/admin_registry.py)과 스키마(app/admin_models.py)를 통해
실제로 올바르게 동작하는지 — 특히 JSON 컬럼의 별칭 왕복(recipes/smelting_recipes/enemies/
locations)과 devices 의 자격 증명 sentinel 채움을 — 검증한다.
"""

from datetime import UTC, datetime
from typing import Any

import pytest
from fastapi.testclient import TestClient

from app.db.models import (
    DeviceModel,
    ItemModel,
    OfflineTaskPolicyModel,
    ProfileModel,
    SaveSlotModel,
)
from app.main import create_app
from tests.conftest import make_database, make_settings

ADMIN_TOKEN = "admin-secret-for-tests"
HEADERS = {"Authorization": f"Bearer {ADMIN_TOKEN}"}


@pytest.fixture
async def seeded() -> Any:
    """관리자 클라이언트 + FK 를 만족시킬 기본 profile/device/save_slot/item."""

    settings = make_settings(llm_provider="mock", admin_api_token=ADMIN_TOKEN)
    database = await make_database(settings)
    now = datetime.now(UTC)
    async with database.session_factory() as session:
        session.add(ProfileModel(profile_id="seed-profile", created_at=now))
        await session.flush()
        session.add(
            DeviceModel(
                device_id="seed-device",
                profile_id="seed-profile",
                role="GameClient",
                token_lookup_id="seed-lookup",
                token_hash="0" * 64,
                creation_request_id="seed-device-req",
                game_registration_key="seed-profile",
                created_at=now,
            )
        )
        session.add(
            SaveSlotModel(
                row_id="seed-slot-row",
                save_slot_id="slot-1",
                profile_id="seed-profile",
                created_at=now,
            )
        )
        session.add(
            ItemModel(
                item_id="SeedItem",
                item_type="Material",
                name_ko="씨앗",
                aliases=[],
                description="테스트용 아이템.",
            )
        )
        session.add_all(
            [
                ItemModel(
                    item_id="PlantStem",
                    item_type="Material",
                    name_ko="나무",
                    aliases=[],
                    description="Offline Task 정책 테스트용 아이템.",
                ),
                ItemModel(
                    item_id="ShoddyBandage",
                    item_type="Consumable",
                    name_ko="엉성한 붕대",
                    aliases=[],
                    description="Offline Task 정책 테스트용 아이템.",
                ),
            ]
        )
        await session.flush()
        session.add_all(
            [
                OfflineTaskPolicyModel(
                    policy_id="gathering-plant-stem",
                    task_type="Gathering",
                    item_id="PlantStem",
                    seconds_per_item=5.0,
                ),
                OfflineTaskPolicyModel(
                    policy_id="crafting-shoddy-bandage",
                    task_type="Crafting",
                    item_id="ShoddyBandage",
                    seconds_per_item=10.0,
                ),
            ]
        )
        await session.commit()
    with TestClient(create_app(settings)) as client:
        yield client, {
            "profile_id": "seed-profile",
            "device_id": "seed-device",
            "save_slot_row_id": "seed-slot-row",
            "item_id": "SeedItem",
        }


def test_profile_crud_cycle(seeded: Any) -> None:
    client, _ids = seeded
    created = client.post(
        "/api/v1/admin/profiles", headers=HEADERS, json={"profile_id": "p1"}
    )
    assert created.status_code == 200
    assert created.json()["profile_id"] == "p1"

    listed = client.get("/api/v1/admin/profiles", headers=HEADERS)
    assert any(row["profile_id"] == "p1" for row in listed.json())

    updated = client.patch(
        "/api/v1/admin/profiles/p1",
        headers=HEADERS,
        json={"created_at": "2020-01-01T00:00:00Z"},
    )
    assert updated.status_code == 200
    assert updated.json()["created_at"].startswith("2020-01-01")

    deleted = client.delete("/api/v1/admin/profiles/p1", headers=HEADERS)
    assert deleted.status_code == 204
    assert client.get("/api/v1/admin/profiles/p1", headers=HEADERS).status_code == 404


def test_profile_duplicate_primary_key_is_conflict(seeded: Any) -> None:
    client, _ids = seeded
    payload = {"profile_id": "dup-profile"}
    first = client.post("/api/v1/admin/profiles", headers=HEADERS, json=payload)
    assert first.status_code == 200
    second = client.post("/api/v1/admin/profiles", headers=HEADERS, json=payload)
    assert second.status_code == 409
    assert second.json()["error"]["code"] == "AdminDuplicateKey"


def test_device_crud_cycle(seeded: Any) -> None:
    client, ids = seeded
    created = client.post(
        "/api/v1/admin/devices",
        headers=HEADERS,
        json={
            "device_id": "d1",
            "profile_id": ids["profile_id"],
            "role": "WebClient",
            "creation_request_id": "req-d1",
        },
    )
    assert created.status_code == 200
    body = created.json()
    assert body["device_id"] == "d1"
    assert "token_hash" not in body
    assert "token_lookup_id" not in body

    updated = client.patch(
        "/api/v1/admin/devices/d1", headers=HEADERS, json={"role": "GameClient"}
    )
    assert updated.status_code == 200
    assert updated.json()["role"] == "GameClient"

    deleted = client.delete("/api/v1/admin/devices/d1", headers=HEADERS)
    assert deleted.status_code == 204


def test_pairing_code_crud_cycle(seeded: Any) -> None:
    client, ids = seeded
    created = client.post(
        "/api/v1/admin/pairing-codes",
        headers=HEADERS,
        json={
            "pairing_code_id": "pc1",
            "profile_id": ids["profile_id"],
            "issuing_device_id": ids["device_id"],
            "issue_request_id": "req-pc1",
            "expires_at": "2030-01-01T00:00:00Z",
        },
    )
    assert created.status_code == 200
    body = created.json()
    assert "code_hash" not in body

    updated = client.patch(
        "/api/v1/admin/pairing-codes/pc1",
        headers=HEADERS,
        json={"expires_at": "2031-01-01T00:00:00Z"},
    )
    assert updated.status_code == 200
    assert updated.json()["expires_at"].startswith("2031")

    deleted = client.delete("/api/v1/admin/pairing-codes/pc1", headers=HEADERS)
    assert deleted.status_code == 204


def test_save_slot_crud_cycle(seeded: Any) -> None:
    client, ids = seeded
    created = client.post(
        "/api/v1/admin/save-slots",
        headers=HEADERS,
        json={
            "row_id": "slot-row-2",
            "save_slot_id": "slot-2",
            "profile_id": ids["profile_id"],
        },
    )
    assert created.status_code == 200

    updated = client.patch(
        "/api/v1/admin/save-slots/slot-row-2",
        headers=HEADERS,
        json={"save_slot_id": "slot-2b"},
    )
    assert updated.status_code == 200
    assert updated.json()["save_slot_id"] == "slot-2b"

    deleted = client.delete("/api/v1/admin/save-slots/slot-row-2", headers=HEADERS)
    assert deleted.status_code == 204


def test_item_crud_cycle(seeded: Any) -> None:
    client, _ids = seeded
    created = client.post(
        "/api/v1/admin/items",
        headers=HEADERS,
        json={
            "item_id": "Item2",
            "item_type": "Material",
            "name_ko": "아이템2",
            "aliases": ["별칭"],
            "description": "설명",
        },
    )
    assert created.status_code == 200

    updated = client.patch(
        "/api/v1/admin/items/Item2", headers=HEADERS, json={"description": "새 설명"}
    )
    assert updated.status_code == 200
    assert updated.json()["description"] == "새 설명"

    deleted = client.delete("/api/v1/admin/items/Item2", headers=HEADERS)
    assert deleted.status_code == 204


def test_recipe_crud_cycle_round_trips_ingredient_alias(seeded: Any) -> None:
    """`ingredients` 는 DB 에 `ItemId`/`Amount` 로 저장되지만 API 는 `item_id`/`amount` 를 쓴다."""

    client, ids = seeded
    created = client.post(
        "/api/v1/admin/recipes",
        headers=HEADERS,
        json={
            "recipe_id": "r1",
            "result_item_id": ids["item_id"],
            "result_amount": 1,
            "required_workbench": "Basic Workbench",
            "duration_seconds": 1.0,
            "ingredients": [{"item_id": ids["item_id"], "amount": 2}],
        },
    )
    assert created.status_code == 200
    ingredients = created.json()["ingredients"]
    assert ingredients == [{"item_id": ids["item_id"], "amount": 2}]

    updated = client.patch(
        "/api/v1/admin/recipes/r1", headers=HEADERS, json={"duration_seconds": 5.0}
    )
    assert updated.status_code == 200
    assert updated.json()["duration_seconds"] == 5.0

    deleted = client.delete("/api/v1/admin/recipes/r1", headers=HEADERS)
    assert deleted.status_code == 204


def test_smelting_recipe_crud_cycle(seeded: Any) -> None:
    client, ids = seeded
    created = client.post(
        "/api/v1/admin/smelting-recipes",
        headers=HEADERS,
        json={
            "smelt_id": "s1",
            "result_item_id": ids["item_id"],
            "result_amount": 1,
            "required_workbench": "Workbench.BlastFurnace",
            "duration_seconds": 1.0,
            "input_item": {"item_id": ids["item_id"], "amount": 1},
            "fuel": {"item_id": ids["item_id"], "amount": 1},
        },
    )
    assert created.status_code == 200
    assert created.json()["input_item"] == {"item_id": ids["item_id"], "amount": 1}

    deleted = client.delete("/api/v1/admin/smelting-recipes/s1", headers=HEADERS)
    assert deleted.status_code == 204


def test_enemy_crud_cycle(seeded: Any) -> None:
    client, _ids = seeded
    created = client.post(
        "/api/v1/admin/enemies",
        headers=HEADERS,
        json={
            "enemy_id": "e1",
            "name_ko": "적1",
            "aliases": ["별칭"],
            "description": "설명",
            "weakness": {
                "weak_element": "Water",
                "weak_part": "머리",
                "ai_advice": "조언",
            },
        },
    )
    assert created.status_code == 200
    assert created.json()["weakness"]["weak_element"] == "Water"

    deleted = client.delete("/api/v1/admin/enemies/e1", headers=HEADERS)
    assert deleted.status_code == 204


def test_location_crud_cycle_round_trips_coordinates_alias(seeded: Any) -> None:
    """`coordinates` 는 DB 에 `X`/`Y`/`Z` 로 저장되지만 API 는 `x`/`y`/`z` 를 쓴다."""

    client, _ids = seeded
    created = client.post(
        "/api/v1/admin/locations",
        headers=HEADERS,
        json={"location_id": "loc1", "coordinates": {"x": 1.0, "y": 2.0, "z": 3.0}},
    )
    assert created.status_code == 200
    assert created.json()["coordinates"] == {"x": 1.0, "y": 2.0, "z": 3.0}

    deleted = client.delete("/api/v1/admin/locations/loc1", headers=HEADERS)
    assert deleted.status_code == 204


def test_episodic_memory_admin_crud_is_not_exposed(seeded: Any) -> None:
    client, _ids = seeded
    assert client.post("/api/v1/admin/episodic-memories", headers=HEADERS).status_code == 404


def test_offline_task_crud_cycle(seeded: Any) -> None:
    client, ids = seeded
    created = client.post(
        "/api/v1/admin/offline-tasks",
        headers=HEADERS,
        json={
            "task_id": "task1",
            "profile_id": ids["profile_id"],
            "save_slot_row_id": ids["save_slot_row_id"],
            "issuing_device_id": ids["device_id"],
            "item_id": ids["item_id"],
            "task_type": "Gathering",
            "status": "Pending",
            "started_at": "2025-01-01T00:00:00Z",
            "creation_request_id": "req-task1",
        },
    )
    assert created.status_code == 200

    updated = client.patch(
        "/api/v1/admin/offline-tasks/task1", headers=HEADERS, json={"status": "InProgress"}
    )
    assert updated.status_code == 200
    assert updated.json()["status"] == "InProgress"

    deleted = client.delete("/api/v1/admin/offline-tasks/task1", headers=HEADERS)
    assert deleted.status_code == 204


def test_offline_task_policy_list_get_and_update(seeded: Any) -> None:
    client, _ids = seeded

    unauthorized = client.get("/api/v1/admin/offline-task-policies")
    listed = client.get("/api/v1/admin/offline-task-policies", headers=HEADERS)
    fetched = client.get(
        "/api/v1/admin/offline-task-policies/gathering-plant-stem",
        headers=HEADERS,
    )
    updated = client.patch(
        "/api/v1/admin/offline-task-policies/gathering-plant-stem",
        headers=HEADERS,
        json={"seconds_per_item": 7.5},
    )
    invalid = client.patch(
        "/api/v1/admin/offline-task-policies/gathering-plant-stem",
        headers=HEADERS,
        json={"seconds_per_item": 0},
    )

    assert unauthorized.status_code == 401
    assert listed.status_code == 200
    assert {row["policy_id"] for row in listed.json()} == {
        "gathering-plant-stem",
        "crafting-shoddy-bandage",
    }
    assert fetched.status_code == 200
    assert fetched.json()["seconds_per_item"] == 5.0
    assert updated.status_code == 200
    assert updated.json()["seconds_per_item"] == 7.5
    assert invalid.status_code == 400


def test_offline_task_policy_routes_are_exposed_in_openapi(seeded: Any) -> None:
    client, _ids = seeded
    paths = client.get("/openapi.json").json()["paths"]

    assert "get" in paths["/api/v1/admin/offline-task-policies"]
    policy_path = paths["/api/v1/admin/offline-task-policies/{policy_id}"]
    assert {"get", "patch"}.issubset(policy_path)
