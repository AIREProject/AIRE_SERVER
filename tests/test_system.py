from fastapi.testclient import TestClient

from app.main import create_app
from tests.conftest import make_settings


def test_health_reports_the_selected_llm_provider() -> None:
    client = TestClient(create_app(make_settings(llm_provider="mock")))

    response = client.get("/health")

    assert response.status_code == 200
    assert response.json() == {
        "service": "mako-companion",
        "status": "ok",
        "llm_provider": "mock",
    }
