"""`/api/v1/devices` 가 주고받는 계약. `app/models.py` 의 채팅 계약과는 별개다."""

from datetime import datetime
from typing import Literal

from pydantic import Field

from app.identity import DeviceRole
from app.models import StableId, StrictModel


class RegisterGameRequest(StrictModel):
    request_id: StableId


class CreatePairingCodeRequest(StrictModel):
    request_id: StableId


class PairDeviceRequest(StrictModel):
    request_id: StableId
    pairing_code: str = Field(pattern=r"^[0-9]{8}$")


class DeviceView(StrictModel):
    device_id: StableId
    role: DeviceRole
    created_at: datetime
    last_used_at: datetime | None = None
    revoked_at: datetime | None = None


class DeviceTokenResponse(StrictModel):
    request_id: StableId
    profile_id: StableId
    device: DeviceView
    device_token: str = Field(min_length=32, max_length=512)


class PairingCodeResponse(StrictModel):
    request_id: StableId
    pairing_code: str = Field(pattern=r"^[0-9]{8}$")
    expires_at: datetime


class DeviceListResponse(StrictModel):
    request_id: StableId
    devices: list[DeviceView]


class DeviceSelfResponse(StrictModel):
    request_id: StableId
    profile_id: StableId
    device_id: StableId
    role: Literal["WebClient"] = "WebClient"
    status: Literal["Active"] = "Active"


class DeviceRevocationResponse(StrictModel):
    request_id: StableId
    device_id: StableId
    status: Literal["Revoked"] = "Revoked"
