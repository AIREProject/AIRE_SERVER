"""프로필당 디바이스 캡: 초과하면 등록을 거부한다(자동 해지 없음).

기본값 20 자체를 20번 페어링해서 확인하는 대신, `make_settings` 로 낮춘 캡을 빠르게
채워 같은 판정 로직을 검증한다.
"""

from typing import Any

from fastapi.testclient import TestClient

from app.main import create_app
from tests.conftest import make_database, make_settings

BOOTSTRAP_TOKEN = "bootstrap-secret-for-tests"


def _headers(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


async def _make_client(max_devices: int) -> TestClient:
    settings = make_settings(
        llm_provider="mock",
        dev_game_device_token=BOOTSTRAP_TOKEN,
        max_devices_per_profile=max_devices,
    )
    await make_database(settings)
    return TestClient(create_app(settings))


def _pair_new_device(
    client: TestClient, game_token: str, *, request_suffix: str
) -> Any:
    pairing_code = client.post(
        "/api/v1/devices/pairing-codes",
        headers=_headers(game_token),
        json={"request_id": f"req-code-{request_suffix}"},
    ).json()["pairing_code"]
    return client.post(
        "/api/v1/devices/pair",
        json={"request_id": f"req-pair-{request_suffix}", "pairing_code": pairing_code},
    )


def test_default_cap_is_twenty() -> None:
    assert make_settings().max_devices_per_profile == 20


async def test_pairing_beyond_the_cap_is_rejected() -> None:
    # 캡 2 중 하나는 register-game 이 만드는 GameClient 가 이미 차지한다 — 페어링으로
    # 채울 수 있는 자리는 하나뿐이다.
    test_client = await _make_client(max_devices=2)
    with test_client as client:
        game_token = client.post(
            "/api/v1/devices/register-game",
            headers=_headers(BOOTSTRAP_TOKEN),
            json={"request_id": "req-game"},
        ).json()["device_token"]

        first_pair = _pair_new_device(client, game_token, request_suffix="1")
        assert first_pair.status_code == 200

        second_pair = _pair_new_device(client, game_token, request_suffix="2")
        assert second_pair.status_code == 403
        assert second_pair.json()["error"]["code"] == "DeviceLimitExceeded"


async def test_revoking_a_device_frees_a_cap_slot() -> None:
    test_client = await _make_client(max_devices=2)
    with test_client as client:
        game_token = client.post(
            "/api/v1/devices/register-game",
            headers=_headers(BOOTSTRAP_TOKEN),
            json={"request_id": "req-game"},
        ).json()["device_token"]

        first_device = _pair_new_device(client, game_token, request_suffix="1").json()["device"]
        blocked = _pair_new_device(client, game_token, request_suffix="2")
        assert blocked.status_code == 403

        client.delete(f"/api/v1/devices/{first_device['device_id']}", headers=_headers(game_token))

        after_revoke = _pair_new_device(client, game_token, request_suffix="3")
        assert after_revoke.status_code == 200
