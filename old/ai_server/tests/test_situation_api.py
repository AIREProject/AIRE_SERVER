"""HTTP 경로(POST /api/v1/situations → CompanionService → CompanionBrain.react) 검증.

`test_companion_chat_api.py` 와 같은 패턴: mock 공급자, 인증된 디바이스 토큰.
"""

from typing import Any

import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr

from app.brain.dialogue import SURFACE_PROFILES
from app.credentials import CredentialProtector
from app.main import create_app
from app.models import Surface
from tests.conftest import make_authenticated_device, make_database, make_settings

PROTECTOR = CredentialProtector(SecretStr("test-only-pepper-not-for-production"))


@pytest.fixture
async def authed_client() -> Any:
    settings = make_settings(llm_provider="mock")
    database = await make_database(settings)
    identity, token = await make_authenticated_device(database, PROTECTOR)
    with TestClient(create_app(settings)) as client:
        yield client, token, identity.profile_id


def _headers(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def _body(situation: list[str], **overrides: Any) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "request_id": "req-situation-1",
        "session_id": "session-1",
        "save_slot_id": "slot-1",
        "companion_id": "mako",
        "situation": situation,
    }
    payload.update(overrides)
    return payload


async def test_situation_yields_the_mock_fallback_line(authed_client: Any) -> None:
    """mock 공급자는 `spec.fallback` 을 그대로 돌려주므로, 게임 창구 상황 폴백과 같아야 한다."""

    client, token, _profile_id = authed_client

    response = client.post(
        "/api/v1/situations",
        headers=_headers(token),
        json=_body(["플레이어 체력이 20% 남았다", "주변에 적 2마리가 있다"]),
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["display_text"] == SURFACE_PROFILES[Surface.GAME].situation
    assert payload["ai_metadata"]["provider"] == "mock"
    assert payload["session_id"] == "session-1"
    assert payload["save_slot_id"] == "slot-1"
    assert payload["companion_id"] == "mako"
    assert "command_candidates" not in payload


async def test_mobile_surface_uses_the_mobile_fallback(authed_client: Any) -> None:
    client, token, _profile_id = authed_client

    response = client.post(
        "/api/v1/situations",
        headers=_headers(token),
        json=_body(["몬스터가 나타났다"], surface="mobile"),
    )

    assert response.status_code == 200
    assert response.json()["display_text"] == SURFACE_PROFILES[Surface.MOBILE].situation


async def test_empty_situation_is_rejected(authed_client: Any) -> None:
    client, token, _profile_id = authed_client

    response = client.post(
        "/api/v1/situations", headers=_headers(token), json=_body([])
    )

    assert response.status_code == 400


async def test_blank_situation_line_is_rejected(authed_client: Any) -> None:
    client, token, _profile_id = authed_client

    response = client.post(
        "/api/v1/situations", headers=_headers(token), json=_body(["   "])
    )

    assert response.status_code == 400


async def test_missing_bearer_token_is_rejected(authed_client: Any) -> None:
    client, _token, _profile_id = authed_client

    response = client.post("/api/v1/situations", json=_body(["몬스터가 나타났다"]))

    assert response.status_code == 401
    assert response.json()["error"]["code"] == "UnauthorizedDevice"


async def test_unknown_companion_id_is_rejected(authed_client: Any) -> None:
    client, token, _profile_id = authed_client

    response = client.post(
        "/api/v1/situations",
        headers=_headers(token),
        json=_body(["몬스터가 나타났다"], companion_id="not-mako"),
    )

    assert response.status_code == 400
    assert response.json()["error"]["code"] == "UnknownCompanion"


async def test_profile_claim_mismatch_is_rejected(authed_client: Any) -> None:
    client, token, _profile_id = authed_client

    response = client.post(
        "/api/v1/situations",
        headers=_headers(token),
        json=_body(["몬스터가 나타났다"], profile_id="profile-someone-else"),
    )

    assert response.status_code == 403
    assert response.json()["error"]["code"] == "IdentityScopeMismatch"


async def test_request_id_header_must_match_body(authed_client: Any) -> None:
    client, token, _profile_id = authed_client

    response = client.post(
        "/api/v1/situations",
        headers={**_headers(token), "X-Request-ID": "req-other"},
        json=_body(["몬스터가 나타났다"]),
    )

    assert response.status_code == 400


async def test_unknown_field_is_rejected(authed_client: Any) -> None:
    """`allowed_commands`/`game_context` 는 이 계약에 없다 — 있어도 거절된다."""

    client, token, _profile_id = authed_client

    response = client.post(
        "/api/v1/situations",
        headers=_headers(token),
        json=_body(["몬스터가 나타났다"], allowed_commands=["Command.Follow"]),
    )

    assert response.status_code == 400
