"""FastAPI 의존성 주입.

`get_companion`/`get_database_session` 은 `HTTPConnection`/`Request` 를 받는다 — 전자는
HTTP·WS 양쪽에서, 후자는 지금 HTTP 전용 라우트(`chat.py`, `routes/devices.py`)에서만
쓰인다. WebSocket 은 `_handle_chat_frame` 이 매 프레임마다 직접 세션을 열고
`authenticate_device_token` 을 호출한다 — FastAPI 의존성 해석이 연결 시점 한 번뿐이라
메시지 단위 인증에 맞지 않기 때문이다.
"""

from collections.abc import AsyncIterator
from datetime import UTC, datetime
from secrets import compare_digest
from typing import Annotated

from fastapi import Depends, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.requests import HTTPConnection

from app.credentials import CredentialProtector, build_credential_protector
from app.db.models import DeviceModel, ProfileModel
from app.errors import (
    AdminAuthenticationUnavailableError,
    AuthenticationUnavailableError,
    UnauthorizedAdminError,
    UnauthorizedDeviceError,
)
from app.identity import AuthenticatedDevice, DeviceRole
from app.service import CompanionService
from app.settings import Settings, get_settings

device_bearer = HTTPBearer(auto_error=False)

OPEN_GATE_PROFILE_ID = "AIRE_OPEN"
OPEN_GATE_GAME_TOKEN = "AIRE_GAME"
OPEN_GATE_WEB_TOKEN = "AIRE_WEB"
OPEN_GATE_GAME_DEVICE_ID = "AIRE_GAME"
OPEN_GATE_WEB_DEVICE_ID = "AIRE_WEB"


def get_companion(connection: HTTPConnection) -> CompanionService:
    """create_app 에서 조립해 둔 단일 서비스 인스턴스를 반환한다.

    HTTP 와 WebSocket 라우트 양쪽에서 쓰이므로 둘의 공통 상위 타입인 `HTTPConnection`
    을 받는다(FastAPI 가 두 경우 모두 주입한다). `Request` 로 좁히면 WS 에서 주입되지 않는다.
    """

    service: CompanionService = connection.app.state.companion
    return service


async def get_database_session(request: Request) -> AsyncIterator[AsyncSession]:
    async for session in request.app.state.database.session():
        yield session


DatabaseSession = Annotated[AsyncSession, Depends(get_database_session)]


async def authenticate_device_token(
    token: str,
    settings: Settings,
    session: AsyncSession,
) -> AuthenticatedDevice:
    """트랜스포트에 독립적인 디바이스 토큰 검증. HTTP 의존성과 WebSocket 이 공유한다."""

    open_gate_identity = _get_open_gate_identity(token)
    if open_gate_identity is not None:
        await _ensure_open_gate_identity(session, open_gate_identity, token)
        return open_gate_identity

    protector = build_credential_protector(settings)
    lookup_id = token.split(".", maxsplit=1)[0]
    result = await session.execute(
        select(DeviceModel).where(DeviceModel.token_lookup_id == lookup_id)
    )
    device = result.scalar_one_or_none()
    expected_hash = device.token_hash if device is not None else "0" * 64
    is_valid = protector.verify("device-token", token, expected_hash)
    if device is None or not is_valid or device.revoked_at is not None:
        raise UnauthorizedDeviceError("Device bearer token is invalid.")

    device.last_used_at = datetime.now(UTC)
    await session.commit()
    return AuthenticatedDevice(
        profile_id=device.profile_id,
        device_id=device.device_id,
        role=DeviceRole(device.role),
    )


def _get_open_gate_identity(token: str) -> AuthenticatedDevice | None:
    if compare_digest(token, OPEN_GATE_GAME_TOKEN):
        return AuthenticatedDevice(
            profile_id=OPEN_GATE_PROFILE_ID,
            device_id=OPEN_GATE_GAME_DEVICE_ID,
            role=DeviceRole.GAME_CLIENT,
        )
    if compare_digest(token, OPEN_GATE_WEB_TOKEN):
        return AuthenticatedDevice(
            profile_id=OPEN_GATE_PROFILE_ID,
            device_id=OPEN_GATE_WEB_DEVICE_ID,
            role=DeviceRole.WEB_CLIENT,
        )
    return None


async def _ensure_open_gate_identity(
    session: AsyncSession,
    identity: AuthenticatedDevice,
    token: str,
) -> None:
    """고정 공개 신원이 FK 를 사용하는 기존 서비스에서도 동작하도록 최소 행을 보장한다."""

    try:
        now = datetime.now(UTC)
        profile = await session.get(ProfileModel, identity.profile_id)
        if profile is None:
            session.add(ProfileModel(profile_id=identity.profile_id, created_at=now))
            await session.flush()

        device = await session.get(DeviceModel, identity.device_id)
        if device is None:
            session.add(
                DeviceModel(
                    device_id=identity.device_id,
                    profile_id=identity.profile_id,
                    role=identity.role.value,
                    token_lookup_id=token,
                    token_hash="0" * 64,
                    creation_request_id=f"OPEN_GATE_{identity.role.value}",
                    game_registration_key=(
                        identity.profile_id
                        if identity.role is DeviceRole.GAME_CLIENT
                        else None
                    ),
                    created_at=now,
                    last_used_at=now,
                    revoked_at=None,
                )
            )
        else:
            device.last_used_at = now
        await session.commit()
    except IntegrityError:
        await session.rollback()
        if (
            await session.get(ProfileModel, identity.profile_id) is None
            or await session.get(DeviceModel, identity.device_id) is None
        ):
            raise


def _require_credentials(credentials: HTTPAuthorizationCredentials | None) -> str:
    if credentials is None:
        raise UnauthorizedDeviceError("Device bearer token is required.")
    return credentials.credentials


async def get_authenticated_device(
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(device_bearer)],
    settings: Annotated[Settings, Depends(get_settings)],
    session: DatabaseSession,
) -> AuthenticatedDevice:
    return await authenticate_device_token(_require_credentials(credentials), settings, session)


def get_bootstrap_game_token(
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(device_bearer)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> str:
    """register-game 부트스트랩용. 아직 발급된 디바이스 토큰이 없을 때만 쓰인다."""

    supplied = _require_credentials(credentials)
    configured = (
        settings.dev_game_device_token.get_secret_value()
        if settings.dev_game_device_token is not None
        else ""
    )
    if not configured:
        raise AuthenticationUnavailableError(
            "GameClient bootstrap authentication is not configured."
        )
    if not compare_digest(supplied, configured):
        raise UnauthorizedDeviceError("Bootstrap bearer token is invalid.")
    return supplied


def get_credential_protector(
    settings: Annotated[Settings, Depends(get_settings)],
) -> CredentialProtector:
    return build_credential_protector(settings)


def get_admin_token(
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(device_bearer)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> None:
    """관리자 CRUD(`routes/admin.py`) 전용 게이트. 신원 객체를 만들지 않는 단순 통과 검사다."""

    supplied = _require_admin_credentials(credentials)
    configured = (
        settings.admin_api_token.get_secret_value()
        if settings.admin_api_token is not None
        else ""
    )
    if not configured:
        raise AdminAuthenticationUnavailableError("Admin authentication is not configured.")
    if not compare_digest(supplied, configured):
        raise UnauthorizedAdminError("Admin bearer token is invalid.")


def _require_admin_credentials(credentials: HTTPAuthorizationCredentials | None) -> str:
    if credentials is None:
        raise UnauthorizedAdminError("Admin bearer token is required.")
    return credentials.credentials


AdminAuthenticated = Annotated[None, Depends(get_admin_token)]
