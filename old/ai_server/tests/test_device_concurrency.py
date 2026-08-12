"""동시 페어링에서도 1회 사용과 디바이스 캡이 지켜지는지.

두 불변식 모두 "읽어서 확인하고 나중에 쓴다" 로 구현하면 조용히 깨진다. 순차 테스트는
그 차이를 보지 못하므로 — 실제로 캡 초과가 이 방식으로 재현됐다 — 여기서는 세션을 따로
연 요청을 `asyncio.gather` 로 겹쳐 돌린다.

`TestClient` 는 동기라 요청을 겹칠 수 없어 서비스 계층을 직접 부른다.
"""

import asyncio
from datetime import UTC, datetime
from typing import Any

import pytest
from pydantic import SecretStr
from sqlalchemy.exc import IntegrityError

from app.credentials import CredentialProtector
from app.db.connection import Database
from app.db.device_repository import SqlAlchemyDeviceRepository
from app.db.models import DeviceModel
from app.errors import DeviceLimitExceededError, UsedPairingCodeError
from app.identity import AuthenticatedDevice, DeviceRole
from app.pairing_models import CreatePairingCodeRequest, PairDeviceRequest, RegisterGameRequest
from app.pairing_service import PairingService
from tests.conftest import make_database, make_settings

PROTECTOR = CredentialProtector(SecretStr("test-only-pepper-not-for-production"))


def _service(database: Database, session: Any, *, max_devices: int) -> PairingService:
    return PairingService(
        SqlAlchemyDeviceRepository(session),
        PROTECTOR,
        pairing_code_ttl_seconds=300,
        max_devices_per_profile=max_devices,
    )


async def _register_game(
    database: Database, *, max_devices: int, request_id: str = "req-game"
) -> AuthenticatedDevice:
    async with database.session_factory() as session:
        registered = await _service(database, session, max_devices=max_devices).register_game(
            RegisterGameRequest(request_id=request_id)
        )
    return AuthenticatedDevice(
        profile_id=registered.profile_id,
        device_id=registered.device.device_id,
        role=DeviceRole.GAME_CLIENT,
    )


async def _issue_code(
    database: Database, identity: AuthenticatedDevice, *, request_id: str, max_devices: int
) -> str:
    async with database.session_factory() as session:
        response = await _service(
            database, session, max_devices=max_devices
        ).create_pairing_code(CreatePairingCodeRequest(request_id=request_id), identity)
    return response.pairing_code


async def _redeem(
    database: Database, code: str, *, request_id: str, max_devices: int
) -> str | None:
    """페어링을 시도하고 성공하면 device_id 를, 거절되면 `None` 을 돌려준다."""

    async with database.session_factory() as session:
        try:
            response = await _service(database, session, max_devices=max_devices).pair_device(
                PairDeviceRequest(request_id=request_id, pairing_code=code)
            )
        except (UsedPairingCodeError, DeviceLimitExceededError):
            return None
        return response.device.device_id


@pytest.fixture
async def database() -> Database:
    return await make_database(make_settings())


async def _active_device_count(database: Database, profile_id: str) -> int:
    async with database.session_factory() as session:
        devices = await SqlAlchemyDeviceRepository(session).list_devices(profile_id)
    return len([device for device in devices if device.revoked_at is None])


async def test_one_code_redeemed_concurrently_creates_one_device(database: Database) -> None:
    """같은 코드를 서로 다른 request_id 로 동시에 써도 디바이스는 하나만 생겨야 한다.

    `request_id` 가 다르면 재전송 경로(`redeemed_request_id` 유니크)에 걸리지 않으므로,
    코드의 1회 사용을 지키는 것은 사용 처리의 원자성뿐이다.
    """

    identity = await _register_game(database, max_devices=20)
    code = await _issue_code(database, identity, request_id="req-code", max_devices=20)

    results = await asyncio.gather(
        _redeem(database, code, request_id="req-a", max_devices=20),
        _redeem(database, code, request_id="req-b", max_devices=20),
    )

    assert sorted(result is None for result in results) == [False, True]
    # GameClient 1 + WebClient 1. 둘 다 통과했다면 3 이 된다.
    assert await _active_device_count(database, identity.profile_id) == 2


async def test_single_use_holds_when_both_requests_read_before_either_writes(
    database: Database, monkeypatch: pytest.MonkeyPatch
) -> None:
    """양쪽이 "미사용" 을 읽은 뒤에 쓰기가 시작돼도 하나만 통과해야 한다.

    위 테스트는 이 경로를 증명하지 못한다 — SQLite 가 쓰기를 직렬화하는 바람에 두 요청이
    겹치지 않고, `used_at` 을 읽는 시점이 이미 갈린다. 그래서 여기서는 **창을 강제로
    연다**: 두 요청이 모두 코드 대조를 마친 뒤에야 쓰기로 넘어가게 barrier 를 끼운다.
    이 상태에서 지켜 주는 것은 `redeem_pairing_code` 의 `WHERE used_at IS NULL` 뿐이고,
    그 조건을 빼면 이 테스트만 실패한다(다른 DB 로 옮겼을 때 실제로 일어날 일이다).
    """

    identity = await _register_game(database, max_devices=20)
    code = await _issue_code(database, identity, request_id="req-code", max_devices=20)

    both_have_read = asyncio.Barrier(2)
    create_device = PairingService._new_device

    async def barriered(self: PairingService, *args: Any, **kwargs: Any) -> Any:
        await both_have_read.wait()
        return await create_device(self, *args, **kwargs)

    monkeypatch.setattr(PairingService, "_new_device", barriered)

    # barrier 가 안 풀리면 영원히 매달리므로 실패로 끊는다.
    async with asyncio.timeout(10):
        results = await asyncio.gather(
            _redeem(database, code, request_id="req-a", max_devices=20),
            _redeem(database, code, request_id="req-b", max_devices=20),
        )

    assert sorted(result is None for result in results) == [False, True]
    assert await _active_device_count(database, identity.profile_id) == 2


async def test_concurrent_register_game_creates_distinct_profiles(database: Database) -> None:
    """서로 다른 request_id 로 동시에 register-game 하면 각자 프로필을 얻는다."""

    identities = await asyncio.gather(
        *(
            _register_game(database, max_devices=20, request_id=f"req-game-{index}")
            for index in range(4)
        )
    )

    profile_ids = {identity.profile_id for identity in identities}
    device_ids = {identity.device_id for identity in identities}
    assert len(profile_ids) == 4
    assert len(device_ids) == 4


async def test_concurrent_register_game_same_request_id_is_idempotent(
    database: Database,
) -> None:
    """같은 request_id 로 동시에 register-game 해도 디바이스는 하나만 생긴다."""

    identities = await asyncio.gather(
        *(_register_game(database, max_devices=20, request_id="req-game") for _ in range(4))
    )

    assert len({identity.profile_id for identity in identities}) == 1
    assert len({identity.device_id for identity in identities}) == 1


async def test_per_profile_game_client_invariant_is_enforced(database: Database) -> None:
    """같은 프로필에 GameClient 두 개는 DB 유니크 제약으로 막힌다."""

    identity = await _register_game(database, max_devices=20)

    async with database.session_factory() as session:
        session.add(
            DeviceModel(
                device_id="device-duplicate-game",
                profile_id=identity.profile_id,
                role=DeviceRole.GAME_CLIENT.value,
                token_lookup_id="token-duplicate-game",
                token_hash="0" * 64,
                creation_request_id="req-duplicate-game",
                # 같은 프로필 → 같은 game_registration_key → 유니크 위반.
                game_registration_key=identity.profile_id,
                created_at=datetime.now(UTC),
                last_used_at=None,
                revoked_at=None,
            )
        )
        with pytest.raises(IntegrityError):
            await session.flush()


async def test_concurrent_pairing_never_exceeds_the_cap(database: Database) -> None:
    """캡 경계에서 동시에 밀어 넣어도 총 개수가 캡을 넘지 않아야 한다.

    캡 3 중 하나는 `register_game` 이 만든 GameClient 가 차지하므로 페어링으로 채울 수
    있는 자리는 둘뿐이다. 코드 세 개를 동시에 쓰면 하나는 반드시 거절돼야 한다.
    """

    identity = await _register_game(database, max_devices=3)
    codes = [
        await _issue_code(database, identity, request_id=f"req-code-{index}", max_devices=3)
        for index in range(3)
    ]

    results = await asyncio.gather(
        *(
            _redeem(database, code, request_id=f"req-pair-{index}", max_devices=3)
            for index, code in enumerate(codes)
        )
    )

    assert len([result for result in results if result is not None]) == 2
    assert await _active_device_count(database, identity.profile_id) == 3
