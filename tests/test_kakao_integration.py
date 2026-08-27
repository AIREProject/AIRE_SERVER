import asyncio
from datetime import UTC, datetime
from typing import Any

import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr
from sqlalchemy import func, select

from app.db.models import (
    ConversationModel,
    DeviceModel,
    MemoryModel,
    ProfileModel,
    RelationshipStateModel,
    SaveSlotModel,
)
from app.identity import AuthenticatedDevice, DeviceRole
from app.kakao_identity import KakaoIdentityService
from app.main import create_app
from app.memory_service import MemoryService
from tests.conftest import make_database, make_settings

ADAPTER_TOKEN = "test-kakao-adapter-token"
IDENTITY_PEPPER = "test-kakao-identity-pepper"


def _settings(**overrides: Any) -> Any:
    return make_settings(
        llm_provider="mock",
        kakao_adapter_token=SecretStr(ADAPTER_TOKEN),
        kakao_identity_pepper=SecretStr(IDENTITY_PEPPER),
        **overrides,
    )


def _body(user_id: str, request_id: str) -> dict[str, Any]:
    return {
        "bot_id": "bot-1",
        "user": {"id": user_id, "type": "botUserKey"},
        "chat": {
            "schema_version": 1,
            "request_id": request_id,
            "session_id": "kakao",
            "save_slot_id": "demo-slot-1",
            "companion_id": "mako",
            "message_id": f"message-{request_id}",
            "user_message": "안녕",
            "surface": "mobile",
            "time_context": {
                "source": "RealWorld",
                "day": 27,
                "hour": 12,
                "period": "Noon",
            },
            "allowed_commands": [],
        },
    }


def _headers(request_id: str, token: str = ADAPTER_TOKEN) -> dict[str, str]:
    return {
        "Authorization": f"Bearer {token}",
        "X-Request-ID": request_id,
    }


@pytest.mark.asyncio
async def test_kakao_users_get_separate_persistent_scopes_without_raw_ids() -> None:
    settings = _settings()
    database = await make_database(settings)

    with TestClient(create_app(settings)) as client:
        first = client.post(
            "/api/v1/integrations/kakao/chat",
            headers=_headers("kakao-a-1"),
            json=_body("raw-user-a", "kakao-a-1"),
        )
        second = client.post(
            "/api/v1/integrations/kakao/chat",
            headers=_headers("kakao-b-1"),
            json=_body("raw-user-b", "kakao-b-1"),
        )

    assert first.status_code == 200
    assert second.status_code == 200
    async with database.session_factory() as session:
        profiles = tuple((await session.execute(select(ProfileModel))).scalars())
        devices = tuple((await session.execute(select(DeviceModel))).scalars())
        conversations = tuple((await session.execute(select(ConversationModel))).scalars())

    assert len(profiles) == 2
    assert len(devices) == 2
    assert len(conversations) == 2
    assert len({row.profile_id for row in conversations}) == 2
    persisted_identity = " ".join(
        [
            *(row.profile_id for row in profiles),
            *(
                " ".join(
                    (
                        row.device_id,
                        row.profile_id,
                        row.token_lookup_id,
                        row.token_hash,
                        row.creation_request_id,
                    )
                )
                for row in devices
            ),
            *(
                " ".join((row.conversation_id, row.profile_id, row.session_id, row.surface))
                for row in conversations
            ),
        ]
    )
    assert "raw-user-a" not in persisted_identity
    assert "raw-user-b" not in persisted_identity


@pytest.mark.asyncio
async def test_same_kakao_user_reuses_profile_conversation_and_memory_scope() -> None:
    settings = _settings()
    database = await make_database(settings)

    for request_id in ("kakao-same-1", "kakao-same-2"):
        # 새 app instance에서도 같은 HMAC identity와 kakao session으로 복원되어야 한다.
        with TestClient(create_app(settings)) as client:
            response = client.post(
                "/api/v1/integrations/kakao/chat",
                headers=_headers(request_id),
                json=_body("same-user", request_id),
            )
            assert response.status_code == 200

    async with database.session_factory() as session:
        profile = (await session.execute(select(ProfileModel))).scalar_one()
        slot = (
            await session.execute(
                select(SaveSlotModel).where(SaveSlotModel.profile_id == profile.profile_id)
            )
        ).scalar_one()
        conversation_count = await session.scalar(select(func.count(ConversationModel.row_id)))
        now = datetime.now(UTC)
        session.add(
            MemoryModel(
                memory_id="memory-kakao-same",
                profile_id=profile.profile_id,
                save_slot_row_id=slot.row_id,
                companion_id="mako",
                memory_type="Preference",
                text="비 오는 날을 좋아한다",
                normalized_text="비 오는 날을 좋아한다",
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
        session.add(
            RelationshipStateModel(
                row_id="relationship-kakao-same",
                profile_id=profile.profile_id,
                save_slot_row_id=slot.row_id,
                companion_id="mako",
                state="Growing",
                created_at=now,
                updated_at=now,
            )
        )
        await session.commit()
        device = (await session.execute(select(DeviceModel))).scalar_one()
        memories = await MemoryService(session).list(
            identity=AuthenticatedDevice(
                profile_id=profile.profile_id,
                device_id=device.device_id,
                role=DeviceRole.WEB_CLIENT,
            ),
            save_slot_id="demo-slot-1",
            companion_id="mako",
        )
        relationship = (await session.execute(select(RelationshipStateModel))).scalar_one()

    assert conversation_count == 1
    assert [memory.memory_id for memory in memories] == ["memory-kakao-same"]
    assert relationship.profile_id == profile.profile_id


@pytest.mark.asyncio
async def test_concurrent_first_resolution_converges_to_one_identity() -> None:
    settings = _settings()
    database = await make_database(settings)

    async def resolve() -> Any:
        async with database.session_factory() as session:
            return await KakaoIdentityService(
                session, SecretStr(IDENTITY_PEPPER)
            ).resolve(bot_id="bot-1", user_type="botUserKey", user_id="concurrent-user")

    first, second = await asyncio.gather(resolve(), resolve())

    assert first == second
    async with database.session_factory() as session:
        assert await session.scalar(select(func.count(ProfileModel.profile_id))) == 1
        assert await session.scalar(select(func.count(DeviceModel.device_id))) == 1


@pytest.mark.asyncio
async def test_kakao_route_fails_closed_and_validates_fixed_contract() -> None:
    settings = _settings()
    await make_database(settings)
    with TestClient(create_app(settings)) as client:
        missing = client.post(
            "/api/v1/integrations/kakao/chat",
            json=_body("user-1", "kakao-auth-1"),
        )
        wrong = client.post(
            "/api/v1/integrations/kakao/chat",
            headers=_headers("kakao-auth-2", "wrong"),
            json=_body("user-1", "kakao-auth-2"),
        )
        invalid = _body("user-1", "kakao-auth-3")
        invalid["user"]["type"] = "appUserId"
        unsupported_type = client.post(
            "/api/v1/integrations/kakao/chat",
            headers=_headers("kakao-auth-3"),
            json=invalid,
        )

    assert missing.status_code == 401
    assert wrong.status_code == 401
    assert unsupported_type.status_code == 400


@pytest.mark.asyncio
async def test_kakao_route_is_unavailable_without_both_secrets() -> None:
    for settings in (
        make_settings(
            llm_provider="mock",
            kakao_identity_pepper=SecretStr(IDENTITY_PEPPER),
        ),
        make_settings(
            llm_provider="mock",
            kakao_adapter_token=SecretStr(ADAPTER_TOKEN),
        ),
    ):
        await make_database(settings)
        with TestClient(create_app(settings)) as client:
            response = client.post(
                "/api/v1/integrations/kakao/chat",
                headers=_headers("kakao-unavailable"),
                json=_body("user-1", "kakao-unavailable"),
            )
        assert response.status_code == 503
