"""HTTP contract checks for canonical GameEvent and Command Result ingestion."""

import hashlib
import json
from typing import Any

import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr

from app.credentials import CredentialProtector
from app.main import create_app
from tests.conftest import make_authenticated_device, make_database, make_settings

PROTECTOR = CredentialProtector(SecretStr("test-only-pepper-not-for-production"))


@pytest.fixture
async def event_client() -> Any:
    settings = make_settings(llm_provider="mock")
    database = await make_database(settings)
    _identity, token = await make_authenticated_device(database, PROTECTOR)
    with TestClient(create_app(settings)) as client:
        yield client, token


def _game_time() -> dict[str, object]:
    return {"source": "GameWorld", "day": 2, "hour": 3, "period": "Night"}


def _event(**overrides: object) -> dict[str, object]:
    payload: dict[str, object] = {
        "schema_version": 1,
        "event_id": "event-route-1",
        "session_id": "session-1",
        "save_slot_id": "slot-1",
        "companion_id": "mako",
        "type": "Event.Danger.Detected",
        "occurred_at": "2026-08-16T01:00:00+09:00",
        "time_context": _game_time(),
        "actor_id": "actor-1",
        "target_ids": ["target-1"],
        "payload": {},
    }
    payload.update(overrides)
    return payload


def _post_raw(
    client: TestClient,
    token: str,
    path: str,
    payload: dict[str, object],
    request_id: str,
    *,
    digest: str | None = None,
) -> Any:
    raw = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode()
    return client.post(
        path,
        content=raw,
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
            "X-Request-ID": request_id,
            "X-Content-SHA256": digest or hashlib.sha256(raw).hexdigest(),
        },
    )


async def test_event_route_validates_hash_replays_and_rejects_conflict(
    event_client: Any,
) -> None:
    client, token = event_client
    payload = _event()

    first = _post_raw(client, token, "/api/v1/events", payload, "event-route-1")
    replay = _post_raw(client, token, "/api/v1/events", payload, "event-route-1")
    conflict = _post_raw(
        client,
        token,
        "/api/v1/events",
        _event(actor_id="actor-2"),
        "event-route-1",
    )
    bad_hash = _post_raw(
        client,
        token,
        "/api/v1/events",
        _event(event_id="event-route-2"),
        "event-route-2",
        digest="A" * 64,
    )

    assert first.status_code == 200
    assert first.json()["importance"] == "High"
    assert replay.json() == first.json()
    assert conflict.status_code == 409
    assert bad_hash.status_code == 400


async def test_openapi_exposes_event_and_command_result_contracts(
    event_client: Any,
) -> None:
    client, _token = event_client

    paths = client.get("/openapi.json").json()["paths"]

    assert "/api/v1/events" in paths
    assert "/api/v1/command-results" in paths


async def test_event_route_is_game_client_only(event_client: Any) -> None:
    client, _token = event_client
    payload = _event(event_id="event-web-role")

    response = _post_raw(
        client,
        "AIRE_WEB",
        "/api/v1/events",
        payload,
        "event-web-role",
    )

    assert response.status_code == 403
    assert response.json()["error"]["code"] == "DeviceRoleNotAllowed"


@pytest.mark.parametrize(
    "override",
    [
        {"schema_version": 2},
        {"type": "Event.Unknown"},
        {"payload": {"detail": True}},
        {"target_ids": ["same", "same"]},
        {"time_context": {"source": "RealWorld", "day": 2, "hour": 3, "period": "Night"}},
        {"occurred_at": "2026-08-16T01:00:00"},
        {"unexpected": True},
    ],
)
async def test_event_route_rejects_invalid_contract(
    event_client: Any, override: dict[str, object]
) -> None:
    client, token = event_client
    payload = _event(event_id="event-invalid", **override)

    response = _post_raw(client, token, "/api/v1/events", payload, "event-invalid")

    assert response.status_code == 400
    assert response.json()["error"]["code"] == "InvalidRequest"


async def test_command_result_route_enforces_candidate_lifecycle(
    event_client: Any,
) -> None:
    client, token = event_client
    chat = client.post(
        "/api/v1/chat",
        headers={"Authorization": f"Bearer {token}"},
        json={
            "request_id": "chat-result-1",
            "session_id": "session-1",
            "save_slot_id": "slot-1",
            "companion_id": "mako",
            "user_message": "따라와",
            "game_context": {
                "schema_version": 1,
                "location_id": None,
                "threat": {"present": False, "count": 0, "nearest_kind": None},
                "nearby_resources": [],
                "available_workstations": [],
                "current_work": None,
                "inventories": [],
            },
            "allowed_commands": ["Command.Follow"],
        },
    )
    candidate = chat.json()["command_candidates"][0]

    def result(operation_id: str, status: str) -> dict[str, object]:
        return {
            "schema_version": 1,
            "operation_id": operation_id,
            "session_id": "session-1",
            "save_slot_id": "slot-1",
            "companion_id": "mako",
            "command_id": candidate["command_id"],
            "request_id": "chat-result-1",
            "type": "Command.Follow",
            "status": status,
            "reason": "None" if status in {"Accepted", "Running"} else "LeaseCompleted",
            "occurred_at": "2026-08-16T01:00:00Z",
            "time_context": _game_time(),
        }

    accepted = _post_raw(
        client, token, "/api/v1/command-results", result("op-1", "Accepted"), "op-1"
    )
    accepted_replay = _post_raw(
        client, token, "/api/v1/command-results", result("op-1", "Accepted"), "op-1"
    )
    accepted_conflict = _post_raw(
        client, token, "/api/v1/command-results", result("op-1", "Running"), "op-1"
    )
    running = _post_raw(
        client, token, "/api/v1/command-results", result("op-2", "Running"), "op-2"
    )
    succeeded = _post_raw(
        client, token, "/api/v1/command-results", result("op-3", "Succeeded"), "op-3"
    )
    late = _post_raw(
        client, token, "/api/v1/command-results", result("op-4", "Failed"), "op-4"
    )

    assert accepted.status_code == running.status_code == succeeded.status_code == 200
    assert accepted_replay.json() == accepted.json()
    assert accepted_conflict.status_code == 409
    assert late.status_code == 409
    assert late.json()["error"]["code"] == "CommandResultTransitionNotAllowed"
