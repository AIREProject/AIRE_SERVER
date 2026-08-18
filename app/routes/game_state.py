"""Authenticated Game State Snapshot storage and retrieval."""

import hashlib
import re
from typing import Annotated

from fastapi import APIRouter, Depends, Header, Query, Request

from app.db.game_state_repository import SqlAlchemyGameStateRepository
from app.dependencies import DatabaseSession, get_authenticated_device
from app.errors import APIError, ErrorCode
from app.game_state_models import GameStateResponse, PutGameStateRequest
from app.game_state_service import GameStateService
from app.identity import AuthenticatedDevice
from app.middleware import REQUEST_ID_HEADER, get_request_id

router = APIRouter(prefix="/api/v1/game-state", tags=["Game State"])
SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")


def _service(session: DatabaseSession) -> GameStateService:
    return GameStateService(SqlAlchemyGameStateRepository(session))


@router.put("", response_model=GameStateResponse)
async def put_game_state(
    request: Request,
    body: PutGameStateRequest,
    identity: Annotated[AuthenticatedDevice, Depends(get_authenticated_device)],
    session: DatabaseSession,
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
    x_base_state_version: Annotated[
        int | None,
        Header(alias="X-Base-State-Version", ge=1),
    ] = None,
) -> GameStateResponse:
    raw_body = await request.body()
    actual_hash = hashlib.sha256(raw_body).hexdigest()
    if not SHA256_PATTERN.fullmatch(x_content_sha256) or x_content_sha256 != actual_hash:
        raise APIError(
            status_code=400,
            code=ErrorCode.INVALID_REQUEST,
            message="X-Content-SHA256 does not match the request body.",
        )
    if x_request_id != body.operation_id:
        raise APIError(
            status_code=400,
            code=ErrorCode.INVALID_REQUEST,
            message="X-Request-ID must match operation_id.",
        )
    return await _service(session).put(
        body,
        identity,
        body_hash=actual_hash,
        payload_size_bytes=len(raw_body),
        base_state_version=x_base_state_version,
    )


@router.get("", response_model=GameStateResponse)
async def get_game_state(
    save_slot_id: Annotated[str, Query(min_length=1, max_length=128)],
    companion_id: Annotated[str, Query(min_length=1, max_length=128)],
    identity: Annotated[AuthenticatedDevice, Depends(get_authenticated_device)],
    session: DatabaseSession,
) -> GameStateResponse:
    return await _service(session).get(
        identity,
        request_id=get_request_id(),
        save_slot_id=save_slot_id,
        companion_id=companion_id,
    )
