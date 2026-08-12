"""AX-I09 Game State Snapshot API contract tests."""

from __future__ import annotations

import hashlib
import json
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr

from app.credentials import CredentialProtector
from app.db.models import DeviceModel, ItemModel, ProfileModel
from app.identity import DeviceRole
from app.main import create_app
from tests.conftest import make_authenticated_device, make_database, make_settings

PROTECTOR = CredentialProtector(SecretStr("test-only-pepper-not-for-production"))
MAKO_CONTAINER_ID = "AIRE.Inventory.MAKO"
SHARED_STORAGE_CONTAINER_ID = "AIRE.Inventory.SharedStorage"


async def _make_device(database: Any, *, profile_id: str, role: DeviceRole) -> str:
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
async def game_state_clients() -> Any:
    settings = make_settings(llm_provider="mock")
    database = await make_database(settings)
    game_identity, game_token = await make_authenticated_device(database, PROTECTOR)
    web_token = await _make_device(
        database,
        profile_id=game_identity.profile_id,
        role=DeviceRole.WEB_CLIENT,
    )
    other_identity, other_game_token = await make_authenticated_device(
        database,
        PROTECTOR,
    )
    async with database.session_factory() as session:
        session.add_all(
            [
                ItemModel(
                    item_id="PlantStem",
                    item_type="Material",
                    name_ko="Plant Stem",
                    aliases=[],
                    description="Game State test material.",
                ),
                ItemModel(
                    item_id="ShoddyBandage",
                    item_type="Consumable",
                    name_ko="Shoddy Bandage",
                    aliases=[],
                    description="Game State test consumable.",
                ),
                ItemModel(
                    item_id="TestSword",
                    item_type="Weapon",
                    name_ko="Test Sword",
                    aliases=[],
                    description="Game State test weapon.",
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
        }


def _snapshot(operation_id: str = "game-state-put-1", state_version: int = 1) -> dict[str, Any]:
    return {
        "schema_version": 1,
        "content_version": 1,
        "operation_id": operation_id,
        "state_version": state_version,
        "world_session_id": "world-session-1",
        "captured_at": "2026-08-12T03:00:00Z",
        "save_slot_id": "demo-slot-1",
        "companion_id": "mako",
        "inventory": {
            "player": {
                "capacity": 30,
                "revision": 7,
                "stacks": [
                    {"slot_index": 0, "item_id": "PlantStem", "count": 8},
                    {"slot_index": 100, "item_id": "ShoddyBandage", "count": 2},
                    {"slot_index": 29, "item_id": "TestSword", "count": 1},
                ],
                "equipment": {"equipped_item_id": "TestSword"},
            },
            "containers": [
                {
                    "container_id": MAKO_CONTAINER_ID,
                    "capacity": 20,
                    "revision": 3,
                    "stacks": [
                        {"slot_index": 0, "item_id": "PlantStem", "count": 4},
                        {"slot_index": 19, "item_id": "TestSword", "count": 1},
                    ],
                    "equipment": {"equipped_item_id": "TestSword"},
                },
                {
                    "container_id": SHARED_STORAGE_CONTAINER_ID,
                    "capacity": 50,
                    "revision": 5,
                    "stacks": [
                        {"slot_index": 49, "item_id": "PlantStem", "count": 10}
                    ],
                    "equipment": {"equipped_item_id": None},
                },
            ],
        },
    }


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def _put(
    client: TestClient,
    token: str,
    body: dict[str, Any],
    *,
    raw: bytes | None = None,
    request_id: str | None = None,
    body_hash: str | None = None,
) -> Any:
    raw = raw or json.dumps(body, separators=(",", ":")).encode("utf-8")
    headers = {
        **_auth(token),
        "Content-Type": "application/json",
        "X-Request-ID": request_id or body["operation_id"],
        "X-Content-SHA256": body_hash or hashlib.sha256(raw).hexdigest(),
    }
    return client.put("/api/v1/game-state", headers=headers, content=raw)


def _get(client: TestClient, token: str, *, request_id: str = "game-state-get-1") -> Any:
    return client.get(
        "/api/v1/game-state",
        headers={**_auth(token), "X-Request-ID": request_id},
        params={"save_slot_id": "demo-slot-1", "companion_id": "mako"},
    )


def test_game_client_put_and_game_or_web_get_full_snapshot(game_state_clients: Any) -> None:
    client = game_state_clients["client"]
    body = _snapshot()

    stored = _put(client, game_state_clients["game_token"], body)
    game_read = _get(client, game_state_clients["game_token"], request_id="get-game")
    web_read = _get(client, game_state_clients["web_token"], request_id="get-web")

    assert stored.status_code == 200
    assert game_read.status_code == 200
    assert web_read.status_code == 200
    response = stored.json()
    assert response["request_id"] == body["operation_id"]
    assert response["operation_id"] == body["operation_id"]
    assert response["schema_version"] == response["content_version"] == 1
    assert response["state_version"] == 1
    assert response["captured_at"] == "2026-08-12T03:00:00Z"
    assert response["last_synced_at"].endswith("Z") or response["last_synced_at"].endswith("+00:00")
    assert response["inventory"] == body["inventory"]
    assert game_read.json()["inventory"] == body["inventory"]
    assert web_read.json()["request_id"] == "get-web"


def test_put_requires_auth_game_role_and_matching_operation_headers(
    game_state_clients: Any,
) -> None:
    client = game_state_clients["client"]
    body = _snapshot()
    raw = json.dumps(body, separators=(",", ":")).encode()
    digest = hashlib.sha256(raw).hexdigest()

    missing_auth = client.put(
        "/api/v1/game-state",
        headers={
            "Content-Type": "application/json",
            "X-Request-ID": body["operation_id"],
            "X-Content-SHA256": digest,
        },
        content=raw,
    )
    web_write = _put(client, game_state_clients["web_token"], body)
    wrong_request_id = _put(
        client,
        game_state_clients["game_token"],
        body,
        request_id="different-operation",
    )
    uppercase_hash = _put(
        client,
        game_state_clients["game_token"],
        body,
        body_hash=digest.upper(),
    )

    assert missing_auth.status_code == 401
    assert web_write.status_code == 403
    assert web_write.json()["error"]["code"] == "DeviceRoleNotAllowed"
    assert wrong_request_id.status_code == 400
    assert wrong_request_id.json()["error"]["code"] == "InvalidRequest"
    assert uppercase_hash.status_code == 400
    assert uppercase_hash.json()["error"]["code"] == "InvalidRequest"


def test_content_hash_is_exact_sha256_of_raw_utf8_request_bytes(
    game_state_clients: Any,
) -> None:
    client = game_state_clients["client"]
    body = _snapshot(operation_id="raw-utf8-operation")
    raw = json.dumps(body, ensure_ascii=False, indent=2).encode("utf-8")

    accepted = _put(client, game_state_clients["game_token"], body, raw=raw)
    body["operation_id"] = "wrong-hash-operation"
    rejected = _put(
        client,
        game_state_clients["game_token"],
        body,
        body_hash="0" * 64,
    )

    assert accepted.status_code == 200
    assert rejected.status_code == 400
    assert rejected.json()["error"]["code"] == "InvalidRequest"


def test_duplicate_same_bytes_replays_original_response_and_different_bytes_conflict(
    game_state_clients: Any,
) -> None:
    client = game_state_clients["client"]
    body = _snapshot(operation_id="duplicate-operation")
    compact = json.dumps(body, separators=(",", ":")).encode()

    first = _put(client, game_state_clients["game_token"], body, raw=compact)
    replay = _put(client, game_state_clients["game_token"], body, raw=compact)
    spaced = json.dumps(body, indent=2).encode()
    conflict = _put(client, game_state_clients["game_token"], body, raw=spaced)

    assert first.status_code == replay.status_code == 200
    assert replay.json() == first.json()
    assert conflict.status_code == 409
    assert conflict.json()["error"]["code"] == "DuplicateRequest"
    assert _get(client, game_state_clients["game_token"]).json() == {
        **first.json(),
        "request_id": "game-state-get-1",
    }


def test_stale_version_is_rejected_without_changing_latest_snapshot(
    game_state_clients: Any,
) -> None:
    client = game_state_clients["client"]
    version_two = _snapshot("version-two", 2)
    version_two["inventory"]["player"]["revision"] = 20
    stale = _snapshot("stale-version", 1)
    stale["inventory"]["player"]["revision"] = 1

    assert _put(client, game_state_clients["game_token"], version_two).status_code == 200
    rejected = _put(client, game_state_clients["game_token"], stale)
    latest = _get(client, game_state_clients["web_token"])

    assert rejected.status_code == 409
    assert rejected.json()["error"]["code"] == "GameStateVersionConflict"
    assert latest.json()["state_version"] == 2
    assert latest.json()["inventory"]["player"]["revision"] == 20


def test_profile_and_scope_are_isolated(game_state_clients: Any) -> None:
    client = game_state_clients["client"]
    assert _put(client, game_state_clients["game_token"], _snapshot()).status_code == 200

    other_profile = _get(client, game_state_clients["other_game_token"])
    other_slot = client.get(
        "/api/v1/game-state",
        headers={**_auth(game_state_clients["game_token"]), "X-Request-ID": "other-slot"},
        params={"save_slot_id": "other-slot", "companion_id": "mako"},
    )
    unknown_companion = client.get(
        "/api/v1/game-state",
        headers={**_auth(game_state_clients["game_token"]), "X-Request-ID": "other-companion"},
        params={"save_slot_id": "demo-slot-1", "companion_id": "not-mako"},
    )

    assert other_profile.status_code == 404
    assert other_profile.json()["error"]["code"] == "GameStateNotFound"
    assert other_slot.status_code == 404
    assert other_slot.json()["error"]["code"] == "GameStateNotFound"
    assert unknown_companion.status_code == 400
    assert unknown_companion.json()["error"]["code"] == "UnknownCompanion"


@pytest.mark.parametrize(
    ("mutate", "expected_status"),
    [
        (lambda body: body.update(schema_version=2), 400),
        (lambda body: body.update(content_version=2), 400),
        (lambda body: body.update(state_version=0), 400),
        (lambda body: body.update(captured_at="2026-08-12T03:00:00"), 400),
        (lambda body: body.update(unexpected=True), 400),
        (lambda body: body["inventory"]["player"].update(capacity=31), 400),
        (lambda body: body["inventory"]["player"]["stacks"][0].update(slot_index=30), 400),
        (lambda body: body["inventory"]["player"]["stacks"][0].update(slot_index=110), 400),
        (lambda body: body["inventory"]["player"]["stacks"][0].update(count=100), 400),
        (lambda body: body["inventory"]["player"]["stacks"][0].update(item_id="Unknown"), 400),
        (lambda body: body["inventory"]["containers"][0].update(capacity=21), 400),
        (lambda body: body["inventory"]["containers"][1].update(capacity=49), 400),
        (lambda body: body["inventory"]["containers"].pop(), 400),
    ],
)
def test_snapshot_schema_and_inventory_bounds_are_strict(
    game_state_clients: Any,
    mutate: Any,
    expected_status: int,
) -> None:
    body = _snapshot(operation_id=f"invalid-{uuid4()}")
    mutate(body)
    response = _put(game_state_clients["client"], game_state_clients["game_token"], body)

    assert response.status_code == expected_status
    assert response.json()["error"]["code"] == "InvalidRequest"


@pytest.mark.parametrize(
    "mutate",
    [
        lambda body: body["inventory"]["player"]["stacks"][2].update(count=2),
        lambda body: body["inventory"]["player"]["equipment"].update(
            equipped_item_id="PlantStem"
        ),
        lambda body: body["inventory"]["containers"][1]["equipment"].update(
            equipped_item_id="TestSword"
        ),
        lambda body: body["inventory"]["containers"][1].update(
            container_id=MAKO_CONTAINER_ID
        ),
    ],
)
def test_equipment_weapon_and_container_invariants_are_validated(
    game_state_clients: Any,
    mutate: Any,
) -> None:
    body = _snapshot(operation_id=f"invalid-equipment-{uuid4()}")
    mutate(body)

    response = _put(game_state_clients["client"], game_state_clients["game_token"], body)

    assert response.status_code == 400
    assert response.json()["error"]["code"] == "InvalidRequest"


def test_snapshot_persists_across_application_restart(game_state_clients: Any) -> None:
    settings = make_settings(llm_provider="mock")
    body = _snapshot(operation_id="persistent-operation", state_version=8)
    first_app = create_app(settings)
    with TestClient(first_app) as first_client:
        stored = _put(first_client, game_state_clients["game_token"], body)
        assert stored.status_code == 200

    with TestClient(create_app(settings)) as restarted_client:
        loaded = _get(restarted_client, game_state_clients["web_token"])

    assert loaded.status_code == 200
    assert loaded.json()["state_version"] == 8
    assert loaded.json()["operation_id"] == "persistent-operation"


def test_concurrent_versions_leave_highest_accepted_snapshot(game_state_clients: Any) -> None:
    client = game_state_clients["client"]
    token = game_state_clients["game_token"]
    bodies = [_snapshot(f"concurrent-{version}", version) for version in (10, 11)]
    bodies[0]["inventory"]["player"]["revision"] = 10
    bodies[1]["inventory"]["player"]["revision"] = 11

    with ThreadPoolExecutor(max_workers=2) as executor:
        responses = list(executor.map(lambda body: _put(client, token, body), bodies))

    assert all(response.status_code in {200, 409} for response in responses)
    latest = _get(client, token)
    assert latest.status_code == 200
    assert latest.json()["state_version"] == max(
        response.json()["state_version"]
        for response in responses
        if response.status_code == 200
    )
    assert latest.json()["inventory"]["player"]["revision"] == latest.json()["state_version"]
