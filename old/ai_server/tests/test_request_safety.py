import asyncio

from fastapi.testclient import TestClient

from app.main import create_app
from tests.conftest import make_settings


def test_request_id_is_propagated() -> None:
    app = create_app(make_settings())
    with TestClient(app) as client:
        response = client.get("/health", headers={"X-Request-ID": "request-health-001"})

    assert response.status_code == 200
    assert response.headers["X-Request-ID"] == "request-health-001"


def test_invalid_request_id_uses_generated_id_in_standard_error() -> None:
    app = create_app(make_settings())
    with TestClient(app) as client:
        response = client.get("/health", headers={"X-Request-ID": "invalid id"})

    assert response.status_code == 400
    assert response.json()["error"]["code"] == "InvalidRequest"
    assert response.headers["X-Request-ID"] == response.json()["request_id"]
    assert response.json()["request_id"] != "invalid id"


def test_request_body_limit_uses_standard_error() -> None:
    app = create_app(make_settings(max_request_body_bytes=8))
    with TestClient(app) as client:
        response = client.post("/api/v1/chat", content=b"0123456789")

    assert response.status_code == 413
    assert response.json()["error"]["code"] == "RequestTooLarge"


def test_request_timeout_uses_standard_error() -> None:
    app = create_app(make_settings(request_timeout_seconds=0.01))

    @app.get("/test/slow")
    async def slow_response() -> dict[str, bool]:
        await asyncio.sleep(3600)
        return {"ok": True}

    with TestClient(app) as client:
        response = client.get("/test/slow")

    assert response.status_code == 504
    assert response.json()["error"]["code"] == "RequestTimeout"


def test_unexpected_error_uses_standard_error() -> None:
    app = create_app(make_settings())

    @app.get("/test/failure")
    async def unexpected_failure() -> None:
        raise RuntimeError("sensitive failure text")

    with TestClient(app, raise_server_exceptions=False) as client:
        response = client.get("/test/failure")

    assert response.status_code == 500
    assert response.json()["error"]["code"] == "InternalError"
    assert "sensitive failure text" not in response.text
