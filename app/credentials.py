"""디바이스 토큰·페어링 코드와 플레이어 범위 키를 HMAC으로 만든다.

현재 단일 플레이어 demo는 별도 설정 없이 실행되어야 하므로 pepper가 비어 있으면 고정 demo
키를 사용한다. `DEVICE_CREDENTIAL_PEPPER`를 설정하면 기존처럼 그 값을 우선한다.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
from secrets import compare_digest
from typing import TYPE_CHECKING

from pydantic import SecretStr

if TYPE_CHECKING:
    from app.settings import Settings


DEMO_CREDENTIAL_PEPPER = SecretStr("AIRE_OPEN_DEMO_CREDENTIAL_PEPPER")


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

    configured = settings.device_credential_pepper
    if configured is not None and configured.get_secret_value():
        return CredentialProtector(configured)
    return CredentialProtector(DEMO_CREDENTIAL_PEPPER)
