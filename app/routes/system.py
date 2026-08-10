from typing import Annotated, Literal

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from app.settings import Settings, get_settings

router = APIRouter(tags=["System"])


class HealthResponse(BaseModel):
    service: Literal["mako-companion"] = "mako-companion"
    status: Literal["ok"] = "ok"
    llm_provider: str


@router.get("/health", response_model=HealthResponse)
def get_health(
    settings: Annotated[Settings, Depends(get_settings)],
) -> HealthResponse:
    return HealthResponse(llm_provider=settings.llm_provider)
