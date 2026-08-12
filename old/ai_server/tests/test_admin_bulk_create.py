"""관리자 CRUD 대량 생성(`/bulk`) — 전체 롤백(all-or-nothing) 계약 검증.

`AdminCrudService.create_resources`(app/admin_service.py)는 배치 중 한 건이라도 PK 중복이면
전체를 롤백하고 항목별 결과는 보고하지 않는다. 여기서는 그 계약이 실제로 지켜지는지 — 특히
"실패한 배치의 유효한 항목들도 저장되지 않는다"는, 단순히 에러 코드만 보고는 검증되지 않는
부분을 — 확인한다.
"""

from datetime import UTC, datetime
from typing import Any

import pytest
from fastapi.testclient import TestClient

from app.db.models import DeviceModel, ItemModel, ProfileModel, SaveSlotModel
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
        await session.commit()
    with TestClient(create_app(settings)) as client:
        yield client, {
            "profile_id": "seed-profile",
            "device_id": "seed-device",
            "save_slot_row_id": "seed-slot-row",
            "item_id": "SeedItem",
        }


def test_bulk_create_profiles_all_appear_in_list(seeded: Any) -> None:
    client, _ids = seeded
    created = client.post(
        "/api/v1/admin/profiles/bulk",
        headers=HEADERS,
        json={"items": [{"profile_id": "b1"}, {"profile_id": "b2"}, {"profile_id": "b3"}]},
    )
    assert created.status_code == 200
    body = created.json()["created"]
    assert [row["profile_id"] for row in body] == ["b1", "b2", "b3"]

    listed = client.get("/api/v1/admin/profiles", headers=HEADERS).json()
    listed_ids = {row["profile_id"] for row in listed}
    assert {"b1", "b2", "b3"} <= listed_ids


def test_bulk_create_rolls_back_entirely_on_existing_duplicate(seeded: Any) -> None:
    client, _ids = seeded
    resp = client.post(
        "/api/v1/admin/profiles/bulk",
        headers=HEADERS,
        json={
            "items": [
                {"profile_id": "ok1"},
                {"profile_id": "seed-profile"},  # seeded 픽스처가 이미 만든 행과 충돌
                {"profile_id": "ok2"},
            ]
        },
    )
    assert resp.status_code == 409
    assert resp.json()["error"]["code"] == "AdminDuplicateKey"

    assert client.get("/api/v1/admin/profiles/ok1", headers=HEADERS).status_code == 404
    assert client.get("/api/v1/admin/profiles/ok2", headers=HEADERS).status_code == 404


def test_bulk_create_rolls_back_entirely_on_intra_batch_duplicate(seeded: Any) -> None:
    client, _ids = seeded
    resp = client.post(
        "/api/v1/admin/profiles/bulk",
        headers=HEADERS,
        json={"items": [{"profile_id": "dup1"}, {"profile_id": "dup1"}]},
    )
    assert resp.status_code == 409
    assert resp.json()["error"]["code"] == "AdminDuplicateKey"
    assert client.get("/api/v1/admin/profiles/dup1", headers=HEADERS).status_code == 404


def test_bulk_create_over_max_items_rejected_before_touching_db(seeded: Any) -> None:
    client, _ids = seeded
    items = [{"profile_id": f"over-{i}"} for i in range(101)]
    resp = client.post("/api/v1/admin/profiles/bulk", headers=HEADERS, json={"items": items})
    assert resp.status_code == 400

    listed = client.get("/api/v1/admin/profiles", headers=HEADERS).json()
    assert not any(row["profile_id"].startswith("over-") for row in listed)


def test_bulk_create_empty_items_rejected(seeded: Any) -> None:
    client, _ids = seeded
    resp = client.post("/api/v1/admin/profiles/bulk", headers=HEADERS, json={"items": []})
    assert resp.status_code == 400


def test_bulk_create_recipes_round_trips_ingredient_alias(seeded: Any) -> None:
    client, ids = seeded
    created = client.post(
        "/api/v1/admin/recipes/bulk",
        headers=HEADERS,
        json={
            "items": [
                {
                    "recipe_id": "br1",
                    "result_item_id": ids["item_id"],
                    "result_amount": 1,
                    "required_workbench": "Basic Workbench",
                    "duration_seconds": 1.0,
                    "ingredients": [{"item_id": ids["item_id"], "amount": 2}],
                },
                {
                    "recipe_id": "br2",
                    "result_item_id": ids["item_id"],
                    "result_amount": 3,
                    "required_workbench": "Basic Workbench",
                    "duration_seconds": 2.0,
                    "ingredients": [{"item_id": ids["item_id"], "amount": 4}],
                },
            ]
        },
    )
    assert created.status_code == 200
    body = created.json()["created"]
    assert body[0]["ingredients"] == [{"item_id": ids["item_id"], "amount": 2}]
    assert body[1]["ingredients"] == [{"item_id": ids["item_id"], "amount": 4}]
