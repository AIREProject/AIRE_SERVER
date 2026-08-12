"""CompanionService 가 두뇌의 답을 게임 계약으로 옮기는 경계 검증.

특히 `allowed_commands` 단언 — `graph.py` 가 결정하고 여기서 단언한다 — 이 살아 있는지
본다. 두뇌가 회귀해 허용 밖 명령을 내면 요청이 실패해야 한다.
"""

import pytest
from pydantic import SecretStr
from sqlalchemy.ext.asyncio import AsyncSession

from app.brain import CompanionAction, CompanionBrain, CompanionReply
from app.brain.contract import CompanionTurn
from app.brain.llm import MockLLMProvider
from app.credentials import CredentialProtector
from app.db.connection import Database
from app.errors import AIServiceInvalidOutputError
from app.identity import AuthenticatedDevice
from app.models import AIMetadata, ChatRequest, CommandType
from app.service import CompanionService
from tests.conftest import make_authenticated_device, make_database, make_settings

METADATA = AIMetadata(provider="mock", model_version="mock-v1", prompt_version="companion-v2")
PROTECTOR = CredentialProtector(SecretStr("test-only-pepper-not-for-production"))


@pytest.fixture
async def database() -> Database:
    return await make_database(make_settings())


@pytest.fixture
async def identity(database: Database) -> AuthenticatedDevice:
    device_identity, _token = await make_authenticated_device(database, PROTECTOR)
    return device_identity


@pytest.fixture
async def session(database: Database) -> AsyncSession:
    async with database.session_factory() as db_session:
        yield db_session


def _service(brain: object | None = None) -> CompanionService:
    return CompanionService(
        brain or CompanionBrain(MockLLMProvider()),  # type: ignore[arg-type]
        metadata=METADATA,
        ai_timeout_seconds=5.0,
    )


def _request(user_message: str, **overrides: object) -> ChatRequest:
    payload: dict[str, object] = {
        "request_id": "req-1",
        "session_id": "session-1",
        "save_slot_id": "slot-1",
        "companion_id": "mako",
        "user_message": user_message,
    }
    payload.update(overrides)
    return ChatRequest.model_validate(payload)


async def test_wait_command_survives_the_boundary(
    identity: AuthenticatedDevice, session: AsyncSession
) -> None:
    request = _request("여기서 기다려", allowed_commands=[CommandType.HOLD_POSITION])

    response = await _service().create_response(request, identity, session, PROTECTOR)

    assert response.request_id == "req-1"
    assert response.session_id == "session-1"
    assert response.display_text
    assert len(response.command_candidates) == 1
    assert response.command_candidates[0].type is CommandType.HOLD_POSITION
    assert response.ai_metadata.provider == "mock"


async def test_out_of_allowlist_command_is_rejected(
    identity: AuthenticatedDevice, session: AsyncSession
) -> None:
    """두뇌가 허용 밖 행동을 내면 게임에 도달하기 전에 걸려야 한다.

    `graph.py` 의 게이트를 우회한 상태 — 즉 그 게이트가 회귀한 상황 — 를 재현하려고
    두뇌를 통째로 가짜로 바꾼다. 이 경로는 정상 동작으로는 만들 수 없다.
    """

    class RogueBrain:
        async def respond(self, turn: CompanionTurn) -> CompanionReply:
            return CompanionReply(
                text="알겠어, 따라갈게.",
                action=CompanionAction(type=CommandType.FOLLOW),
            )

        async def aclose(self) -> None:
            return None

    service = _service(RogueBrain())

    with pytest.raises(AIServiceInvalidOutputError):
        await service.create_response(
            _request("따라와", allowed_commands=[]), identity, session, PROTECTOR
        )


async def test_allowed_command_from_the_same_rogue_brain_passes(
    identity: AuthenticatedDevice, session: AsyncSession
) -> None:
    """단언이 무차별로 거절하지 않는지 — 허용되면 통과해야 한다."""

    class RogueBrain:
        async def respond(self, turn: CompanionTurn) -> CompanionReply:
            return CompanionReply(
                text="알겠어, 따라갈게.",
                action=CompanionAction(type=CommandType.FOLLOW),
            )

        async def aclose(self) -> None:
            return None

    service = _service(RogueBrain())

    response = await service.create_response(
        _request("따라와", allowed_commands=[CommandType.FOLLOW]), identity, session, PROTECTOR
    )

    assert response.command_candidates[0].type is CommandType.FOLLOW


async def test_conversation_key_separates_sessions(
    identity: AuthenticatedDevice, session: AsyncSession
) -> None:
    """같은 프로필이라도 세션이 다르면 되묻기 상태가 이어지지 않는다."""

    service = _service()
    allowed = [CommandType.GATHER_RESOURCE]

    await service.create_response(
        _request("저것 좀 캐 줘", allowed_commands=allowed), identity, session, PROTECTOR
    )
    other = await service.create_response(
        _request("나무", allowed_commands=allowed, session_id="session-2"),
        identity,
        session,
        PROTECTOR,
    )

    assert other.command_candidates == []


async def test_conversation_key_separates_players(
    database: Database, identity: AuthenticatedDevice, session: AsyncSession
) -> None:
    """같은 세션 이름이라도 프로필이 다르면 다른 대화다."""

    # `devices.game_registration_key` 는 프로필당 GameClient 하나만 허용한다 —
    # 프로필이 다르면 두 GameClient 도 공존하므로 서로 다른 프로필로 만든다.
    other_identity, _token = await make_authenticated_device(database, PROTECTOR)
    service = _service()
    allowed = [CommandType.GATHER_RESOURCE]

    await service.create_response(
        _request("저것 좀 캐 줘", allowed_commands=allowed), identity, session, PROTECTOR
    )
    other = await service.create_response(
        _request("나무", allowed_commands=allowed), other_identity, session, PROTECTOR
    )

    assert other.command_candidates == []
