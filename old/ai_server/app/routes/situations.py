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
from app.models import SituationRequest, SituationResponse
from app.service import CompanionService

router = APIRouter(prefix="/api/v1", tags=["Situations"])


@router.post("/situations", response_model=SituationResponse)
async def create_situation_response(
    situation_request: SituationRequest,
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
) -> SituationResponse:
    if x_request_id is not None and x_request_id != situation_request.request_id:
        raise APIError(
            status_code=400,
            code=ErrorCode.INVALID_REQUEST,
            message="X-Request-ID must match the request body request_id.",
            retryable=False,
        )
    if x_request_id is None:
        request_id_context.set(situation_request.request_id)

    return await companion.create_situation_response(
        situation_request, identity, session, protector
    )
