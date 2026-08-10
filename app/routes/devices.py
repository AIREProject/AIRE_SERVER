from typing import Annotated

from fastapi import APIRouter, Depends, Header

from app.credentials import CredentialProtector
from app.db.device_repository import SqlAlchemyDeviceRepository
from app.dependencies import (
    DatabaseSession,
    get_authenticated_device,
    get_bootstrap_game_token,
    get_credential_protector,
)
from app.errors import APIError, ErrorCode
from app.identity import AuthenticatedDevice
from app.middleware import REQUEST_ID_HEADER, get_request_id, request_id_context
from app.pairing_models import (
    CreatePairingCodeRequest,
    DeviceListResponse,
    DeviceRevocationResponse,
    DeviceSelfResponse,
    DeviceTokenResponse,
    PairDeviceRequest,
    PairingCodeResponse,
    RegisterGameRequest,
)
from app.pairing_service import PairingService
from app.settings import Settings, get_settings

router = APIRouter(prefix="/api/v1/devices", tags=["Devices"])

RequestIdHeader = Annotated[
    str | None,
    Header(
        alias=REQUEST_ID_HEADER,
        min_length=1,
        max_length=128,
        pattern=r"^[A-Za-z0-9][A-Za-z0-9._:-]*$",
    ),
]


def _adopt_body_request_id(body_request_id: str, header_request_id: str | None) -> None:
    if header_request_id is not None and header_request_id != body_request_id:
        raise APIError(
            status_code=400,
            code=ErrorCode.INVALID_REQUEST,
            message="X-Request-ID must match the request body request_id.",
        )
    if header_request_id is None:
        request_id_context.set(body_request_id)


def _service(
    session: DatabaseSession,
    protector: CredentialProtector,
    settings: Settings,
) -> PairingService:
    return PairingService(
        SqlAlchemyDeviceRepository(session),
        protector,
        pairing_code_ttl_seconds=settings.pairing_code_ttl_seconds,
        max_devices_per_profile=settings.max_devices_per_profile,
    )


@router.post("/register-game", response_model=DeviceTokenResponse)
async def register_game(
    body: RegisterGameRequest,
    session: DatabaseSession,
    protector: Annotated[CredentialProtector, Depends(get_credential_protector)],
    settings: Annotated[Settings, Depends(get_settings)],
    _bootstrap: Annotated[str, Depends(get_bootstrap_game_token)],
    x_request_id: RequestIdHeader = None,
) -> DeviceTokenResponse:
    _adopt_body_request_id(body.request_id, x_request_id)
    return await _service(session, protector, settings).register_game(body)


@router.post("/pairing-codes", response_model=PairingCodeResponse)
async def create_pairing_code(
    body: CreatePairingCodeRequest,
    identity: Annotated[AuthenticatedDevice, Depends(get_authenticated_device)],
    session: DatabaseSession,
    protector: Annotated[CredentialProtector, Depends(get_credential_protector)],
    settings: Annotated[Settings, Depends(get_settings)],
    x_request_id: RequestIdHeader = None,
) -> PairingCodeResponse:
    _adopt_body_request_id(body.request_id, x_request_id)
    return await _service(session, protector, settings).create_pairing_code(body, identity)


@router.post("/pair", response_model=DeviceTokenResponse)
async def pair_device(
    body: PairDeviceRequest,
    session: DatabaseSession,
    protector: Annotated[CredentialProtector, Depends(get_credential_protector)],
    settings: Annotated[Settings, Depends(get_settings)],
    x_request_id: RequestIdHeader = None,
) -> DeviceTokenResponse:
    _adopt_body_request_id(body.request_id, x_request_id)
    return await _service(session, protector, settings).pair_device(body)


@router.get("", response_model=DeviceListResponse)
async def list_devices(
    identity: Annotated[AuthenticatedDevice, Depends(get_authenticated_device)],
    session: DatabaseSession,
    protector: Annotated[CredentialProtector, Depends(get_credential_protector)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> DeviceListResponse:
    return await _service(session, protector, settings).list_devices(get_request_id(), identity)


@router.get("/me", response_model=DeviceSelfResponse)
async def get_current_device(
    identity: Annotated[AuthenticatedDevice, Depends(get_authenticated_device)],
    session: DatabaseSession,
    protector: Annotated[CredentialProtector, Depends(get_credential_protector)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> DeviceSelfResponse:
    return await _service(session, protector, settings).get_current_device(
        get_request_id(), identity
    )


@router.delete("/me", response_model=DeviceRevocationResponse)
async def revoke_current_device(
    identity: Annotated[AuthenticatedDevice, Depends(get_authenticated_device)],
    session: DatabaseSession,
    protector: Annotated[CredentialProtector, Depends(get_credential_protector)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> DeviceRevocationResponse:
    return await _service(session, protector, settings).revoke_current_device(
        get_request_id(), identity
    )


@router.delete("/{device_id}", response_model=DeviceRevocationResponse)
async def revoke_device(
    device_id: str,
    identity: Annotated[AuthenticatedDevice, Depends(get_authenticated_device)],
    session: DatabaseSession,
    protector: Annotated[CredentialProtector, Depends(get_credential_protector)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> DeviceRevocationResponse:
    return await _service(session, protector, settings).revoke_device(
        get_request_id(), device_id, identity
    )
