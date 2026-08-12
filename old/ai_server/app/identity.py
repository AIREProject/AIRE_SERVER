"""Bearer 토큰 검증이 만들어 내는 인증된 신원.

`ChatRequest` 가 자기신고한 `profile_id`/`device_id` 를 담을 수 있는 이유는 편의상이고,
실제 신원은 항상 이 타입이다 — `validate_claims` 가 둘을 대조해 불일치를 걸러낸다.
"""

from dataclasses import dataclass
from enum import StrEnum

from app.errors import IdentityScopeMismatchError


class DeviceRole(StrEnum):
    GAME_CLIENT = "GameClient"
    WEB_CLIENT = "WebClient"


@dataclass(frozen=True, slots=True)
class AuthenticatedDevice:
    profile_id: str
    device_id: str
    role: DeviceRole

    def validate_claims(self, profile_id: str | None, device_id: str | None) -> None:
        if profile_id is not None and profile_id != self.profile_id:
            raise IdentityScopeMismatchError(
                "Client profile claim does not match authenticated identity."
            )
        if device_id is not None and device_id != self.device_id:
            raise IdentityScopeMismatchError(
                "Client device claim does not match authenticated identity."
            )
