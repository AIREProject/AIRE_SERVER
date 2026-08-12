"""관리자 CRUD 삭제 — 자식 행이 남아 있으면 거부(§4).

두 경로를 각각 검증한다: DB 에 `ForeignKey` 로 선언된 관계는 `PRAGMA foreign_keys=ON` 이
`IntegrityError` 로 막고, `items → recipes/smelting_recipes.result_item_id` 처럼 FK 가 아닌
관계는 `AdminCrudService.delete_resource` 가 `count_by_fk` 로 미리 확인한다. 두 경로 다
같은 409 `AdminChildReferenceExists` 로 통일된다.
"""

from typing import Any

import pytest
from fastapi.testclient import TestClient

from app.main import create_app
from tests.conftest import make_database, make_settings

ADMIN_TOKEN = "admin-secret-for-tests"
HEADERS = {"Authorization": f"Bearer {ADMIN_TOKEN}"}


@pytest.fixture
async def client() -> Any:
    settings = make_settings(llm_provider="mock", admin_api_token=ADMIN_TOKEN)
    await make_database(settings)
    with TestClient(create_app(settings)) as test_client:
        yield test_client


def test_deleting_profile_with_device_is_rejected(client: Any) -> None:
    profile = client.post(
        "/api/v1/admin/profiles", headers=HEADERS, json={"profile_id": "profile-fk"}
    )
    assert profile.status_code == 200
    device = client.post(
        "/api/v1/admin/devices",
        headers=HEADERS,
        json={
            "device_id": "device-fk",
            "profile_id": "profile-fk",
            "role": "WebClient",
            "creation_request_id": "req-fk",
        },
    )
    assert device.status_code == 200

    rejected = client.delete("/api/v1/admin/profiles/profile-fk", headers=HEADERS)
    assert rejected.status_code == 409
    assert rejected.json()["error"]["code"] == "AdminChildReferenceExists"

    assert client.delete("/api/v1/admin/devices/device-fk", headers=HEADERS).status_code == 204
    allowed = client.delete("/api/v1/admin/profiles/profile-fk", headers=HEADERS)
    assert allowed.status_code == 204


def test_deleting_item_referenced_by_recipe_is_rejected(client: Any) -> None:
    item = client.post(
        "/api/v1/admin/items",
        headers=HEADERS,
        json={
            "item_id": "item-nonfk",
            "item_type": "Material",
            "name_ko": "아이템",
            "aliases": [],
            "description": "설명",
        },
    )
    assert item.status_code == 200
    recipe = client.post(
        "/api/v1/admin/recipes",
        headers=HEADERS,
        json={
            "recipe_id": "recipe-nonfk",
            "result_item_id": "item-nonfk",
            "result_amount": 1,
            "required_workbench": "Basic Workbench",
            "duration_seconds": 1.0,
            "ingredients": [{"item_id": "item-nonfk", "amount": 1}],
        },
    )
    assert recipe.status_code == 200

    rejected = client.delete("/api/v1/admin/items/item-nonfk", headers=HEADERS)
    assert rejected.status_code == 409
    assert rejected.json()["error"]["code"] == "AdminChildReferenceExists"

    assert (
        client.delete("/api/v1/admin/recipes/recipe-nonfk", headers=HEADERS).status_code == 204
    )
    allowed = client.delete("/api/v1/admin/items/item-nonfk", headers=HEADERS)
    assert allowed.status_code == 204


def test_deleting_item_referenced_by_smelting_recipe_is_rejected(client: Any) -> None:
    item = client.post(
        "/api/v1/admin/items",
        headers=HEADERS,
        json={
            "item_id": "item-smelt",
            "item_type": "Material",
            "name_ko": "아이템",
            "aliases": [],
            "description": "설명",
        },
    )
    assert item.status_code == 200
    smelting = client.post(
        "/api/v1/admin/smelting-recipes",
        headers=HEADERS,
        json={
            "smelt_id": "smelt-nonfk",
            "result_item_id": "item-smelt",
            "result_amount": 1,
            "required_workbench": "Workbench.BlastFurnace",
            "duration_seconds": 1.0,
            "input_item": {"item_id": "item-smelt", "amount": 1},
            "fuel": {"item_id": "item-smelt", "amount": 1},
        },
    )
    assert smelting.status_code == 200

    rejected = client.delete("/api/v1/admin/items/item-smelt", headers=HEADERS)
    assert rejected.status_code == 409
    assert rejected.json()["error"]["code"] == "AdminChildReferenceExists"

    assert (
        client.delete("/api/v1/admin/smelting-recipes/smelt-nonfk", headers=HEADERS).status_code
        == 204
    )
    allowed = client.delete("/api/v1/admin/items/item-smelt", headers=HEADERS)
    assert allowed.status_code == 204
