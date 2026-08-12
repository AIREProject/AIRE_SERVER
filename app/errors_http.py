import logging

from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from app.errors import (
    AdminAuthenticationUnavailableError,
    AdminChildReferenceExistsError,
    AdminDuplicateKeyError,
    AdminResourceNotFoundError,
    AIServiceInvalidOutputError,
    AIServiceTimeoutError,
    AIServiceUnavailableError,
    APIError,
    AuthenticationUnavailableError,
    DeviceLimitExceededError,
    DeviceNotFoundError,
    DeviceRoleNotAllowedError,
    DuplicateRequestError,
    ErrorBody,
    ErrorCode,
    ErrorEnvelope,
    ExpiredPairingCodeError,
    GameStateNotFoundError,
    GameStateVersionConflictError,
    IdentityScopeMismatchError,
    InvalidPairingCodeError,
    OfflineTaskInvalidRequestError,
    OfflineTaskNotFoundError,
    OfflineTaskTransitionError,
    RequestBodyTooLargeError,
    UnauthorizedAdminError,
    UnauthorizedDeviceError,
    UnknownCompanionError,
    UsedPairingCodeError,
)
from app.middleware import get_request_id

logger = logging.getLogger("aire.backend")

# 애플리케이션 오류 → (status_code, code, message, retryable).
# HTTP 예외 핸들러와 WebSocket 메시지 루프가 공유한다(WS 는 status_code 를 쓰지 않는다).
APPLICATION_ERROR_MAP: dict[type[Exception], tuple[int, ErrorCode, str, bool]] = {
    AIServiceUnavailableError: (
        503,
        ErrorCode.AI_SERVICE_UNAVAILABLE,
        "AI service is currently unavailable.",
        True,
    ),
    AIServiceTimeoutError: (
        504,
        ErrorCode.AI_SERVICE_TIMEOUT,
        "AI service exceeded the configured timeout.",
        True,
    ),
    AIServiceInvalidOutputError: (
        503,
        ErrorCode.AI_SERVICE_INVALID_OUTPUT,
        "AI service returned invalid structured output.",
        True,
    ),
    UnauthorizedDeviceError: (
        401,
        ErrorCode.UNAUTHORIZED_DEVICE,
        "Device bearer token is missing or invalid.",
        False,
    ),
    AuthenticationUnavailableError: (
        503,
        ErrorCode.AUTHENTICATION_UNAVAILABLE,
        "Device authentication is not configured.",
        True,
    ),
    DeviceLimitExceededError: (
        403,
        ErrorCode.DEVICE_LIMIT_EXCEEDED,
        "This profile has reached its device limit.",
        False,
    ),
    DeviceNotFoundError: (
        404,
        ErrorCode.DEVICE_NOT_FOUND,
        "The requested device was not found.",
        False,
    ),
    DeviceRoleNotAllowedError: (
        403,
        ErrorCode.DEVICE_ROLE_NOT_ALLOWED,
        "Device role is not allowed for this action.",
        False,
    ),
    DuplicateRequestError: (
        409,
        ErrorCode.DUPLICATE_REQUEST,
        "The request ID was already used with different content.",
        False,
    ),
    IdentityScopeMismatchError: (
        403,
        ErrorCode.IDENTITY_SCOPE_MISMATCH,
        "Requested identity scope does not match the authenticated device.",
        False,
    ),
    InvalidPairingCodeError: (
        400,
        ErrorCode.INVALID_PAIRING_CODE,
        "The pairing code is invalid.",
        False,
    ),
    ExpiredPairingCodeError: (
        410,
        ErrorCode.EXPIRED_PAIRING_CODE,
        "The pairing code has expired.",
        False,
    ),
    UsedPairingCodeError: (
        409,
        ErrorCode.USED_PAIRING_CODE,
        "The pairing code has already been used.",
        False,
    ),
    OfflineTaskNotFoundError: (
        404,
        ErrorCode.OFFLINE_TASK_NOT_FOUND,
        "The offline task was not found.",
        False,
    ),
    OfflineTaskTransitionError: (
        409,
        ErrorCode.OFFLINE_TASK_TRANSITION,
        "The offline task cannot make that state transition.",
        False,
    ),
    OfflineTaskInvalidRequestError: (
        400,
        ErrorCode.OFFLINE_TASK_INVALID_REQUEST,
        "The offline task request is invalid.",
        False,
    ),
    UnknownCompanionError: (
        400,
        ErrorCode.UNKNOWN_COMPANION,
        "companion_id is not recognized.",
        False,
    ),
    GameStateNotFoundError: (
        404,
        ErrorCode.GAME_STATE_NOT_FOUND,
        "The requested Game State Snapshot was not found.",
        False,
    ),
    GameStateVersionConflictError: (
        409,
        ErrorCode.GAME_STATE_VERSION_CONFLICT,
        "The Game State version is not newer than the stored version.",
        False,
    ),
    UnauthorizedAdminError: (
        401,
        ErrorCode.UNAUTHORIZED_ADMIN,
        "Admin bearer token is missing or invalid.",
        False,
    ),
    AdminAuthenticationUnavailableError: (
        503,
        ErrorCode.ADMIN_AUTHENTICATION_UNAVAILABLE,
        "Admin authentication is not configured.",
        True,
    ),
    AdminResourceNotFoundError: (
        404,
        ErrorCode.ADMIN_RESOURCE_NOT_FOUND,
        "The requested admin resource was not found.",
        False,
    ),
    AdminChildReferenceExistsError: (
        409,
        ErrorCode.ADMIN_CHILD_REFERENCE_EXISTS,
        "The resource still has referencing rows and cannot be deleted.",
        False,
    ),
    AdminDuplicateKeyError: (
        409,
        ErrorCode.ADMIN_DUPLICATE_KEY,
        "A resource with this primary key already exists.",
        False,
    ),
}


def _error_response(
    *,
    status_code: int,
    code: ErrorCode,
    message: str,
    retryable: bool,
    details: dict[str, object] | None = None,
) -> JSONResponse:
    envelope = ErrorEnvelope(
        request_id=get_request_id(),
        error=ErrorBody(
            code=code,
            message=message,
            retryable=retryable,
            details=details or {},
        ),
    )
    return JSONResponse(
        status_code=status_code,
        content=envelope.model_dump(mode="json"),
    )


def register_error_handlers(app: FastAPI) -> None:
    application_errors = APPLICATION_ERROR_MAP

    async def handle_application_error(
        _request: Request,
        error: Exception,
    ) -> JSONResponse:
        status_code, code, message, retryable = application_errors[type(error)]
        return _error_response(
            status_code=status_code,
            code=code,
            message=message,
            retryable=retryable,
        )

    for error_class in application_errors:
        app.add_exception_handler(error_class, handle_application_error)

    @app.exception_handler(APIError)
    async def handle_api_error(_request: Request, error: APIError) -> JSONResponse:
        return _error_response(
            status_code=error.status_code,
            code=error.code,
            message=error.message,
            retryable=error.retryable,
            details=error.details,
        )

    @app.exception_handler(RequestBodyTooLargeError)
    async def handle_large_body(
        _request: Request,
        _error: RequestBodyTooLargeError,
    ) -> JSONResponse:
        return _error_response(
            status_code=413,
            code=ErrorCode.REQUEST_TOO_LARGE,
            message="Request body exceeds the configured size limit.",
            retryable=False,
        )

    @app.exception_handler(RequestValidationError)
    async def handle_validation_error(
        _request: Request,
        error: RequestValidationError,
    ) -> JSONResponse:
        issues = [
            {
                "location": ".".join(str(part) for part in issue["loc"]),
                "type": issue["type"],
            }
            for issue in error.errors()
        ]
        return _error_response(
            status_code=400,
            code=ErrorCode.INVALID_REQUEST,
            message="Request validation failed.",
            retryable=False,
            details={"issues": issues[:16]},
        )

    @app.exception_handler(HTTPException)
    async def handle_http_error(
        _request: Request,
        error: HTTPException,
    ) -> JSONResponse:
        return _error_response(
            status_code=error.status_code,
            code=ErrorCode.INVALID_REQUEST,
            message="HTTP request could not be processed.",
            retryable=False,
        )

    @app.exception_handler(Exception)
    async def handle_unexpected_error(
        _request: Request,
        error: Exception,
    ) -> JSONResponse:
        logger.error(
            "unhandled_request_error",
            extra={
                "event": "unhandled_request_error",
                "request_id": get_request_id(),
                "error_type": type(error).__name__,
            },
        )
        return _error_response(
            status_code=500,
            code=ErrorCode.INTERNAL_ERROR,
            message="An internal server error occurred.",
            retryable=True,
        )
