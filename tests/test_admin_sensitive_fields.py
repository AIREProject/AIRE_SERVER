"""관리자 CRUD 가 자격 증명 해시를 새어 보내거나 받지 않는지 검증(§3).

`devices.token_hash`/`token_lookup_id`, `pairing_codes.code_hash` 는 `app/admin_models.py`
어디에도 없다 — 응답에 없고, `extra="forbid"`(`StrictModel`)라 요청에 넣으면 400
(`app/errors_http.py:handle_validation_error` 가 `RequestValidationError` 를 전부 400 으로
통일한다 — FastAPI 기본값인 422 가 아니다).
"""

from typing import Any

import pytest
from fastapi.testclient import TestClient

from app.main import create_app
from tests.conftest import make_database, make_settings

ADMIN_TOKEN = "admin-secret-for-tests"
HEADERS = {"Authorization": f"Bearer {ADMIN_TOKEN}"}


@pytest.fixture
async def seeded() -> Any:
    settings = make_settings(llm_provider="mock", admin_api_token=ADMIN_TOKEN)
    await make_database(settings)
    with TestClient(create_app(settings)) as client:
        profile = client.post(
            "/api/v1/admin/profiles", headers=HEADERS, json={"profile_id": "sensitive-profile"}
        )
        assert profile.status_code == 200
        device = client.post(
            "/api/v1/admin/devices",
            headers=HEADERS,
            json={
                "device_id": "sensitive-device",
                "profile_id": "sensitive-profile",
                "role": "WebClient",
                "creation_request_id": "req-sensitive",
            },
        )
        assert device.status_code == 200
        yield client


def test_device_response_never_exposes_credential_fields(seeded: Any) -> None:
    client = seeded
    response = client.get("/api/v1/admin/devices/sensitive-device", headers=HEADERS)
    assert response.status_code == 200
    body = response.json()
    assert "token_hash" not in body
    assert "token_lookup_id" not in body


def test_device_create_rejects_credential_fields(seeded: Any) -> None:
    client = seeded
    response = client.post(
        "/api/v1/admin/devices",
        headers=HEADERS,
        json={
            "device_id": "sneaky-device",
            "profile_id": "sensitive-profile",
            "role": "WebClient",
            "creation_request_id": "req-sneaky",
            "token_hash": "a" * 64,
        },
    )
    assert response.status_code == 400


def test_device_update_rejects_credential_fields(seeded: Any) -> None:
    client = seeded
    response = client.patch(
        "/api/v1/admin/devices/sensitive-device",
        headers=HEADERS,
        json={"token_lookup_id": "attacker-controlled"},
    )
    assert response.status_code == 400


def test_pairing_code_response_never_exposes_credential_fields(seeded: Any) -> None:
    client = seeded
    created = client.post(
        "/api/v1/admin/pairing-codes",
        headers=HEADERS,
        json={
            "pairing_code_id": "sensitive-pairing",
            "profile_id": "sensitive-profile",
            "issuing_device_id": "sensitive-device",
            "issue_request_id": "req-pairing",
            "expires_at": "2030-01-01T00:00:00Z",
        },
    )
    assert created.status_code == 200
    assert "code_hash" not in created.json()

    fetched = client.get("/api/v1/admin/pairing-codes/sensitive-pairing", headers=HEADERS)
    assert "code_hash" not in fetched.json()


def test_pairing_code_create_rejects_credential_fields(seeded: Any) -> None:
    client = seeded
    response = client.post(
        "/api/v1/admin/pairing-codes",
        headers=HEADERS,
        json={
            "pairing_code_id": "sneaky-pairing",
            "profile_id": "sensitive-profile",
            "issuing_device_id": "sensitive-device",
            "issue_request_id": "req-sneaky-pairing",
            "expires_at": "2030-01-01T00:00:00Z",
            "code_hash": "a" * 64,
        },
    )
    assert response.status_code == 400
