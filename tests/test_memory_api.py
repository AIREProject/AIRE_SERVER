"""Scoped user-facing source-backed memory controls."""

from datetime import UTC, datetime
from typing import Any

import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr
from sqlalchemy import select

from app.credentials import CredentialProtector
from app.db.models import MemoryCorrectionModel, MemoryModel, SaveSlotModel
from app.main import create_app
from tests.conftest import make_authenticated_device, make_database, make_settings

PROTECTOR = CredentialProtector(SecretStr("test-only-pepper-not-for-production"))


@pytest.fixture
async def memory_client() -> Any:
    settings = make_settings(llm_provider="mock")
    database = await make_database(settings)
    identity, token = await make_authenticated_device(database, PROTECTOR)
    _other, other_token = await make_authenticated_device(database, PROTECTOR)
    now = datetime.now(UTC)
    async with database.session_factory() as session:
        slot = SaveSlotModel(
            row_id="slot-row-1",
            save_slot_id="slot-1",
            profile_id=identity.profile_id,
            created_at=now,
        )
        session.add(slot)
        await session.flush()
        session.add(
            MemoryModel(
                memory_id="memory-1",
                profile_id=identity.profile_id,
                save_slot_row_id=slot.row_id,
                companion_id="mako",
                memory_type="Preference",
                text="플레이어는 비를 좋아한다",
                normalized_text="플레이어는 비를 좋아한다",
                importance=6,
                pinned=False,
                status="Active",
                created_at=now,
                recalled_at=None,
                recall_count=0,
                embedding=None,
                embedding_model=None,
                archived_at=None,
                archived_reason=None,
            )
        )
        await session.commit()
    with TestClient(create_app(settings)) as client:
        yield client, database, token, other_token


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


async def test_memory_api_scopes_corrections_and_archival(memory_client: Any) -> None:
    client, database, token, other_token = memory_client
    path = "/api/v1/memories?save_slot_id=slot-1&companion_id=mako"
    listed = client.get(path, headers=_auth(token))
    assert listed.status_code == 200
    assert listed.json()["memories"][0]["corrected"] is False
    assert client.get(path, headers=_auth(other_token)).json()["memories"] == []
    assert client.post(
        "/api/v1/memories/search",
        headers=_auth(token),
        json={"save_slot_id": "slot-1", "companion_id": "mako", "query": "비"},
    ).json()["memories"][0]["memory_id"] == "memory-1"

    updated = client.patch(
        "/api/v1/memories/memory-1",
        headers=_auth(token),
        json={
            "corrected_text": "플레이어는 맑은 날을 좋아한다",
            "correction_reason": "사용자 정정",
            "pinned": True,
        },
    )
    assert updated.status_code == 200
    assert updated.json()["text"] == "플레이어는 맑은 날을 좋아한다"
    assert updated.json()["corrected"] is True
    searched = client.post(
        "/api/v1/memories/search",
        headers=_auth(token),
        json={"save_slot_id": "slot-1", "companion_id": "mako", "query": "맑은"},
    )
    assert searched.status_code == 200
    assert searched.json()["memories"][0]["corrected"] is True
    assert client.post(
        "/api/v1/memories/search",
        headers=_auth(other_token),
        json={"save_slot_id": "slot-1", "companion_id": "mako", "query": "맑은"},
    ).json()["memories"] == []
    assert client.patch(
        "/api/v1/memories/memory-1",
        headers=_auth(other_token),
        json={"pinned": False},
    ).status_code == 404

    deleted = client.delete(
        "/api/v1/memories/memory-1?reason=user-request", headers=_auth(token)
    )
    assert deleted.status_code == 204
    assert client.get(path, headers=_auth(token)).json()["memories"] == []
    assert client.post(
        "/api/v1/memories/search",
        headers=_auth(token),
        json={"save_slot_id": "slot-1", "companion_id": "mako", "query": "맑은"},
    ).json()["memories"] == []
    assert client.delete(
        "/api/v1/memories/memory-1?reason=user-request", headers=_auth(token)
    ).status_code == 404
    async with database.session_factory() as session:
        memory = await session.get(MemoryModel, "memory-1")
        correction = (await session.execute(select(MemoryCorrectionModel))).scalar_one()
        assert memory is not None and memory.status == "Archived"
        assert memory.text == "플레이어는 비를 좋아한다"
        assert correction.corrected_text == "플레이어는 맑은 날을 좋아한다"


async def test_memory_reset_is_scoped_and_idempotent(memory_client: Any) -> None:
    client, _database, token, _other_token = memory_client
    body = {"save_slot_id": "slot-1", "companion_id": "mako", "reason": "reset"}
    first = client.post("/api/v1/memories/reset", headers=_auth(token), json=body)
    second = client.post("/api/v1/memories/reset", headers=_auth(token), json=body)
    assert first.status_code == 200 and first.json()["archived_count"] == 1
    assert second.status_code == 200 and second.json()["archived_count"] == 0
