"""Authenticated review endpoints for pending memory candidates."""

from typing import Annotated

from fastapi import APIRouter, Depends, Query

from app.dependencies import DatabaseSession, get_authenticated_device
from app.identity import AuthenticatedDevice
from app.memory_candidate_review_service import MemoryCandidateReviewService
from app.memory_service import MemoryService
from app.middleware import get_request_id
from app.models import (
    MemoryCandidateDecisionResponse,
    MemoryCandidateListResponse,
    MemoryCandidateView,
    ReviewMemoryCandidateRequest,
)

router = APIRouter(prefix="/api/v1/memory-candidates", tags=["Memory Candidates"])


@router.get("", response_model=MemoryCandidateListResponse)
async def list_memory_candidates(
    save_slot_id: Annotated[str, Query(min_length=1, max_length=128)],
    companion_id: Annotated[str, Query(min_length=1, max_length=128)],
    identity: Annotated[AuthenticatedDevice, Depends(get_authenticated_device)],
    session: DatabaseSession,
) -> MemoryCandidateListResponse:
    candidates = await MemoryCandidateReviewService(session).list(
        identity, save_slot_id=save_slot_id, companion_id=companion_id
    )
    return MemoryCandidateListResponse(request_id=get_request_id(), candidates=candidates)


@router.get("/{candidate_id}", response_model=MemoryCandidateView)
async def get_memory_candidate(
    candidate_id: str,
    save_slot_id: Annotated[str, Query(min_length=1, max_length=128)],
    companion_id: Annotated[str, Query(min_length=1, max_length=128)],
    identity: Annotated[AuthenticatedDevice, Depends(get_authenticated_device)],
    session: DatabaseSession,
) -> MemoryCandidateView:
    return await MemoryCandidateReviewService(session).get(
        identity,
        candidate_id,
        save_slot_id=save_slot_id,
        companion_id=companion_id,
    )


@router.patch("/{candidate_id}", response_model=MemoryCandidateDecisionResponse)
async def decide_memory_candidate(
    candidate_id: str,
    save_slot_id: Annotated[str, Query(min_length=1, max_length=128)],
    companion_id: Annotated[str, Query(min_length=1, max_length=128)],
    body: ReviewMemoryCandidateRequest,
    identity: Annotated[AuthenticatedDevice, Depends(get_authenticated_device)],
    session: DatabaseSession,
) -> MemoryCandidateDecisionResponse:
    decision = await MemoryCandidateReviewService(session).decide(
        identity,
        candidate_id,
        body,
        save_slot_id=save_slot_id,
        companion_id=companion_id,
    )
    memory = (
        None
        if decision.memory_id is None
        else await MemoryService(session).get(identity, decision.memory_id)
    )
    return MemoryCandidateDecisionResponse(
        request_id=get_request_id(),
        candidate_id=decision.candidate_id,
        decision=body.decision,
        memory=memory,
    )
