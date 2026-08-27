"""Private service-to-service Kakao chat integration."""

from secrets import compare_digest
from typing import Annotated

from fastapi import APIRouter, Depends, Header
from fastapi.security import HTTPAuthorizationCredentials

from app.credentials import CredentialProtector
from app.dependencies import (
    DatabaseSession,
    device_bearer,
    get_companion,
    get_credential_protector,
)
from app.errors import (
    APIError,
    AuthenticationUnavailableError,
    ErrorCode,
    UnauthorizedDeviceError,
)
from app.kakao_identity import KakaoIdentityService
from app.kakao_models import KakaoIntegrationChatRequest
from app.middleware import REQUEST_ID_HEADER, request_id_context
from app.models import ChatResponse
from app.service import CompanionService
from app.settings import Settings, get_settings

router = APIRouter(prefix="/api/v1/integrations/kakao", tags=["Kakao Integration"])


def _authorize_adapter(
    credentials: HTTPAuthorizationCredentials | None,
    settings: Settings,
) -> None:
    configured = (
        settings.kakao_adapter_token.get_secret_value()
        if settings.kakao_adapter_token is not None
        else ""
    )
    if not configured:
        raise AuthenticationUnavailableError("Kakao adapter authentication is not configured.")
    if credentials is None or not compare_digest(credentials.credentials, configured):
        raise UnauthorizedDeviceError("Kakao adapter bearer token is invalid.")


@router.post("/chat", response_model=ChatResponse)
async def create_kakao_chat_response(
    body: KakaoIntegrationChatRequest,
    companion: Annotated[CompanionService, Depends(get_companion)],
    settings: Annotated[Settings, Depends(get_settings)],
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(device_bearer)],
    session: DatabaseSession,
    protector: Annotated[CredentialProtector, Depends(get_credential_protector)],
    x_request_id: Annotated[
        str | None,
        Header(
            alias=REQUEST_ID_HEADER,
            min_length=1,
            max_length=128,
            pattern=r"^[A-Za-z0-9][A-Za-z0-9._:-]*$",
        ),
    ] = None,
) -> ChatResponse:
    _authorize_adapter(credentials, settings)
    if (
        settings.kakao_identity_pepper is None
        or not settings.kakao_identity_pepper.get_secret_value()
    ):
        raise AuthenticationUnavailableError("Kakao identity protection is not configured.")
    if x_request_id is not None and x_request_id != body.chat.request_id:
        raise APIError(
            status_code=400,
            code=ErrorCode.INVALID_REQUEST,
            message="X-Request-ID must match the nested chat request_id.",
            retryable=False,
        )
    if x_request_id is None:
        request_id_context.set(body.chat.request_id)

    identity = await KakaoIdentityService(
        session, settings.kakao_identity_pepper
    ).resolve(
        bot_id=body.bot_id,
        user_type=body.user.type,
        user_id=body.user.id,
    )
    return await companion.create_response(body.chat, identity, session, protector)
