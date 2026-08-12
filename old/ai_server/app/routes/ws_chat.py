"""채팅 WebSocket 엔드포인트.

HTTP `POST /api/v1/chat` 과 **동일한 페이로드 스키마**를 `type` 판별자 봉투로 감싸
지속 연결 위에서 주고받는다. 봉투에는 `token` 이 나란히 실린다 — 브라우저 `WebSocket` 은
핸드셰이크에 커스텀 헤더를 못 실으므로, HTTP 의 `Authorization: Bearer` 대신 메시지마다
같은 검증(`authenticate_device_token`)을 거친다. 연결 하나로 게임/모바일을 나누지 않는
이유는 그대로다 — 그 구분은 애초에 토큰을 헤더로 받느냐 첫 메시지로 받느냐의 차이였을
뿐이고, 지금은 인증 방식이 하나(토큰-인-봉투)뿐이라 나눌 이유가 없다.
"""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from time import perf_counter
from typing import Annotated, Any, Protocol, cast

from fastapi import APIRouter, Depends, WebSocket, WebSocketDisconnect
from pydantic import BaseModel, ValidationError

from app.credentials import CredentialProtector, build_credential_protector
from app.dependencies import authenticate_device_token, get_companion
from app.errors import APIError, ErrorBody, ErrorCode, ErrorEnvelope, UnauthorizedDeviceError
from app.errors_http import APPLICATION_ERROR_MAP
from app.identity import AuthenticatedDevice
from app.middleware import get_request_id, request_id_context
from app.models import ChatRequest, SituationRequest
from app.service import CompanionService
from app.settings import Settings, get_settings

router = APIRouter(prefix="/api/v1", tags=["Chat"])
logger = logging.getLogger("aire.backend")


class _HasRequestId(Protocol):
    """검증된 `ChatRequest`/`SituationRequest` 가 공통으로 갖는 것 — 상관 ID 하나뿐이다."""

    request_id: str


@dataclass(frozen=True, slots=True)
class _FrameSpec:
    """`type` 판별자 하나가 봉투를 어떻게 검증하고 응답할지.

    `chat`/`situation` 은 계약 모델만 다르고 나머지 처리(인증·타임아웃·오류 매핑)는
    완전히 같다 — `CompanionService.create_response`/`create_situation_response` 가
    같은 시그니처 `(request, identity, session, protector) -> response` 를 공유하기
    때문에, 여기서는 어떤 서비스 메서드를 부를지만 클로저로 갈라 둔다.
    """

    request_model: type[BaseModel]
    response_type: str
    call: Callable[
        [CompanionService, Any, AuthenticatedDevice, Any, CredentialProtector], Awaitable[Any]
    ]


_FRAME_SPECS: dict[str, _FrameSpec] = {
    "chat": _FrameSpec(
        request_model=ChatRequest,
        response_type="chat_response",
        call=lambda companion, request, identity, session, protector: (
            companion.create_response(request, identity, session, protector)
        ),
    ),
    "situation": _FrameSpec(
        request_model=SituationRequest,
        response_type="situation_response",
        call=lambda companion, request, identity, session, protector: (
            companion.create_situation_response(request, identity, session, protector)
        ),
    ),
}


@router.websocket("/chat")
async def chat_websocket(
    websocket: WebSocket,
    settings: Annotated[Settings, Depends(get_settings)],
    companion: Annotated[CompanionService, Depends(get_companion)],
) -> None:
    await websocket.accept()
    while True:
        try:
            raw = await websocket.receive_text()
        except (WebSocketDisconnect, RuntimeError):
            return
        await _handle_frame(websocket, raw, settings, companion)


async def _handle_frame(
    websocket: WebSocket,
    raw: str,
    settings: Settings,
    companion: CompanionService,
) -> None:
    """단일 봉투(`chat`/`situation`)를 처리한다.

    WebSocket 은 `RequestContextMiddleware` 를 타지 않으므로(HTTP scope 만 처리),
    크기 제한·request-id 컨텍스트·타임아웃·완료 로깅·디바이스 인증을 여기서 메시지
    단위로 대신한다.
    """

    if len(raw.encode("utf-8")) > settings.max_request_body_bytes:
        await _send_error(
            websocket,
            ErrorCode.REQUEST_TOO_LARGE,
            "Message exceeds the configured size limit.",
            retryable=False,
        )
        return

    try:
        envelope = json.loads(raw)
    except json.JSONDecodeError:
        await _send_error(
            websocket,
            ErrorCode.INVALID_REQUEST,
            "Message is not valid JSON.",
            retryable=False,
        )
        return

    if not isinstance(envelope, dict):
        await _send_error(
            websocket,
            ErrorCode.INVALID_REQUEST,
            "Message envelope must be an object.",
            retryable=False,
        )
        return

    payload = envelope.get("payload")
    token = envelope.get("token")
    # 검증 실패 응답도 클라이언트가 상관지을 수 있도록 request_id 를 미리 확보한다.
    context_token = request_id_context.set(_candidate_request_id(payload))
    started_at = perf_counter()
    outcome = "error"
    try:
        frame_type = envelope.get("type")
        spec = _FRAME_SPECS.get(frame_type) if isinstance(frame_type, str) else None
        if spec is None:
            await _send_error(
                websocket,
                ErrorCode.INVALID_REQUEST,
                "Unsupported message type.",
                retryable=False,
            )
            return

        try:
            request_obj = spec.request_model.model_validate(payload)
        except ValidationError as error:
            await _send_error(
                websocket,
                ErrorCode.INVALID_REQUEST,
                "Request validation failed.",
                retryable=False,
                details={"issues": _validation_issues(error)},
            )
            return

        request_id_context.set(cast(_HasRequestId, request_obj).request_id)
        outcome = await _respond(websocket, spec, request_obj, token, settings, companion)
    finally:
        logger.info(
            "ws_message_complete",
            extra={
                "event": "ws_message_complete",
                "request_id": get_request_id(),
                "path": websocket.url.path,
                "outcome": outcome,
                "duration_ms": round((perf_counter() - started_at) * 1000, 3),
            },
        )
        request_id_context.reset(context_token)


async def _respond(
    websocket: WebSocket,
    spec: _FrameSpec,
    request_obj: Any,
    token: Any,
    settings: Settings,
    companion: CompanionService,
) -> str:
    try:
        async with asyncio.timeout(settings.request_timeout_seconds):
            if not isinstance(token, str) or not token:
                raise UnauthorizedDeviceError("Device bearer token is required.")
            protector = build_credential_protector(settings)
            database = websocket.app.state.database
            async with database.session_factory() as session:
                identity = await authenticate_device_token(token, settings, session)
                response = await spec.call(companion, request_obj, identity, session, protector)
    except TimeoutError:
        await _send_error(
            websocket,
            ErrorCode.REQUEST_TIMEOUT,
            "Request processing exceeded the configured timeout.",
            retryable=True,
        )
        return "timeout"
    except APIError as error:
        await _send_error(
            websocket,
            error.code,
            error.message,
            retryable=error.retryable,
            details=error.details,
        )
        return "error"
    except Exception as error:  # 어떤 실패도 연결을 끊지 않는다.
        mapped = APPLICATION_ERROR_MAP.get(type(error))
        if mapped is None:
            logger.error(
                "unhandled_ws_error",
                extra={
                    "event": "unhandled_ws_error",
                    "request_id": get_request_id(),
                    "error_type": type(error).__name__,
                },
            )
            await _send_error(
                websocket,
                ErrorCode.INTERNAL_ERROR,
                "An internal server error occurred.",
                retryable=True,
            )
            return "error"
        _, code, message, retryable = mapped
        await _send_error(websocket, code, message, retryable=retryable)
        return "error"

    await _send(
        websocket,
        {"type": spec.response_type, "payload": response.model_dump(mode="json")},
    )
    return "ok"


async def _send_error(
    websocket: WebSocket,
    code: ErrorCode,
    message: str,
    *,
    retryable: bool,
    details: dict[str, Any] | None = None,
) -> None:
    """HTTP 와 동일한 `ErrorEnvelope` 를 error 봉투로 보낸다. 연결은 유지한다."""

    envelope = ErrorEnvelope(
        request_id=get_request_id(),
        error=ErrorBody(
            code=code,
            message=message,
            retryable=retryable,
            details=details or {},
        ),
    )
    await _send(websocket, {"type": "error", "payload": envelope.model_dump(mode="json")})


async def _send(websocket: WebSocket, message: dict[str, Any]) -> None:
    """이미 끊긴 소켓에 쓰다가 루프가 죽지 않도록 전송 실패를 흡수한다."""

    try:
        await websocket.send_json(message)
    except (WebSocketDisconnect, RuntimeError):
        return


def _candidate_request_id(payload: object) -> str:
    """검증 전 페이로드에서 상관용 request_id 를 최선 노력으로 얻는다."""

    if isinstance(payload, dict):
        value = payload.get("request_id")
        if isinstance(value, str) and value:
            return value
    return "request-unavailable"


def _validation_issues(error: ValidationError) -> list[dict[str, str]]:
    """HTTP `handle_validation_error` 와 같은 형태로 검증 실패를 요약한다."""

    return [
        {
            "location": ".".join(str(part) for part in issue["loc"]),
            "type": issue["type"],
        }
        for issue in error.errors()
    ][:16]
