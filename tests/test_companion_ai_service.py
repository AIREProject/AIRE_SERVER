"""CompanionService 의 발화→(대사, 명령 후보) 매핑 검증.

MockLLMProvider 로 결정론적으로 라우팅·대사가 재현되므로 외부 호출이 없다. 신원은
이제 인증된 `AuthenticatedDevice` 가 준다(`docs/temporary-scaffolds.md` §2) — DB 에 직접
디바이스 행을 만드는 `tests.conftest.make_authenticated_device` 로 지름길을 쓴다.
"""

import re
from typing import Any

import pytest
from pydantic import SecretStr
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.brain import CompanionBrain
from app.brain.dialogue import DialogueSpec
from app.brain.llm import MockLLMProvider
from app.credentials import CredentialProtector
from app.db.connection import Database
from app.db.models import ItemModel, OfflineTaskModel
from app.errors import AIServiceUnavailableError
from app.identity import AuthenticatedDevice, DeviceRole
from app.models import AIMetadata, ChatRequest, ChatResponse, CommandType, TimeContext, TimeSource
from app.service import CompanionService, _player_key
from tests.conftest import make_authenticated_device, make_database, make_settings

METADATA = AIMetadata(provider="mock", model_version="mock-v1", prompt_version="companion-v2")
PROTECTOR = CredentialProtector(SecretStr("test-only-pepper-not-for-production"))


def empty_game_context(*, location_id: str | None = None) -> dict[str, Any]:
    return {
        "schema_version": 1,
        "location_id": location_id,
        "threat": {"present": False, "count": 0, "nearest_kind": None},
        "nearby_resources": [],
        "available_workstations": [],
        "current_work": None,
        "inventories": [],
    }


class RecordingProvider(MockLLMProvider):
    def __init__(self) -> None:
        self.dialogue_specs: list[DialogueSpec] = []

    async def generate_dialogue(self, spec: DialogueSpec) -> str:
        self.dialogue_specs.append(spec)
        return await super().generate_dialogue(spec)


def make_service(
    *, default_location_id: str | None = None, llm: MockLLMProvider | None = None
) -> CompanionService:
    return CompanionService(
        CompanionBrain(llm or MockLLMProvider()),
        metadata=METADATA,
        ai_timeout_seconds=5.0,
        default_location_id=default_location_id,
    )


def make_request(
    user_message: str,
    *,
    allowed_commands: list[CommandType] | None = None,
    game_context: dict[str, Any] | None = None,
    session_id: str = "session-1",
    save_slot_id: str = "slot-1",
    companion_id: str = "mako",
    message_id: str | None = None,
    time_context: TimeContext | None = None,
) -> ChatRequest:
    return ChatRequest(
        request_id="req-1",
        session_id=session_id,
        save_slot_id=save_slot_id,
        companion_id=companion_id,
        message_id=message_id,
        user_message=user_message,
        time_context=time_context,
        game_context=game_context or empty_game_context(),
        allowed_commands=allowed_commands or [],
    )


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


async def respond(
    service: CompanionService,
    identity: AuthenticatedDevice,
    session: AsyncSession,
    user_message: str,
    **overrides: Any,
) -> ChatResponse:
    request = make_request(user_message, **overrides)
    return await service.create_response(request, identity, session, PROTECTOR)


@pytest.mark.parametrize(
    ("text", "command_type"),
    [
        ("따라와", CommandType.FOLLOW),
        ("여기서 기다려", CommandType.HOLD_POSITION),
        ("그만", CommandType.CANCEL_CURRENT),
    ],
)
async def test_movement_commands_emit_mapped_candidate(
    text: str, command_type: CommandType, identity: AuthenticatedDevice, session: AsyncSession
) -> None:
    service = make_service()

    result = await respond(service, identity, session, text, allowed_commands=[command_type])

    assert result.request_id == "req-1"
    assert result.display_text
    assert len(result.command_candidates) == 1
    candidate = result.command_candidates[0]
    assert candidate.type is command_type
    assert candidate.request_id == "req-1"
    assert candidate.expires_at > candidate.issued_at


async def test_command_not_in_allowlist_returns_dialogue_only(
    identity: AuthenticatedDevice, session: AsyncSession
) -> None:
    service = make_service()

    # 발화는 대기 명령이지만 allowlist 가 비어 있어 명령을 방출하지 않는다.
    result = await respond(service, identity, session, "여기서 기다려", allowed_commands=[])

    assert result.display_text
    assert result.command_candidates == []


@pytest.mark.parametrize("text", ["됐어", "취소", "나중에 하자"])
async def test_cancel_utterances_emit_cancel_current(
    text: str, identity: AuthenticatedDevice, session: AsyncSession
) -> None:
    service = make_service()

    result = await respond(
        service, identity, session, text, allowed_commands=[CommandType.CANCEL_CURRENT]
    )

    assert len(result.command_candidates) == 1
    assert result.command_candidates[0].type is CommandType.CANCEL_CURRENT


async def test_stop_and_cancel_utterances_emit_same_command_type(
    identity: AuthenticatedDevice, session: AsyncSession
) -> None:
    service = make_service()
    allowed_commands = [CommandType.CANCEL_CURRENT]

    stop_result = await respond(
        service, identity, session, "그만", allowed_commands=allowed_commands
    )
    cancel_result = await respond(
        service, identity, session, "됐어", allowed_commands=allowed_commands
    )

    assert stop_result.command_candidates[0].type is CommandType.CANCEL_CURRENT
    assert cancel_result.command_candidates[0].type is stop_result.command_candidates[0].type


@pytest.mark.parametrize("text", ["됐어", "취소", "나중에 하자"])
async def test_cancel_utterances_respect_allowlist(
    text: str, identity: AuthenticatedDevice, session: AsyncSession
) -> None:
    service = make_service()

    result = await respond(service, identity, session, text, allowed_commands=[])

    assert result.display_text
    assert result.command_candidates == []


@pytest.mark.parametrize(
    ("text", "parameters"),
    [
        ("나무 20개 캐 줘", {"resource": "wood", "quantity": 20}),
        # 수량 미명시는 실패가 아니다. 키를 비워 게임이 기본량을 정하게 한다.
        ("나무를 모아 줘", {"resource": "wood"}),
        ("돌 캐줘", {"resource": "stone"}),
    ],
)
async def test_gather_emits_candidate_with_resolved_parameters(
    text: str,
    parameters: dict[str, object],
    identity: AuthenticatedDevice,
    session: AsyncSession,
) -> None:
    service = make_service()

    result = await respond(
        service, identity, session, text, allowed_commands=[CommandType.GATHER_RESOURCE]
    )

    assert result.display_text
    assert len(result.command_candidates) == 1
    candidate = result.command_candidates[0]
    assert candidate.type is CommandType.GATHER_RESOURCE
    assert candidate.parameters == parameters


async def test_gather_without_allowlist_returns_dialogue_only(
    identity: AuthenticatedDevice, session: AsyncSession
) -> None:
    service = make_service()

    result = await respond(service, identity, session, "나무를 모아 줘", allowed_commands=[])

    assert result.display_text
    assert result.command_candidates == []


async def _seed_branch_item(database: Database) -> None:
    async with database.session_factory() as db_session:
        db_session.add(
            ItemModel(
                item_id="Branch",
                item_type="Material",
                name_ko="나뭇가지",
                aliases=["나뭇가지"],
                description="나무에서 떨어진 가지.",
            )
        )
        await db_session.commit()


async def test_mobile_gather_creates_offline_task(
    database: Database, session: AsyncSession
) -> None:
    await _seed_branch_item(database)
    identity, _token = await make_authenticated_device(
        database, PROTECTOR, role=DeviceRole.WEB_CLIENT
    )
    service = make_service()

    result = await respond(
        service, identity, session, "나무를 모아 줘", allowed_commands=[CommandType.GATHER_RESOURCE]
    )

    assert result.command_candidates == []
    assert result.offline_task_id is not None

    async with database.session_factory() as check_session:
        rows = (
            await check_session.execute(
                select(OfflineTaskModel).where(OfflineTaskModel.profile_id == identity.profile_id)
            )
        ).scalars().all()
    assert len(rows) == 1
    assert rows[0].task_id == result.offline_task_id
    assert rows[0].task_type == "Gathering"
    assert rows[0].item_id == "Branch"
    # 채팅 경로는 살아있는 GameClient가 없어 Pending을 건너뛰고 바로 시작한다.
    assert rows[0].status == "InProgress"
    # 수량 미지정 요청은 상한치(MAX_GATHER_QUANTITY)를 요청 수량으로 삼는다.
    assert rows[0].quantity == 50


async def test_mobile_gather_with_quantity_stores_requested_amount(
    database: Database, session: AsyncSession
) -> None:
    await _seed_branch_item(database)
    identity, _token = await make_authenticated_device(
        database, PROTECTOR, role=DeviceRole.WEB_CLIENT
    )
    service = make_service()

    result = await respond(
        service,
        identity,
        session,
        "나무 20개 캐 줘",
        allowed_commands=[CommandType.GATHER_RESOURCE],
    )

    async with database.session_factory() as check_session:
        rows = (
            await check_session.execute(
                select(OfflineTaskModel).where(OfflineTaskModel.profile_id == identity.profile_id)
            )
        ).scalars().all()
    assert rows[0].task_id == result.offline_task_id
    assert rows[0].quantity == 20


async def test_mobile_gather_task_creation_is_idempotent(
    database: Database, session: AsyncSession
) -> None:
    await _seed_branch_item(database)
    identity, _token = await make_authenticated_device(
        database, PROTECTOR, role=DeviceRole.WEB_CLIENT
    )
    service = make_service()

    first = await respond(
        service, identity, session, "나무를 모아 줘", allowed_commands=[CommandType.GATHER_RESOURCE]
    )
    second = await respond(
        service, identity, session, "나무를 모아 줘", allowed_commands=[CommandType.GATHER_RESOURCE]
    )

    assert first.offline_task_id is not None
    assert first.offline_task_id == second.offline_task_id

    async with database.session_factory() as check_session:
        rows = (
            await check_session.execute(
                select(OfflineTaskModel).where(OfflineTaskModel.profile_id == identity.profile_id)
            )
        ).scalars().all()
    assert len(rows) == 1


async def test_game_client_gather_does_not_create_offline_task(
    identity: AuthenticatedDevice, session: AsyncSession
) -> None:
    service = make_service()

    result = await respond(
        service, identity, session, "나무를 모아 줘", allowed_commands=[CommandType.GATHER_RESOURCE]
    )

    assert result.offline_task_id is None
    assert len(result.command_candidates) == 1


async def test_mobile_gather_without_allowlist_creates_no_task(
    database: Database, session: AsyncSession
) -> None:
    identity, _token = await make_authenticated_device(
        database, PROTECTOR, role=DeviceRole.WEB_CLIENT
    )
    service = make_service()

    result = await respond(service, identity, session, "나무를 모아 줘", allowed_commands=[])

    assert result.offline_task_id is None
    assert result.command_candidates == []


async def test_recipe_returns_fact_grounded_dialogue(
    identity: AuthenticatedDevice, session: AsyncSession
) -> None:
    service = make_service()

    result = await respond(service, identity, session, "철검 만드는 법을 알려 줘")

    assert "철" in result.display_text
    assert result.command_candidates == []


async def test_enemy_returns_fact_grounded_dialogue(
    identity: AuthenticatedDevice, session: AsyncSession
) -> None:
    service = make_service()

    result = await respond(service, identity, session, "골리앗 약점이 뭐야?")

    assert "가슴의 깨진 코어" in result.display_text
    assert "폭발물" in result.display_text
    assert result.command_candidates == []


async def test_enemy_question_without_a_name_invents_nothing(
    identity: AuthenticatedDevice, session: AsyncSession
) -> None:
    """적을 지목하지 않은 '저거 어떻게 잡아?' 는 아무 적의 약점도 말하면 안 된다."""

    service = make_service()

    result = await respond(service, identity, session, "저거 어떻게 잡아?")

    assert "약점" not in result.display_text
    assert result.command_candidates == []


async def test_lore_uses_location_from_game_context(
    identity: AuthenticatedDevice, session: AsyncSession
) -> None:
    service = make_service()

    result = await respond(
        service,
        identity,
        session,
        "이 마을은 어떤 곳이야?",
        game_context=empty_game_context(
            location_id="region_abandoned_mining_village"
        ),
    )

    assert "광산" in result.display_text
    assert result.command_candidates == []


async def test_lore_falls_back_to_default_location_when_game_sends_none(
    identity: AuthenticatedDevice, session: AsyncSession
) -> None:
    """임시 발판: 게임이 위치를 안 보내는 동안 설정한 위치로 대신 답한다."""

    service = make_service(default_location_id="region_abandoned_mining_village")

    result = await respond(service, identity, session, "이 마을은 어떤 곳이야?")

    assert "광산" in result.display_text


async def test_default_location_does_not_override_location_from_game(
    identity: AuthenticatedDevice, session: AsyncSession
) -> None:
    """게임이 보낸 위치가 이긴다. 모르는 곳이라면 다른 곳 이야기를 지어내지 않는다."""

    service = make_service(default_location_id="region_abandoned_mining_village")

    result = await respond(
        service,
        identity,
        session,
        "이 마을은 어떤 곳이야?",
        game_context=empty_game_context(location_id="region_unknown"),
    )

    assert "광산" not in result.display_text
    assert result.display_text


async def test_service_maps_time_context_into_dialogue(
    identity: AuthenticatedDevice, session: AsyncSession
) -> None:
    provider = RecordingProvider()
    service = make_service(llm=provider)

    await respond(
        service,
        identity,
        session,
        "안녕, 마코",
        time_context=TimeContext(source=TimeSource.GAME_WORLD, day=2, hour=6, period="Dawn"),
    )

    assert provider.dialogue_specs[-1].situation == ("지금은 게임 세계 기준 2일차 새벽, 6시다.",)


async def test_conversation_returns_dialogue_without_command(
    identity: AuthenticatedDevice, session: AsyncSession
) -> None:
    service = make_service()

    result = await respond(service, identity, session, "안녕, 마코")

    assert result.display_text
    assert result.command_candidates == []


async def test_unknown_returns_dialogue_only(
    identity: AuthenticatedDevice, session: AsyncSession
) -> None:
    service = make_service()

    result = await respond(service, identity, session, "오늘 비가 올까?")

    assert result.display_text
    assert result.command_candidates == []


async def test_metadata_reports_provider(
    identity: AuthenticatedDevice, session: AsyncSession
) -> None:
    service = make_service()

    result = await respond(service, identity, session, "안녕, 마코")

    assert result.ai_metadata.provider == "mock"
    assert result.ai_metadata.model_version == "mock-v1"
    assert result.ai_metadata.prompt_version == "companion-v2"


async def test_aclose_delegates_to_provider() -> None:
    from unittest.mock import AsyncMock

    from app.brain.llm import TimingLLMProvider

    inner = MockLLMProvider()
    inner.aclose = AsyncMock()  # type: ignore[method-assign]
    service = CompanionService(
        CompanionBrain(TimingLLMProvider(inner)),
        metadata=METADATA,
        ai_timeout_seconds=5.0,
    )

    await service.aclose()

    # 서비스 → 두뇌 → TimingLLMProvider 순으로 위임하고, 마지막 공급자가 자원을 정리한다.
    inner.aclose.assert_awaited_once()


async def test_brain_failure_becomes_service_unavailable(
    identity: AuthenticatedDevice, session: AsyncSession
) -> None:
    """두뇌가 낸 어떤 예외도 표준 AI 장애 오류가 되어야 한다."""

    from tests.test_companion_graph import ExplodingLLMProvider

    service = CompanionService(
        CompanionBrain(ExplodingLLMProvider()),
        metadata=METADATA,
        ai_timeout_seconds=5.0,
    )

    with pytest.raises(AIServiceUnavailableError):
        await respond(service, identity, session, "따라와")


async def test_conversation_key_carries_multi_turn_state(
    identity: AuthenticatedDevice, session: AsyncSession
) -> None:
    """서비스가 키를 넘겨야 되묻기가 다음 요청으로 이어진다."""

    service = make_service()
    allowed = [CommandType.GATHER_RESOURCE]

    asked = await respond(
        service, identity, session, "저것 좀 캐 줘", allowed_commands=allowed
    )
    answered = await respond(service, identity, session, "나무", allowed_commands=allowed)

    assert asked.command_candidates == []
    assert len(answered.command_candidates) == 1
    assert answered.command_candidates[0].type is CommandType.GATHER_RESOURCE


async def test_different_sessions_do_not_share_state(
    identity: AuthenticatedDevice, session: AsyncSession
) -> None:
    service = make_service()
    allowed = [CommandType.GATHER_RESOURCE]

    await respond(service, identity, session, "저것 좀 캐 줘", allowed_commands=allowed)
    other = await respond(
        service, identity, session, "나무", allowed_commands=allowed, session_id="session-2"
    )

    assert other.command_candidates == []


def test_player_key_signature_carries_no_session() -> None:
    """장기기억은 세션을 넘어 이어져야 한다 — `_player_key` 는 애초에 세션을 받지 않는다.

    (`_conversation_key` 와 달리 `session_id`/`companion_id` 인자가 없다는 것 자체가
    이 불변식이다. 시그니처가 강제하므로 값 비교 테스트는 더 이상 필요 없다.)
    """

    key = _player_key(PROTECTOR, profile_id="profile-1", save_slot_id="slot-1")
    assert re.fullmatch(r"[0-9a-f]{64}", key)


async def test_player_key_separates_players(
    database: Database, identity: AuthenticatedDevice
) -> None:
    # `devices.game_registration_key` 는 프로필당 GameClient 하나만 허용한다 —
    # 프로필이 다르면 두 GameClient 도 공존하므로 서로 다른 프로필로 만든다.
    other_identity, _token = await make_authenticated_device(database, PROTECTOR)

    assert _player_key(
        PROTECTOR, profile_id=identity.profile_id, save_slot_id="slot-1"
    ) != _player_key(PROTECTOR, profile_id=other_identity.profile_id, save_slot_id="slot-1")


async def test_player_key_separates_save_slots(identity: AuthenticatedDevice) -> None:
    """같은 프로필이라도 세이브슬롯이 다르면 다른 장기기억이다."""

    assert _player_key(
        PROTECTOR, profile_id=identity.profile_id, save_slot_id="slot-1"
    ) != _player_key(PROTECTOR, profile_id=identity.profile_id, save_slot_id="slot-2")


def test_player_key_is_safe_to_use_as_a_file_name() -> None:
    """이 값이 그대로 파일 이름이 된다. 경로 구분자가 새면 안 된다."""

    key = _player_key(PROTECTOR, profile_id="../../profile", save_slot_id="slot-1")

    assert re.fullmatch(r"[0-9a-f]{64}", key)


async def test_allowed_commands_the_brain_cannot_produce_are_ignored(
    identity: AuthenticatedDevice, session: AsyncSession
) -> None:
    """게임에는 있지만 마코가 만들지 않는 명령이 허용돼도 그대로 통과해야 한다."""

    service = make_service()

    result = await respond(
        service,
        identity,
        session,
        "따라와",
        allowed_commands=[CommandType.ENGAGE_TARGET, CommandType.FOLLOW],
    )

    assert len(result.command_candidates) == 1
    assert result.command_candidates[0].type is CommandType.FOLLOW
