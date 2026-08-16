"""Scoped user controls for source-backed memories."""

from typing import Annotated

from fastapi import APIRouter, Depends, Query

from app.dependencies import DatabaseSession, get_authenticated_device
from app.identity import AuthenticatedDevice
from app.memory_service import MemoryService
from app.middleware import get_request_id
from app.models import (
    MemoryListResponse,
    MemoryResetResponse,
    MemoryView,
    ResetMemoriesRequest,
    SearchMemoriesRequest,
    UpdateMemoryRequest,
)

router = APIRouter(prefix="/api/v1/memories", tags=["Memories"])


def _service(session: DatabaseSession) -> MemoryService:
    return MemoryService(session)


@router.get("", response_model=MemoryListResponse)
async def list_memories(
    save_slot_id: Annotated[str, Query(min_length=1, max_length=128)],
    companion_id: Annotated[str, Query(min_length=1, max_length=128)],
    identity: Annotated[AuthenticatedDevice, Depends(get_authenticated_device)],
    session: DatabaseSession,
) -> MemoryListResponse:
    return MemoryListResponse(
        request_id=get_request_id(),
        memories=await _service(session).list(
            identity, save_slot_id=save_slot_id, companion_id=companion_id
        ),
    )


@router.patch("/{memory_id}", response_model=MemoryView)
async def update_memory(
    memory_id: str,
    body: UpdateMemoryRequest,
    identity: Annotated[AuthenticatedDevice, Depends(get_authenticated_device)],
    session: DatabaseSession,
) -> MemoryView:
    return await _service(session).update(identity, memory_id, body)


@router.post("/search", response_model=MemoryListResponse)
async def search_memories(
    body: SearchMemoriesRequest,
    identity: Annotated[AuthenticatedDevice, Depends(get_authenticated_device)],
    session: DatabaseSession,
) -> MemoryListResponse:
    return MemoryListResponse(
        request_id=get_request_id(),
        memories=await _service(session).search(identity, body),
    )


@router.get("/{memory_id}", response_model=MemoryView)
async def get_memory(
    memory_id: str,
    identity: Annotated[AuthenticatedDevice, Depends(get_authenticated_device)],
    session: DatabaseSession,
) -> MemoryView:
    return await _service(session).get(identity, memory_id)


@router.delete("/{memory_id}", status_code=204)
async def delete_memory(
    memory_id: str,
    reason: Annotated[str, Query(min_length=1, max_length=512)],
    identity: Annotated[AuthenticatedDevice, Depends(get_authenticated_device)],
    session: DatabaseSession,
) -> None:
    await _service(session).delete(identity, memory_id, reason=reason)


@router.post("/reset", response_model=MemoryResetResponse)
async def reset_memories(
    body: ResetMemoriesRequest,
    identity: Annotated[AuthenticatedDevice, Depends(get_authenticated_device)],
    session: DatabaseSession,
) -> MemoryResetResponse:
    return MemoryResetResponse(
        request_id=get_request_id(),
        archived_count=await _service(session).reset(
            identity,
            save_slot_id=body.save_slot_id,
            companion_id=body.companion_id,
            reason=body.reason,
        ),
    )
