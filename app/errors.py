"""오류 타입과 클라이언트에게 나가는 오류 봉투.

HTTP 핸들러(`errors_http.py`)와 WebSocket 메시지 루프가 같은 `ErrorEnvelope` 를 쓴다.
새 오류는 여기에 타입을 더하고 `errors_http.APPLICATION_ERROR_MAP` 에 한 줄만 넣으면
두 경로에 동시에 반영된다.
"""

from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class AIServiceUnavailableError(RuntimeError):
    pass


class AIServiceTimeoutError(RuntimeError):
    pass


class AIServiceInvalidOutputError(RuntimeError):
    pass


# --- 디바이스 인증 / 페어링 (docs/temporary-scaffolds.md §2) -------------------------


class UnauthorizedDeviceError(RuntimeError):
    pass


class AuthenticationUnavailableError(RuntimeError):
    pass


class DeviceLimitExceededError(RuntimeError):
    pass


class DeviceNotFoundError(RuntimeError):
    pass


class DeviceRoleNotAllowedError(RuntimeError):
    pass


class DeviceWriteConflictError(RuntimeError):
    pass


class DuplicateRequestError(RuntimeError):
    pass


class IdentityScopeMismatchError(RuntimeError):
    pass


class InvalidPairingCodeError(RuntimeError):
    pass


class ExpiredPairingCodeError(RuntimeError):
    pass


class UsedPairingCodeError(RuntimeError):
    pass


class OfflineTaskNotFoundError(RuntimeError):
    pass


class OfflineTaskTransitionError(RuntimeError):
    pass


class OfflineTaskInvalidRequestError(RuntimeError):
    pass


class UnknownCompanionError(RuntimeError):
    pass


# --- 관리자 CRUD (app/routes/admin.py) -----------------------------------------------


class UnauthorizedAdminError(RuntimeError):
    pass


class AdminAuthenticationUnavailableError(RuntimeError):
    pass


class AdminResourceNotFoundError(RuntimeError):
    pass


class AdminChildReferenceExistsError(RuntimeError):
    pass


class AdminDuplicateKeyError(RuntimeError):
    pass


class ErrorCode(StrEnum):
    INVALID_REQUEST = "InvalidRequest"
    REQUEST_TOO_LARGE = "RequestTooLarge"
    REQUEST_TIMEOUT = "RequestTimeout"
    AI_SERVICE_UNAVAILABLE = "AIServiceUnavailable"
    AI_SERVICE_TIMEOUT = "AIServiceTimeout"
    AI_SERVICE_INVALID_OUTPUT = "AIServiceInvalidOutput"
    UNAUTHORIZED_DEVICE = "UnauthorizedDevice"
    AUTHENTICATION_UNAVAILABLE = "AuthenticationUnavailable"
    DEVICE_LIMIT_EXCEEDED = "DeviceLimitExceeded"
    DEVICE_NOT_FOUND = "DeviceNotFound"
    DEVICE_ROLE_NOT_ALLOWED = "DeviceRoleNotAllowed"
    DUPLICATE_REQUEST = "DuplicateRequest"
    IDENTITY_SCOPE_MISMATCH = "IdentityScopeMismatch"
    INVALID_PAIRING_CODE = "InvalidPairingCode"
    EXPIRED_PAIRING_CODE = "ExpiredPairingCode"
    USED_PAIRING_CODE = "UsedPairingCode"
    OFFLINE_TASK_NOT_FOUND = "OfflineTaskNotFound"
    OFFLINE_TASK_TRANSITION = "OfflineTaskTransitionNotAllowed"
    OFFLINE_TASK_INVALID_REQUEST = "OfflineTaskInvalidRequest"
    UNKNOWN_COMPANION = "UnknownCompanion"
    UNAUTHORIZED_ADMIN = "UnauthorizedAdmin"
    ADMIN_AUTHENTICATION_UNAVAILABLE = "AdminAuthenticationUnavailable"
    ADMIN_RESOURCE_NOT_FOUND = "AdminResourceNotFound"
    ADMIN_CHILD_REFERENCE_EXISTS = "AdminChildReferenceExists"
    ADMIN_DUPLICATE_KEY = "AdminDuplicateKey"
    INTERNAL_ERROR = "InternalError"


class ErrorBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    code: ErrorCode
    message: str = Field(min_length=1, max_length=512)
    retryable: bool
    details: dict[str, Any] = Field(default_factory=dict)


class ErrorEnvelope(BaseModel):
    model_config = ConfigDict(extra="forbid")

    request_id: str
    error: ErrorBody


class APIError(Exception):
    def __init__(
        self,
        *,
        status_code: int,
        code: ErrorCode,
        message: str,
        retryable: bool = False,
        details: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(code.value)
        self.status_code = status_code
        self.code = code
        self.message = message
        self.retryable = retryable
        self.details = details or {}


class RequestBodyTooLargeError(Exception):
    pass
