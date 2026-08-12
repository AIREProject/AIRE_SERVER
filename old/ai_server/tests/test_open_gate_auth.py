"""고정 공개 Bearer 두 개가 기존 role/scope 경계에 연결되는지 검증한다."""

from app.db.models import DeviceModel, ProfileModel
from app.dependencies import (
    OPEN_GATE_GAME_DEVICE_ID,
    OPEN_GATE_GAME_TOKEN,
    OPEN_GATE_PROFILE_ID,
    OPEN_GATE_WEB_DEVICE_ID,
    OPEN_GATE_WEB_TOKEN,
    authenticate_device_token,
)
from app.identity import DeviceRole
from tests.conftest import make_database, make_settings


async def test_open_gate_tokens_create_fixed_shared_identity_without_pepper() -> None:
    settings = make_settings(device_credential_pepper=None)
    database = await make_database(settings)

    async with database.session_factory() as session:
        game = await authenticate_device_token(OPEN_GATE_GAME_TOKEN, settings, session)
        web = await authenticate_device_token(OPEN_GATE_WEB_TOKEN, settings, session)

        assert game.profile_id == OPEN_GATE_PROFILE_ID
        assert game.device_id == OPEN_GATE_GAME_DEVICE_ID
        assert game.role is DeviceRole.GAME_CLIENT
        assert web.profile_id == OPEN_GATE_PROFILE_ID
        assert web.device_id == OPEN_GATE_WEB_DEVICE_ID
        assert web.role is DeviceRole.WEB_CLIENT

        assert await session.get(ProfileModel, OPEN_GATE_PROFILE_ID) is not None
        assert await session.get(DeviceModel, OPEN_GATE_GAME_DEVICE_ID) is not None
        assert await session.get(DeviceModel, OPEN_GATE_WEB_DEVICE_ID) is not None
