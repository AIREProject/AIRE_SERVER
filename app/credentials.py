"""디바이스 토큰·페어링 코드를 HMAC 으로 만들고 검증한다.

`settings.device_credential_pepper` 를 키로 쓴다 — 비어 있으면 인증 전체가 동작할 수
없으므로 즉시 `CredentialProtectionNotConfiguredError` 로 실패한다(조용히 약한 키로
넘어가지 않는다).
"""

from __future__ import annotations

import base64
import hashlib
import hmac
from secrets import compare_digest
from typing import TYPE_CHECKING

from pydantic import SecretStr

from app.errors import AuthenticationUnavailableError

if TYPE_CHECKING:
    from app.settings import Settings


class CredentialProtectionNotConfiguredError(RuntimeError):
    pass


class CredentialProtector:
    def __init__(self, pepper: SecretStr | None) -> None:
        if pepper is None or not pepper.get_secret_value():
            raise CredentialProtectionNotConfiguredError(
                "Device credential protection is not configured."
            )
        self._key = pepper.get_secret_value().encode("utf-8")

    def make_device_token(
        self,
        *,
        lookup_id: str,
        device_id: str,
        creation_request_id: str,
    ) -> str:
        secret = self._digest(f"device-secret:{device_id}:{creation_request_id}")
        encoded = base64.urlsafe_b64encode(secret).decode("ascii").rstrip("=")
        return f"{lookup_id}.{encoded}"

    def make_pairing_code(self, pairing_code_id: str, issue_request_id: str) -> str:
        digest = self._digest(f"pairing-code:{pairing_code_id}:{issue_request_id}")
        return f"{int.from_bytes(digest[:8], 'big') % 100_000_000:08d}"

    def hash_value(self, purpose: str, value: str) -> str:
        return self._digest(f"{purpose}:{value}").hex()

    def verify(self, purpose: str, value: str, expected_hash: str) -> bool:
        return compare_digest(self.hash_value(purpose, value), expected_hash)

    def _digest(self, value: str) -> bytes:
        return hmac.new(self._key, value.encode("utf-8"), hashlib.sha256).digest()


def build_credential_protector(settings: Settings) -> CredentialProtector:
    """트랜스포트에 독립적인 protector 조립. `service.py`, `dependencies.py`, WS 가 공유한다."""

    try:
        return CredentialProtector(settings.device_credential_pepper)
    except CredentialProtectionNotConfiguredError as error:
        raise AuthenticationUnavailableError(
            "Persistent device authentication is not configured."
        ) from error
