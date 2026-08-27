"""Deterministic, source-private Kakao identity provisioning."""

from __future__ import annotations

import json
from datetime import UTC, datetime

from pydantic import SecretStr
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.credentials import CredentialProtector
from app.db.models import DeviceModel, ProfileModel
from app.identity import AuthenticatedDevice, DeviceRole


class KakaoIdentityService:
    def __init__(self, session: AsyncSession, pepper: SecretStr) -> None:
        self._session = session
        self._protector = CredentialProtector(pepper)

    async def resolve(self, *, bot_id: str, user_type: str, user_id: str) -> AuthenticatedDevice:
        subject = json.dumps(
            [bot_id, user_type, user_id],
            ensure_ascii=False,
            separators=(",", ":"),
        )
        digest = self._protector.hash_value("kakao-subject", subject)
        profile_id = f"profile-kakao-{digest}"
        device_id = f"device-kakao-{digest}"
        now = datetime.now(UTC)

        try:
            profile = await self._session.get(ProfileModel, profile_id)
            if profile is None:
                self._session.add(ProfileModel(profile_id=profile_id, created_at=now))
                await self._session.flush()

            device = await self._session.get(DeviceModel, device_id)
            if device is None:
                device = DeviceModel(
                    device_id=device_id,
                    profile_id=profile_id,
                    role=DeviceRole.WEB_CLIENT.value,
                    token_lookup_id=f"kakao-{digest}",
                    token_hash=self._protector.hash_value("kakao-virtual-device", digest),
                    creation_request_id=f"KAKAO_{digest}",
                    game_registration_key=None,
                    created_at=now,
                    last_used_at=now,
                    revoked_at=None,
                )
                self._session.add(device)
            else:
                self._validate_device(device, profile_id)
                device.last_used_at = now
            await self._session.commit()
        except IntegrityError:
            await self._session.rollback()
            device = await self._session.get(DeviceModel, device_id)
            if device is None or await self._session.get(ProfileModel, profile_id) is None:
                raise
            self._validate_device(device, profile_id)
            device.last_used_at = now
            await self._session.commit()

        return AuthenticatedDevice(
            profile_id=profile_id,
            device_id=device_id,
            role=DeviceRole.WEB_CLIENT,
        )

    @staticmethod
    def _validate_device(device: DeviceModel, profile_id: str) -> None:
        if (
            device.profile_id != profile_id
            or device.role != DeviceRole.WEB_CLIENT.value
            or device.revoked_at is not None
        ):
            raise RuntimeError("Kakao integration identity is unavailable.")
