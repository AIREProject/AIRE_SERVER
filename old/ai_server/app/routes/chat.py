from typing import Annotated

from fastapi import APIRouter, Depends, Header

from app.credentials import CredentialProtector
from app.dependencies import (
    DatabaseSession,
    get_authenticated_device,
    get_companion,
    get_credential_protector,
)
from app.errors import APIError, ErrorCode
from app.identity import AuthenticatedDevice
from app.middleware import REQUEST_ID_HEADER, request_id_context
from app.models import ChatRequest, ChatResponse
from app.service import CompanionService

router = APIRouter(prefix="/api/v1", tags=["Chat"])


@router.post("/chat", response_model=ChatResponse)
async def create_chat_response(
    chat_request: ChatRequest,
    companion: Annotated[CompanionService, Depends(get_companion)],
    identity: Annotated[AuthenticatedDevice, Depends(get_authenticated_device)],
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
    if x_request_id is not None and x_request_id != chat_request.request_id:
        raise APIError(
            status_code=400,
            code=ErrorCode.INVALID_REQUEST,
            message="X-Request-ID must match the request body request_id.",
            retryable=False,
        )
    if x_request_id is None:
        request_id_context.set(chat_request.request_id)

    return await companion.create_response(chat_request, identity, session, protector)
