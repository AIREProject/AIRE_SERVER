"""디바이스 페어링 API 검증: register-game → pairing-codes → pair, 조회, 해지.

`docs/temporary-scaffolds.md` §2 가 예고한 실제 Bearer 인증 복구다 — `player_name`
자기신고를 대체한다.
"""

from typing import Any

import pytest
from fastapi.testclient import TestClient

from app.main import create_app
from tests.conftest import make_database, make_settings

BOOTSTRAP_TOKEN = "bootstrap-secret-for-tests"


@pytest.fixture
async def client() -> Any:
    settings = make_settings(llm_provider="mock", dev_game_device_token=BOOTSTRAP_TOKEN)
    await make_database(settings)
    with TestClient(create_app(settings)) as test_client:
        yield test_client


def _headers(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def _register_game(client: TestClient, *, request_id: str = "req-game") -> dict[str, Any]:
    response = client.post(
        "/api/v1/devices/register-game",
        headers=_headers(BOOTSTRAP_TOKEN),
        json={"request_id": request_id},
    )
    payload: dict[str, Any] = response.json()
    return payload


async def test_register_game_requires_bootstrap_token(client: TestClient) -> None:
    response = client.post("/api/v1/devices/register-game", json={"request_id": "req-1"})

    assert response.status_code == 401
    assert response.json()["error"]["code"] == "UnauthorizedDevice"


async def test_register_game_issues_device_token(client: TestClient) -> None:
    payload = _register_game(client)

    assert payload["device"]["role"] == "GameClient"
    assert payload["device_token"]


async def test_register_game_is_idempotent_on_request_id(client: TestClient) -> None:
    first = _register_game(client, request_id="req-1")
    second = _register_game(client, request_id="req-1")

    assert first["device_token"] == second["device_token"]
    # 같은 request_id 재시도는 두 번째 프로필을 만들지 않는다.
    assert first["profile_id"] == second["profile_id"]


async def test_second_game_registration_creates_new_profile(client: TestClient) -> None:
    first = _register_game(client, request_id="req-1")

    response = client.post(
        "/api/v1/devices/register-game",
        headers=_headers(BOOTSTRAP_TOKEN),
        json={"request_id": "req-2"},
    )

    assert response.status_code == 200
    second = response.json()
    # 서로 다른 request_id 는 각자 새 프로필 + 새 GameClient 를 얻는다.
    assert first["profile_id"] != second["profile_id"]
    assert first["device_token"] != second["device_token"]
    assert second["device"]["role"] == "GameClient"


async def test_full_pairing_flow_issues_web_client_token(client: TestClient) -> None:
    game_token = _register_game(client)["device_token"]

    code_response = client.post(
        "/api/v1/devices/pairing-codes",
        headers=_headers(game_token),
        json={"request_id": "req-code-1"},
    )
    assert code_response.status_code == 200
    pairing_code = code_response.json()["pairing_code"]

    pair_response = client.post(
        "/api/v1/devices/pair",
        json={"request_id": "req-pair-1", "pairing_code": pairing_code},
    )

    assert pair_response.status_code == 200
    payload = pair_response.json()
    assert payload["device"]["role"] == "WebClient"
    assert payload["device_token"]


async def test_pairing_code_can_only_be_used_once(client: TestClient) -> None:
    game_token = _register_game(client)["device_token"]
    pairing_code = client.post(
        "/api/v1/devices/pairing-codes",
        headers=_headers(game_token),
        json={"request_id": "req-code-1"},
    ).json()["pairing_code"]

    first = client.post(
        "/api/v1/devices/pair", json={"request_id": "req-pair-1", "pairing_code": pairing_code}
    )
    second = client.post(
        "/api/v1/devices/pair", json={"request_id": "req-pair-2", "pairing_code": pairing_code}
    )

    assert first.status_code == 200
    assert second.status_code == 409
    assert second.json()["error"]["code"] == "UsedPairingCode"


async def test_invalid_pairing_code_is_rejected(client: TestClient) -> None:
    response = client.post(
        "/api/v1/devices/pair",
        json={"request_id": "req-pair-1", "pairing_code": "00000000"},
    )

    assert response.status_code == 400
    assert response.json()["error"]["code"] == "InvalidPairingCode"


async def test_web_client_can_list_and_revoke_itself(client: TestClient) -> None:
    game_token = _register_game(client)["device_token"]
    pairing_code = client.post(
        "/api/v1/devices/pairing-codes",
        headers=_headers(game_token),
        json={"request_id": "req-code-1"},
    ).json()["pairing_code"]
    web_token = client.post(
        "/api/v1/devices/pair",
        json={"request_id": "req-pair-1", "pairing_code": pairing_code},
    ).json()["device_token"]

    me = client.get("/api/v1/devices/me", headers=_headers(web_token))
    assert me.status_code == 200

    revoke = client.delete("/api/v1/devices/me", headers=_headers(web_token))
    assert revoke.status_code == 200
    assert revoke.json()["status"] == "Revoked"

    after = client.get("/api/v1/devices/me", headers=_headers(web_token))
    assert after.status_code == 401
    assert after.json()["error"]["code"] == "UnauthorizedDevice"


async def test_game_client_can_list_and_revoke_web_devices(client: TestClient) -> None:
    game_token = _register_game(client)["device_token"]
    pairing_code = client.post(
        "/api/v1/devices/pairing-codes",
        headers=_headers(game_token),
        json={"request_id": "req-code-1"},
    ).json()["pairing_code"]
    web_device = client.post(
        "/api/v1/devices/pair",
        json={"request_id": "req-pair-1", "pairing_code": pairing_code},
    ).json()["device"]

    listing = client.get("/api/v1/devices", headers=_headers(game_token))
    assert listing.status_code == 200
    assert any(
        device["device_id"] == web_device["device_id"] for device in listing.json()["devices"]
    )

    revoke = client.delete(
        f"/api/v1/devices/{web_device['device_id']}", headers=_headers(game_token)
    )
    assert revoke.status_code == 200
    assert revoke.json()["status"] == "Revoked"


def _pair_web_client(client: TestClient, game_token: str, *, suffix: str) -> dict[str, Any]:
    pairing_code = client.post(
        "/api/v1/devices/pairing-codes",
        headers=_headers(game_token),
        json={"request_id": f"req-code-{suffix}"},
    ).json()["pairing_code"]
    return dict(
        client.post(
            "/api/v1/devices/pair",
            json={"request_id": f"req-pair-{suffix}", "pairing_code": pairing_code},
        ).json()
    )


async def test_two_profiles_are_isolated(client: TestClient) -> None:
    """서로 다른 플레이어(프로필)의 기기는 서로를 보지 못한다."""

    game_a = _register_game(client, request_id="req-game-a")
    game_b = _register_game(client, request_id="req-game-b")
    assert game_a["profile_id"] != game_b["profile_id"]

    web_a = _pair_web_client(client, game_a["device_token"], suffix="a")
    web_b = _pair_web_client(client, game_b["device_token"], suffix="b")

    # 각 WebClient 는 자기 프로필에 붙는다.
    assert web_a["profile_id"] == game_a["profile_id"]
    assert web_b["profile_id"] == game_b["profile_id"]

    # 게임 A 의 목록에는 B 의 WebClient 가 보이지 않는다.
    listing_a = client.get(
        "/api/v1/devices", headers=_headers(game_a["device_token"])
    ).json()["devices"]
    device_ids_a = {device["device_id"] for device in listing_a}
    assert web_a["device"]["device_id"] in device_ids_a
    assert web_b["device"]["device_id"] not in device_ids_a


async def test_game_client_cannot_revoke_a_game_client_device(client: TestClient) -> None:
    """revoke_device 는 WebClient 대상 전용이다."""

    registration = _register_game(client)
    game_token = registration["device_token"]
    game_device_id = registration["device"]["device_id"]

    response = client.delete(f"/api/v1/devices/{game_device_id}", headers=_headers(game_token))

    assert response.status_code == 403
    assert response.json()["error"]["code"] == "DeviceRoleNotAllowed"
