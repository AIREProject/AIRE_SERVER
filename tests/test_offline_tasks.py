"""모바일 작업 지시와 게임 클라이언트 상태 전이 API 검증."""

from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr

from app.credentials import CredentialProtector
from app.db.models import (
    DeviceModel,
    ItemModel,
    OfflineTaskModel,
    OfflineTaskPolicyModel,
    ProfileModel,
)
from app.identity import DeviceRole
from app.main import create_app
from tests.conftest import make_authenticated_device, make_database, make_settings

PROTECTOR = CredentialProtector(SecretStr("test-only-pepper-not-for-production"))


async def _make_device(
    database: Any,
    *,
    profile_id: str,
    role: DeviceRole,
) -> str:
    device_id = f"device-{uuid4()}"
    lookup_id = f"token-{uuid4()}"
    request_id = f"request-{uuid4()}"
    now = datetime.now(UTC)
    token = PROTECTOR.make_device_token(
        lookup_id=lookup_id,
        device_id=device_id,
        creation_request_id=request_id,
    )
    async with database.session_factory() as session:
        if await session.get(ProfileModel, profile_id) is None:
            session.add(ProfileModel(profile_id=profile_id, created_at=now))
            await session.flush()
        session.add(
            DeviceModel(
                device_id=device_id,
                profile_id=profile_id,
                role=role.value,
                token_lookup_id=lookup_id,
                token_hash=PROTECTOR.hash_value("device-token", token),
                creation_request_id=request_id,
                game_registration_key=(
                    profile_id if role is DeviceRole.GAME_CLIENT else None
                ),
                created_at=now,
                last_used_at=None,
                revoked_at=None,
            )
        )
        await session.commit()
    return token


@pytest.fixture
async def task_clients() -> Any:
    settings = make_settings(
        llm_provider="mock",
        admin_api_token="admin-secret-for-tests",
    )
    database = await make_database(settings)
    game_identity, game_token = await make_authenticated_device(database, PROTECTOR)
    web_token = await _make_device(
        database, profile_id=game_identity.profile_id, role=DeviceRole.WEB_CLIENT
    )
    other_identity, other_game_token = await make_authenticated_device(
        database, PROTECTOR, role=DeviceRole.WEB_CLIENT
    )
    async with database.session_factory() as session:
        session.add_all(
            [
                ItemModel(
                    item_id="Branch",
                    item_type="Material",
                    name_ko="나뭇가지",
                    aliases=["나뭇가지"],
                    description="나무에서 떨어진 가지.",
                ),
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
        yield {
            "client": client,
            "database": database,
            "game_token": game_token,
            "web_token": web_token,
            "other_game_token": other_game_token,
            "profile_id": game_identity.profile_id,
            "other_profile_id": other_identity.profile_id,
            "admin_headers": {
                "Authorization": "Bearer admin-secret-for-tests",
            },
        }


def _headers(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


async def _force_task_state(
    database: Any,
    task_id: str,
    *,
    status: str,
    quantity: int | None,
    started_at: datetime,
) -> None:
    """채팅으로 자동 시작된 작업을 흉내내기 위해 DB 행을 직접 조작한다.

    실제 채팅 경로(`app/service.py:_create_gather_task`)와 같은 결과 상태를 시간을
    기다리지 않고 만들어야, 경과시간 역산 로직을 sleep 없이 검증할 수 있다.
    """

    async with database.session_factory() as session:
        task = await session.get(OfflineTaskModel, task_id)
        assert task is not None
        task.status = status
        task.quantity = quantity
        task.started_at = started_at
        await session.commit()


def _create_gathering_task(client: TestClient, web_headers: dict[str, str], request_id: str) -> str:
    created = client.post(
        "/api/v1/tasks",
        headers=web_headers,
        json={
            "request_id": request_id,
            "save_slot_id": "slot-a",
            "task_type": "Gathering",
            "item_id": "Branch",
        },
    )
    assert created.status_code == 200
    task_id: str = created.json()["task"]["task_id"]
    return task_id


def test_web_creates_and_game_advances_task(task_clients: Any) -> None:
    client = task_clients["client"]
    web_headers = _headers(task_clients["web_token"])
    game_headers = _headers(task_clients["game_token"])

    created = client.post(
        "/api/v1/tasks",
        headers=web_headers,
        json={
            "request_id": "task-create-1",
            "save_slot_id": "slot-a",
            "task_type": "Gathering",
            "item_id": "Branch",
        },
    )

    assert created.status_code == 200
    task = created.json()["task"]
    task_id = task["task_id"]
    assert task["status"] == "Pending"
    assert task["save_slot_id"] == "slot-a"

    started = client.post(f"/api/v1/tasks/{task_id}/start", headers=game_headers)
    completed = client.post(f"/api/v1/tasks/{task_id}/complete", headers=game_headers)
    claimed = client.post(f"/api/v1/tasks/{task_id}/claim", headers=game_headers)

    assert started.status_code == 200
    assert started.json()["task"]["status"] == "InProgress"
    assert completed.status_code == 200
    assert completed.json()["task"]["status"] == "Completed"
    assert claimed.status_code == 200
    assert claimed.json()["task"]["status"] == "Claimed"


def test_task_creation_is_idempotent_for_same_scope(task_clients: Any) -> None:
    client = task_clients["client"]
    body = {
        "request_id": "task-create-idempotent",
        "save_slot_id": "slot-a",
        "task_type": "Scouting",
    }

    first = client.post(
        "/api/v1/tasks", headers=_headers(task_clients["web_token"]), json=body
    )
    second = client.post(
        "/api/v1/tasks", headers=_headers(task_clients["web_token"]), json=body
    )

    assert first.status_code == 200
    assert second.status_code == 200
    assert second.json()["task"]["task_id"] == first.json()["task"]["task_id"]


async def test_quantity_task_starts_immediately_and_reports_live_progress(
    task_clients: Any,
) -> None:
    client = task_clients["client"]
    web_headers = _headers(task_clients["web_token"])
    created = client.post(
        "/api/v1/tasks",
        headers=web_headers,
        json={
            "request_id": "task-live-progress",
            "save_slot_id": "slot-a",
            "task_type": "Gathering",
            "item_id": "PlantStem",
            "quantity": 10,
        },
    )

    assert created.status_code == 200
    task = created.json()["task"]
    assert task["status"] == "InProgress"
    assert task["progress_quantity"] == 0
    task_id = task["task_id"]

    await _force_task_state(
        task_clients["database"],
        task_id,
        status="InProgress",
        quantity=10,
        started_at=datetime.now(UTC) - timedelta(seconds=12),
    )
    listed = client.get(
        "/api/v1/tasks",
        headers=web_headers,
        params={"save_slot_id": "slot-a"},
    )

    assert listed.status_code == 200
    live_task = next(
        value for value in listed.json()["tasks"] if value["task_id"] == task_id
    )
    assert live_task["status"] == "InProgress"
    assert live_task["progress_quantity"] == 2
    assert live_task["result_quantity"] is None


def test_web_deletes_owned_pending_and_in_progress_tasks(task_clients: Any) -> None:
    client = task_clients["client"]
    web_headers = _headers(task_clients["web_token"])
    game_headers = _headers(task_clients["game_token"])

    pending_id = _create_gathering_task(client, web_headers, "task-delete-pending")
    in_progress_id = _create_gathering_task(
        client, web_headers, "task-delete-in-progress"
    )
    started = client.post(f"/api/v1/tasks/{in_progress_id}/start", headers=game_headers)
    assert started.status_code == 200

    pending_delete = client.delete(
        f"/api/v1/tasks/{pending_id}",
        headers={**web_headers, "X-Request-ID": "task-delete-pending-request"},
    )
    in_progress_delete = client.delete(
        f"/api/v1/tasks/{in_progress_id}",
        headers={**web_headers, "X-Request-ID": "task-delete-progress-request"},
    )

    assert pending_delete.status_code == 204
    assert pending_delete.headers["X-Request-ID"] == "task-delete-pending-request"
    assert in_progress_delete.status_code == 204
    listed = client.get(
        "/api/v1/tasks",
        headers=web_headers,
        params={"save_slot_id": "slot-a"},
    )
    assert listed.status_code == 200
    assert listed.json()["tasks"] == []


def test_task_delete_enforces_role_owner_and_terminal_state(task_clients: Any) -> None:
    client = task_clients["client"]
    web_headers = _headers(task_clients["web_token"])
    game_headers = _headers(task_clients["game_token"])
    other_headers = _headers(task_clients["other_game_token"])
    task_id = _create_gathering_task(client, web_headers, "task-delete-guards")

    assert client.delete(f"/api/v1/tasks/{task_id}", headers=game_headers).status_code == 403
    assert client.delete(f"/api/v1/tasks/{task_id}", headers=other_headers).status_code == 404

    assert client.post(f"/api/v1/tasks/{task_id}/start", headers=game_headers).status_code == 200
    assert client.post(f"/api/v1/tasks/{task_id}/complete", headers=game_headers).status_code == 200
    terminal_delete = client.delete(f"/api/v1/tasks/{task_id}", headers=web_headers)

    assert terminal_delete.status_code == 409
    listed = client.get(
        "/api/v1/tasks",
        headers=web_headers,
        params={"save_slot_id": "slot-a"},
    )
    assert listed.status_code == 200
    assert [task["task_id"] for task in listed.json()["tasks"]] == [task_id]


def test_roles_and_transitions_are_enforced(task_clients: Any) -> None:
    client = task_clients["client"]
    created = client.post(
        "/api/v1/tasks",
        headers=_headers(task_clients["web_token"]),
        json={
            "request_id": "task-role-1",
            "save_slot_id": "slot-a",
            "task_type": "Scouting",
        },
    )
    task_id = created.json()["task"]["task_id"]

    web_start = client.post(
        f"/api/v1/tasks/{task_id}/start", headers=_headers(task_clients["web_token"])
    )
    game_complete = client.post(
        f"/api/v1/tasks/{task_id}/complete", headers=_headers(task_clients["game_token"])
    )
    game_start = client.post(
        f"/api/v1/tasks/{task_id}/start", headers=_headers(task_clients["game_token"])
    )
    duplicate_start = client.post(
        f"/api/v1/tasks/{task_id}/start", headers=_headers(task_clients["game_token"])
    )

    assert web_start.status_code == 403
    assert game_complete.status_code == 409
    assert game_start.status_code == 200
    assert duplicate_start.status_code == 409


def test_tasks_are_scoped_to_profile_and_save_slot(task_clients: Any) -> None:
    client = task_clients["client"]
    created = client.post(
        "/api/v1/tasks",
        headers=_headers(task_clients["web_token"]),
        json={
            "request_id": "task-scope-1",
            "save_slot_id": "slot-a",
            "task_type": "Scouting",
        },
    )
    assert created.status_code == 200

    same_profile_other_slot = client.get(
        "/api/v1/tasks",
        headers=_headers(task_clients["game_token"]),
        params={"save_slot_id": "slot-b"},
    )
    other_profile = client.get(
        "/api/v1/tasks",
        headers=_headers(task_clients["other_game_token"]),
        params={"save_slot_id": "slot-a"},
    )

    assert same_profile_other_slot.status_code == 200
    assert same_profile_other_slot.json()["tasks"] == []
    assert other_profile.status_code == 200
    assert other_profile.json()["tasks"] == []


def test_gathering_and_crafting_require_a_known_item(task_clients: Any) -> None:
    client = task_clients["client"]
    missing_item = client.post(
        "/api/v1/tasks",
        headers=_headers(task_clients["web_token"]),
        json={
            "request_id": "task-invalid-1",
            "save_slot_id": "slot-a",
            "task_type": "Gathering",
        },
    )
    unknown_item = client.post(
        "/api/v1/tasks",
        headers=_headers(task_clients["web_token"]),
        json={
            "request_id": "task-invalid-2",
            "save_slot_id": "slot-a",
            "task_type": "Crafting",
            "item_id": "NotAnItem",
        },
    )

    assert missing_item.status_code == 400
    assert missing_item.json()["error"]["code"] == "OfflineTaskInvalidRequest"
    assert unknown_item.status_code == 400
    assert unknown_item.json()["error"]["code"] == "OfflineTaskInvalidRequest"


async def test_list_shows_live_progress_without_mutating_status(task_clients: Any) -> None:
    client = task_clients["client"]
    web_headers = _headers(task_clients["web_token"])
    task_id = _create_gathering_task(client, web_headers, "task-progress-1")
    # 25분 경과 → 10분당 1개이므로 2개까지 진행됐어야 한다.
    await _force_task_state(
        task_clients["database"],
        task_id,
        status="InProgress",
        quantity=5,
        started_at=datetime.now(UTC) - timedelta(minutes=25),
    )

    first = client.get("/api/v1/tasks", headers=web_headers, params={"save_slot_id": "slot-a"})
    second = client.get("/api/v1/tasks", headers=web_headers, params={"save_slot_id": "slot-a"})

    for response in (first, second):
        assert response.status_code == 200
        task = next(t for t in response.json()["tasks"] if t["task_id"] == task_id)
        assert task["status"] == "InProgress"
        assert task["progress_quantity"] == 2
        assert task["result_quantity"] is None


async def test_policy_update_affects_only_new_tasks(task_clients: Any) -> None:
    client = task_clients["client"]
    web_headers = _headers(task_clients["web_token"])
    game_headers = _headers(task_clients["game_token"])

    first = client.post(
        "/api/v1/tasks",
        headers=web_headers,
        json={
            "request_id": "task-policy-snapshot-old",
            "save_slot_id": "slot-a",
            "task_type": "Gathering",
            "item_id": "PlantStem",
            "quantity": 10,
        },
    )
    assert first.status_code == 200
    first_task_id = first.json()["task"]["task_id"]

    updated = client.patch(
        "/api/v1/admin/offline-task-policies/gathering-plant-stem",
        headers=task_clients["admin_headers"],
        json={"seconds_per_item": 1.0},
    )
    assert updated.status_code == 200

    second = client.post(
        "/api/v1/tasks",
        headers=web_headers,
        json={
            "request_id": "task-policy-snapshot-new",
            "save_slot_id": "slot-a",
            "task_type": "Gathering",
            "item_id": "PlantStem",
            "quantity": 10,
        },
    )
    assert second.status_code == 200
    second_task_id = second.json()["task"]["task_id"]

    await _force_task_state(
        task_clients["database"],
        first_task_id,
        status="InProgress",
        quantity=10,
        started_at=datetime.now(UTC) - timedelta(seconds=12),
    )
    await _force_task_state(
        task_clients["database"],
        second_task_id,
        status="InProgress",
        quantity=10,
        started_at=datetime.now(UTC) - timedelta(seconds=3),
    )

    first_completed = client.post(
        f"/api/v1/tasks/{first_task_id}/complete",
        headers=game_headers,
    )
    second_completed = client.post(
        f"/api/v1/tasks/{second_task_id}/complete",
        headers=game_headers,
    )

    assert first_completed.status_code == 200
    assert first_completed.json()["task"]["result_quantity"] == 2
    assert second_completed.status_code == 200
    assert second_completed.json()["task"]["result_quantity"] == 3


async def test_mobile_collect_finalizes_partial_progress_immediately(task_clients: Any) -> None:
    client = task_clients["client"]
    web_headers = _headers(task_clients["web_token"])
    task_id = _create_gathering_task(client, web_headers, "task-collect-1")
    # 요청 수량은 50개지만 45분(4개 분량)만 지난 상태 — 다 안 채워도 그 자리에서 확정.
    await _force_task_state(
        task_clients["database"],
        task_id,
        status="InProgress",
        quantity=50,
        started_at=datetime.now(UTC) - timedelta(minutes=45),
    )

    collected = client.post(f"/api/v1/tasks/{task_id}/collect", headers=web_headers)

    assert collected.status_code == 200
    task = collected.json()["task"]
    assert task["status"] == "Completed"
    assert task["result_quantity"] == 4


async def test_first_unit_not_ready_stays_in_progress(task_clients: Any) -> None:
    client = task_clients["client"]
    web_headers = _headers(task_clients["web_token"])
    game_headers = _headers(task_clients["game_token"])
    task_id = _create_gathering_task(client, web_headers, "task-before-first-unit")
    await _force_task_state(
        task_clients["database"],
        task_id,
        status="InProgress",
        quantity=5,
        started_at=datetime.now(UTC) - timedelta(minutes=5),
    )

    completed = client.post(
        f"/api/v1/tasks/{task_id}/complete",
        headers=game_headers,
    )

    assert completed.status_code == 200
    task = completed.json()["task"]
    assert task["status"] == "InProgress"
    assert task["progress_quantity"] == 0
    assert task["result_quantity"] is None


async def test_game_complete_applies_same_elapsed_calc_for_quantity_tasks(
    task_clients: Any,
) -> None:
    client = task_clients["client"]
    web_headers = _headers(task_clients["web_token"])
    game_headers = _headers(task_clients["game_token"])
    task_id = _create_gathering_task(client, web_headers, "task-collect-2")
    await _force_task_state(
        task_clients["database"],
        task_id,
        status="InProgress",
        quantity=50,
        started_at=datetime.now(UTC) - timedelta(minutes=45),
    )

    completed = client.post(f"/api/v1/tasks/{task_id}/complete", headers=game_headers)

    assert completed.status_code == 200
    task = completed.json()["task"]
    assert task["status"] == "Completed"
    assert task["result_quantity"] == 4


async def test_complete_unaffected_for_quantity_less_tasks(task_clients: Any) -> None:
    client = task_clients["client"]
    web_headers = _headers(task_clients["web_token"])
    game_headers = _headers(task_clients["game_token"])
    task_id = _create_gathering_task(client, web_headers, "task-collect-3")

    client.post(f"/api/v1/tasks/{task_id}/start", headers=game_headers)
    completed = client.post(f"/api/v1/tasks/{task_id}/complete", headers=game_headers)

    assert completed.status_code == 200
    task = completed.json()["task"]
    assert task["status"] == "Completed"
    assert task["result_quantity"] is None


async def test_collect_requires_web_role(task_clients: Any) -> None:
    client = task_clients["client"]
    web_headers = _headers(task_clients["web_token"])
    game_headers = _headers(task_clients["game_token"])
    task_id = _create_gathering_task(client, web_headers, "task-collect-4")
    await _force_task_state(
        task_clients["database"],
        task_id,
        status="InProgress",
        quantity=50,
        started_at=datetime.now(UTC) - timedelta(minutes=45),
    )

    denied = client.post(f"/api/v1/tasks/{task_id}/collect", headers=game_headers)

    assert denied.status_code == 403


async def test_collect_rejects_when_not_in_progress(task_clients: Any) -> None:
    client = task_clients["client"]
    web_headers = _headers(task_clients["web_token"])
    task_id = _create_gathering_task(client, web_headers, "task-collect-5")

    rejected = client.post(f"/api/v1/tasks/{task_id}/collect", headers=web_headers)

    assert rejected.status_code == 409
