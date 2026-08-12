"""모바일 작업 지시와 게임 클라이언트 상태 갱신 API."""

from typing import Annotated

from fastapi import APIRouter, Depends, Header, Query

from app.db.offline_task_repository import SqlAlchemyOfflineTaskRepository
from app.dependencies import DatabaseSession, get_authenticated_device
from app.errors import APIError, ErrorCode
from app.identity import AuthenticatedDevice
from app.middleware import REQUEST_ID_HEADER, get_request_id, request_id_context
from app.offline_task_models import (
    CreateOfflineTaskRequest,
    OfflineTaskListResponse,
    OfflineTaskResponse,
    OfflineTaskStatus,
)
from app.offline_task_service import OfflineTaskService

router = APIRouter(prefix="/api/v1/tasks", tags=["Offline Tasks"])

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


def _service(session: DatabaseSession) -> OfflineTaskService:
    return OfflineTaskService(SqlAlchemyOfflineTaskRepository(session))


@router.post("", response_model=OfflineTaskResponse)
async def create_task(
    body: CreateOfflineTaskRequest,
    identity: Annotated[AuthenticatedDevice, Depends(get_authenticated_device)],
    session: DatabaseSession,
    x_request_id: RequestIdHeader = None,
) -> OfflineTaskResponse:
    _adopt_body_request_id(body.request_id, x_request_id)
    return await _service(session).create(body, identity)


@router.get("", response_model=OfflineTaskListResponse)
async def list_tasks(
    save_slot_id: Annotated[str, Query(min_length=1, max_length=128)],
    identity: Annotated[AuthenticatedDevice, Depends(get_authenticated_device)],
    session: DatabaseSession,
    status: OfflineTaskStatus | None = None,
) -> OfflineTaskListResponse:
    return await _service(session).list(
        get_request_id(), identity, save_slot_id=save_slot_id, status=status
    )


@router.post("/{task_id}/start", response_model=OfflineTaskResponse)
async def start_task(
    task_id: str,
    identity: Annotated[AuthenticatedDevice, Depends(get_authenticated_device)],
    session: DatabaseSession,
) -> OfflineTaskResponse:
    return await _service(session).start(get_request_id(), task_id, identity)


@router.post("/{task_id}/complete", response_model=OfflineTaskResponse)
async def complete_task(
    task_id: str,
    identity: Annotated[AuthenticatedDevice, Depends(get_authenticated_device)],
    session: DatabaseSession,
) -> OfflineTaskResponse:
    return await _service(session).complete(get_request_id(), task_id, identity)


@router.post("/{task_id}/claim", response_model=OfflineTaskResponse)
async def claim_task(
    task_id: str,
    identity: Annotated[AuthenticatedDevice, Depends(get_authenticated_device)],
    session: DatabaseSession,
) -> OfflineTaskResponse:
    return await _service(session).claim(get_request_id(), task_id, identity)


@router.post("/{task_id}/collect", response_model=OfflineTaskResponse)
async def collect_task(
    task_id: str,
    identity: Annotated[AuthenticatedDevice, Depends(get_authenticated_device)],
    session: DatabaseSession,
) -> OfflineTaskResponse:
    return await _service(session).collect(get_request_id(), task_id, identity)
