"""Authenticated UE GameEvent and Command Result ingestion endpoints."""

import hashlib
import re
from typing import Annotated

from fastapi import APIRouter, Depends, Header, Request

from app.db.event_repository import SqlAlchemyEventRepository
from app.dependencies import DatabaseSession, get_authenticated_device
from app.errors import APIError, ErrorCode
from app.event_models import (
    CommandResultResponse,
    CreateCommandResultRequest,
    CreateGameEventRequest,
    GameEventResponse,
)
from app.event_service import EventService
from app.identity import AuthenticatedDevice
from app.middleware import REQUEST_ID_HEADER
from app.relationship_service import RelationshipService
from app.settings import Settings, get_settings

router = APIRouter(prefix="/api/v1", tags=["Events"])
_SHA256 = re.compile(r"^[0-9a-f]{64}$")


def _service(session: DatabaseSession, settings: Settings) -> EventService:
    return EventService(
        SqlAlchemyEventRepository(session),
        event_retention_days=settings.game_event_retention_days,
        audit_retention_days=settings.audit_retention_days,
        relationship_service=RelationshipService(session),
    )


async def _validated_hash(request: Request, supplied: str) -> str:
    actual = hashlib.sha256(await request.body()).hexdigest()
    if not _SHA256.fullmatch(supplied) or supplied != actual:
        raise APIError(
            status_code=400,
            code=ErrorCode.INVALID_REQUEST,
            message="X-Content-SHA256 does not match the request body.",
        )
    return actual


@router.post("/events", response_model=GameEventResponse)
async def create_event(
    request: Request,
    body: CreateGameEventRequest,
    identity: Annotated[AuthenticatedDevice, Depends(get_authenticated_device)],
    session: DatabaseSession,
    settings: Annotated[Settings, Depends(get_settings)],
    x_request_id: Annotated[
        str,
        Header(
            alias=REQUEST_ID_HEADER,
            min_length=1,
            max_length=128,
            pattern=r"^[A-Za-z0-9][A-Za-z0-9._:-]*$",
        ),
    ],
    x_content_sha256: Annotated[str, Header(alias="X-Content-SHA256")],
) -> GameEventResponse:
    body_hash = await _validated_hash(request, x_content_sha256)
    if x_request_id != body.event_id:
        raise APIError(
            status_code=400,
            code=ErrorCode.INVALID_REQUEST,
            message="X-Request-ID must match event_id.",
        )
    return await _service(session, settings).create_event(body, identity, body_hash=body_hash)


@router.post("/command-results", response_model=CommandResultResponse)
async def create_command_result(
    request: Request,
    body: CreateCommandResultRequest,
    identity: Annotated[AuthenticatedDevice, Depends(get_authenticated_device)],
    session: DatabaseSession,
    settings: Annotated[Settings, Depends(get_settings)],
    x_request_id: Annotated[
        str,
        Header(
            alias=REQUEST_ID_HEADER,
            min_length=1,
            max_length=128,
            pattern=r"^[A-Za-z0-9][A-Za-z0-9._:-]*$",
        ),
    ],
    x_content_sha256: Annotated[str, Header(alias="X-Content-SHA256")],
) -> CommandResultResponse:
    body_hash = await _validated_hash(request, x_content_sha256)
    if x_request_id != body.operation_id:
        raise APIError(
            status_code=400,
            code=ErrorCode.INVALID_REQUEST,
            message="X-Request-ID must match operation_id.",
        )
    return await _service(session, settings).create_command_result(
        body, identity, body_hash=body_hash
    )
