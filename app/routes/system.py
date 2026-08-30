from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Request, Response, status
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError

from app.settings import Settings, get_settings

router = APIRouter(tags=["System"])

EXPECTED_DATABASE_REVISION = "0018"


class HealthResponse(BaseModel):
    service: Literal["mako-companion"] = "mako-companion"
    status: Literal["ok"] = "ok"
    llm_provider: str


class ReadinessResponse(BaseModel):
    service: Literal["mako-companion"] = "mako-companion"
    status: Literal["ready", "degraded", "not_ready"]
    database: Literal["ready", "unavailable", "revision_mismatch"]
    database_revision: str | None
    expected_revision: str = EXPECTED_DATABASE_REVISION
    llm: Literal["ready", "degraded"]
    configured_llm_provider: str
    fallback_provider: Literal["mock"] = "mock"


@router.get("/health", response_model=HealthResponse)
def get_health(
    settings: Annotated[Settings, Depends(get_settings)],
) -> HealthResponse:
    return HealthResponse(llm_provider=settings.llm_provider)


@router.get("/ready", response_model=ReadinessResponse)
async def get_ready(
    request: Request,
    response: Response,
    settings: Annotated[Settings, Depends(get_settings)],
) -> ReadinessResponse:
    revision: str | None = None
    database_state: Literal["ready", "unavailable", "revision_mismatch"] = "unavailable"
    try:
        async with request.app.state.database.engine.connect() as connection:
            revision = await connection.scalar(text("SELECT version_num FROM alembic_version"))
        database_state = (
            "ready" if revision == EXPECTED_DATABASE_REVISION else "revision_mismatch"
        )
    except SQLAlchemyError:
        database_state = "unavailable"
    if database_state != "ready":
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
        return ReadinessResponse(
            status="not_ready",
            database=database_state,
            database_revision=revision,
            llm="ready",
            configured_llm_provider=settings.llm_provider,
        )
    worker_status = request.app.state.memory_worker.status
    error_is_latest = worker_status.last_error_at is not None and (
        worker_status.last_success_at is None
        or worker_status.last_error_at > worker_status.last_success_at
    )
    llm_state: Literal["ready", "degraded"] = "degraded" if error_is_latest else "ready"
    return ReadinessResponse(
        status="degraded" if llm_state == "degraded" else "ready",
        database=database_state,
        database_revision=revision,
        llm=llm_state,
        configured_llm_provider=settings.llm_provider,
    )
