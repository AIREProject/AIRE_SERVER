from fastapi.testclient import TestClient
from sqlalchemy import text

from app.main import create_app
from tests.conftest import make_database, make_settings


def test_health_reports_the_selected_llm_provider() -> None:
    client = TestClient(create_app(make_settings(llm_provider="mock")))

    response = client.get("/health")

    assert response.status_code == 200
    assert response.json() == {
        "service": "mako-companion",
        "status": "ok",
        "llm_provider": "mock",
    }


async def test_ready_requires_migration_head() -> None:
    settings = make_settings(llm_provider="mock", memory_worker_enabled=False)
    await make_database(settings)
    with TestClient(create_app(settings)) as client:
        response = client.get("/ready")
    assert response.status_code == 503
    assert response.json()["status"] == "not_ready"
    assert response.json()["database"] == "unavailable"


async def test_ready_accepts_current_database_head() -> None:
    settings = make_settings(llm_provider="mock", memory_worker_enabled=False)
    database = await make_database(settings)
    async with database.engine.begin() as connection:
        await connection.execute(text("CREATE TABLE alembic_version (version_num VARCHAR(32))"))
        await connection.execute(text("INSERT INTO alembic_version VALUES ('0014')"))
    with TestClient(create_app(settings)) as client:
        response = client.get("/ready")
    assert response.status_code == 200
    assert response.json()["status"] == "ready"
    assert response.json()["database_revision"] == "0014"
