"""Chat tombstone behavior after canonical Message TTL purge."""

from datetime import UTC, datetime, timedelta

from fastapi.testclient import TestClient
from pydantic import SecretStr
from sqlalchemy import update

from app.credentials import CredentialProtector
from app.db.models import MessageModel
from app.main import create_app
from tests.conftest import make_authenticated_device, make_database, make_settings

PROTECTOR = CredentialProtector(SecretStr("test-only-pepper-not-for-production"))


async def test_purged_chat_replays_410_but_changed_digest_remains_409() -> None:
    settings = make_settings(llm_provider="mock")
    database = await make_database(settings)
    _identity, token = await make_authenticated_device(database, PROTECTOR)
    app = create_app(settings)
    payload = {
        "request_id": "chat-expired-1",
        "session_id": "session-1",
        "save_slot_id": "slot-1",
        "companion_id": "mako",
        "message_id": "message-expired-1",
        "user_message": "안녕, 마코",
        "game_context": {
            "schema_version": 1,
            "location_id": None,
            "threat": {"present": False, "count": 0, "nearest_kind": None},
            "nearby_resources": [],
            "available_workstations": [],
            "current_work": None,
            "inventories": [],
        },
    }
    headers = {"Authorization": f"Bearer {token}"}

    with TestClient(app) as client:
        first = client.post("/api/v1/chat", headers=headers, json=payload)
        assert first.status_code == 200

        now = datetime.now(UTC)
        async with database.session_factory() as session:
            await session.execute(
                update(MessageModel).values(expires_at=now - timedelta(seconds=1))
            )
            await session.commit()
        await app.state.retention.sweep(now=now)

        expired = client.post("/api/v1/chat", headers=headers, json=payload)
        conflict = client.post(
            "/api/v1/chat",
            headers=headers,
            json={**payload, "user_message": "다른 내용"},
        )

    assert expired.status_code == 410
    assert expired.json()["error"]["code"] == "IdempotencyRecordExpired"
    assert conflict.status_code == 409
    assert conflict.json()["error"]["code"] == "DuplicateRequest"
