"""관리자 CRUD 인증 게이트(`app/dependencies.py:get_admin_token`) 검증."""

from fastapi.testclient import TestClient

from app.main import create_app
from tests.conftest import make_database, make_settings

ADMIN_TOKEN = "admin-secret-for-tests"


async def _client(admin_api_token: str | None) -> TestClient:
    settings = make_settings(llm_provider="mock", admin_api_token=admin_api_token)
    await make_database(settings)
    return TestClient(create_app(settings))


async def test_missing_bearer_is_rejected() -> None:
    client = await _client(ADMIN_TOKEN)
    response = client.get("/api/v1/admin/profiles")
    assert response.status_code == 401
    assert response.json()["error"]["code"] == "UnauthorizedAdmin"


async def test_wrong_token_is_rejected() -> None:
    client = await _client(ADMIN_TOKEN)
    response = client.get(
        "/api/v1/admin/profiles", headers={"Authorization": "Bearer wrong-token"}
    )
    assert response.status_code == 401
    assert response.json()["error"]["code"] == "UnauthorizedAdmin"


async def test_unconfigured_admin_token_is_unavailable() -> None:
    client = await _client(None)
    response = client.get(
        "/api/v1/admin/profiles", headers={"Authorization": f"Bearer {ADMIN_TOKEN}"}
    )
    assert response.status_code == 503
    assert response.json()["error"]["code"] == "AdminAuthenticationUnavailable"


async def test_correct_token_is_accepted() -> None:
    client = await _client(ADMIN_TOKEN)
    response = client.get(
        "/api/v1/admin/profiles", headers={"Authorization": f"Bearer {ADMIN_TOKEN}"}
    )
    assert response.status_code == 200
    assert response.json() == []


async def test_device_bearer_token_does_not_grant_admin_access() -> None:
    """디바이스 인증(`get_authenticated_device`)과 관리자 인증은 서로 다른 비밀이다."""

    client = await _client(ADMIN_TOKEN)
    response = client.get(
        "/api/v1/admin/profiles", headers={"Authorization": "Bearer some-device-token"}
    )
    assert response.status_code == 401
