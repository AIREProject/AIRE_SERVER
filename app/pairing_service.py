"""디바이스 등록 → 페어링 코드 발급 → 페어링 → 조회/해지.

`cd0be55:app/application/pairing_service.py` 를 개조했다. 달라진 점은 딱 하나,
`_new_device` 가 새 디바이스를 만들기 전에 `max_devices_per_profile` 캡을 검사한다는
것뿐이다 — 초과하면 자동 해지 없이 거부한다.
"""

from datetime import UTC, datetime, timedelta
from uuid import uuid4

from app.credentials import CredentialProtector
from app.db.device_repository import SqlAlchemyDeviceRepository
from app.db.models import DeviceModel, PairingCodeModel
from app.errors import (
    DeviceLimitExceededError,
    DeviceNotFoundError,
    DeviceRoleNotAllowedError,
    DeviceWriteConflictError,
    DuplicateRequestError,
    ExpiredPairingCodeError,
    IdentityScopeMismatchError,
    InvalidPairingCodeError,
    UsedPairingCodeError,
)
from app.identity import AuthenticatedDevice, DeviceRole
from app.pairing_models import (
    CreatePairingCodeRequest,
    DeviceListResponse,
    DeviceRevocationResponse,
    DeviceSelfResponse,
    DeviceTokenResponse,
    DeviceView,
    PairDeviceRequest,
    PairingCodeResponse,
    RegisterGameRequest,
)


class PairingService:
    def __init__(
        self,
        repository: SqlAlchemyDeviceRepository,
        protector: CredentialProtector,
        *,
        pairing_code_ttl_seconds: int,
        max_devices_per_profile: int,
    ) -> None:
        self._repository = repository
        self._protector = protector
        self._pairing_code_ttl_seconds = pairing_code_ttl_seconds
        self._max_devices_per_profile = max_devices_per_profile

    async def register_game(self, request: RegisterGameRequest) -> DeviceTokenResponse:
        existing = await self._repository.find_device_by_creation_request(request.request_id)
        if existing is not None:
            if existing.role != DeviceRole.GAME_CLIENT.value:
                raise DuplicateRequestError
            return self._token_response(request.request_id, existing)

        # register-game 호출마다 새 프로필 + 새 GameClient 를 만든다. 플레이어(프로필)당
        # GameClient 는 하나로 제한되지만(uq_devices_game_registration_per_profile), 프로필
        # 수 자체는 제한이 없다.
        now = datetime.now(UTC)
        profile_id = f"profile-{uuid4()}"
        await self._repository.create_profile(profile_id, now)
        await self._repository.flush()
        device = await self._new_device(profile_id, DeviceRole.GAME_CLIENT, request.request_id, now)
        try:
            await self._repository.commit()
        except DeviceWriteConflictError as error:
            # 여기 도달 가능한 충돌은 creation_request_id 유니크 위반뿐이다 — 각 호출의
            # game_registration_key(= 새 profile-<uuid4>)는 구조적으로 유일하기 때문이다.
            await self._repository.rollback()
            existing = await self._repository.find_device_by_creation_request(request.request_id)
            if existing is not None:
                if existing.role != DeviceRole.GAME_CLIENT.value:
                    raise DuplicateRequestError from error
                return self._token_response(request.request_id, existing)
            raise
        return self._token_response(request.request_id, device)

    async def create_pairing_code(
        self,
        request: CreatePairingCodeRequest,
        identity: AuthenticatedDevice,
    ) -> PairingCodeResponse:
        self._require_game(identity)
        existing = await self._repository.find_pairing_code_by_issue_request(
            identity.profile_id, request.request_id
        )
        if existing is not None:
            return self._pairing_code_response(request.request_id, existing)

        now = datetime.now(UTC)
        pairing_code_id = f"pairing-{uuid4()}"
        code = self._protector.make_pairing_code(pairing_code_id, request.request_id)
        model = await self._repository.create_pairing_code(
            pairing_code_id=pairing_code_id,
            profile_id=identity.profile_id,
            issuing_device_id=identity.device_id,
            code_hash=self._protector.hash_value("pairing-code", code),
            issue_request_id=request.request_id,
            expires_at=now + timedelta(seconds=self._pairing_code_ttl_seconds),
            created_at=now,
        )
        try:
            await self._repository.commit()
        except DeviceWriteConflictError:
            await self._repository.rollback()
            existing = await self._repository.find_pairing_code_by_issue_request(
                identity.profile_id, request.request_id
            )
            if existing is None:
                raise
            return self._pairing_code_response(request.request_id, existing)
        return self._pairing_code_response(request.request_id, model)

    async def pair_device(self, request: PairDeviceRequest) -> DeviceTokenResponse:
        retried = await self._repository.find_pairing_code_by_redemption_request(request.request_id)
        if retried is not None:
            if not self._protector.verify("pairing-code", request.pairing_code, retried.code_hash):
                raise InvalidPairingCodeError
            if retried.paired_device_id is None:
                raise InvalidPairingCodeError
            device = await self._repository.get_device(retried.paired_device_id)
            if device is None:
                raise DeviceNotFoundError
            return self._token_response(request.request_id, device)

        now = datetime.now(UTC)
        pairing_code = await self._match_pairing_code(request.pairing_code, now)
        if pairing_code is None:
            raise InvalidPairingCodeError
        if self._as_utc(pairing_code.expires_at) <= now:
            raise ExpiredPairingCodeError
        if pairing_code.used_at is not None:
            raise UsedPairingCodeError

        device = await self._new_device(
            pairing_code.profile_id, DeviceRole.WEB_CLIENT, request.request_id, now
        )
        try:
            await self._repository.flush()
            # 위의 `used_at is not None` 검사만으로는 부족하다 — 읽은 뒤 여기 오기까지
            # 다른 요청이 같은 코드를 쓸 수 있다. 조건을 UPDATE 안으로 옮겨 다시 판정하고,
            # 졌으면 방금 만든 디바이스까지 통째로 되돌린다(같은 트랜잭션이다).
            redeemed = await self._repository.redeem_pairing_code(
                pairing_code_id=pairing_code.pairing_code_id,
                used_at=now,
                redeemed_request_id=request.request_id,
                paired_device_id=device.device_id,
            )
            if not redeemed:
                await self._repository.rollback()
                raise UsedPairingCodeError
            await self._repository.commit()
        except DeviceWriteConflictError as error:
            await self._repository.rollback()
            retried = await self._repository.find_pairing_code_by_redemption_request(
                request.request_id
            )
            if retried is None or retried.paired_device_id is None:
                raise DuplicateRequestError from error
            existing_device = await self._repository.get_device(retried.paired_device_id)
            if existing_device is None:
                raise DeviceNotFoundError from error
            return self._token_response(request.request_id, existing_device)
        return self._token_response(request.request_id, device)

    async def list_devices(
        self, request_id: str, identity: AuthenticatedDevice
    ) -> DeviceListResponse:
        self._require_game(identity)
        devices = await self._repository.list_devices(identity.profile_id)
        return DeviceListResponse(
            request_id=request_id,
            devices=[self._device_view(device) for device in devices],
        )

    async def get_current_device(
        self, request_id: str, identity: AuthenticatedDevice
    ) -> DeviceSelfResponse:
        self._require_web(identity)
        return DeviceSelfResponse(
            request_id=request_id,
            profile_id=identity.profile_id,
            device_id=identity.device_id,
        )

    async def revoke_current_device(
        self, request_id: str, identity: AuthenticatedDevice
    ) -> DeviceRevocationResponse:
        self._require_web(identity)
        device = await self._repository.get_device(identity.device_id)
        if device is None:
            raise DeviceNotFoundError
        if device.profile_id != identity.profile_id:
            raise IdentityScopeMismatchError
        if device.revoked_at is None:
            device.revoked_at = datetime.now(UTC)
            await self._repository.commit()
        return DeviceRevocationResponse(request_id=request_id, device_id=identity.device_id)

    async def revoke_device(
        self, request_id: str, device_id: str, identity: AuthenticatedDevice
    ) -> DeviceRevocationResponse:
        self._require_game(identity)
        device = await self._repository.get_device(device_id)
        if device is None:
            raise DeviceNotFoundError
        if device.profile_id != identity.profile_id:
            raise IdentityScopeMismatchError
        if device.role != DeviceRole.WEB_CLIENT.value:
            raise DeviceRoleNotAllowedError
        if device.revoked_at is None:
            device.revoked_at = datetime.now(UTC)
            await self._repository.commit()
        return DeviceRevocationResponse(request_id=request_id, device_id=device_id)

    async def _match_pairing_code(self, raw_code: str, now: datetime) -> PairingCodeModel | None:
        """제시된 코드와 후보들을 HMAC 으로 대조한다.

        후보는 만료 후 TTL 만큼의 유예 안에 있는 것들뿐이다(`list_recent_pairing_codes`).
        전체 이력을 훑으면 발급이 쌓일수록 시도 한 번이 비싸지고, 오래된 코드를 굳이
        구분해 줄 이유도 없다. 일치가 여럿이어도 첫 번째만 쓴다 — 순회를 조기 종료하지
        않는 것은 비교 횟수를 코드 값과 무관하게 유지하기 위해서다.
        """

        not_before = now - timedelta(seconds=self._pairing_code_ttl_seconds)
        match = None
        for candidate in await self._repository.list_recent_pairing_codes(not_before):
            if (
                self._protector.verify("pairing-code", raw_code, candidate.code_hash)
                and match is None
            ):
                match = candidate
        return match

    async def _new_device(
        self,
        profile_id: str,
        role: DeviceRole,
        request_id: str,
        now: datetime,
    ) -> DeviceModel:
        # 캡 판정 전에 프로필 행을 잠근다. 잠그지 않으면 두 동시 요청이 같은 개수를 읽고
        # 각자 INSERT 해 캡을 넘긴다 — 서로 다른 행이라 유니크 제약도 걸리지 않는다.
        await self._repository.lock_profile(profile_id)
        if await self._repository.count_active_devices(profile_id) >= (
            self._max_devices_per_profile
        ):
            raise DeviceLimitExceededError

        device_id = f"device-{uuid4()}"
        lookup_id = f"token-{uuid4()}"
        token = self._protector.make_device_token(
            lookup_id=lookup_id,
            device_id=device_id,
            creation_request_id=request_id,
        )
        return await self._repository.create_device(
            device_id=device_id,
            profile_id=profile_id,
            role=role.value,
            token_lookup_id=lookup_id,
            token_hash=self._protector.hash_value("device-token", token),
            creation_request_id=request_id,
            created_at=now,
            game_registration_key=(profile_id if role is DeviceRole.GAME_CLIENT else None),
        )

    def _token_response(self, request_id: str, device: DeviceModel) -> DeviceTokenResponse:
        token = self._protector.make_device_token(
            lookup_id=device.token_lookup_id,
            device_id=device.device_id,
            creation_request_id=device.creation_request_id,
        )
        return DeviceTokenResponse(
            request_id=request_id,
            profile_id=device.profile_id,
            device=self._device_view(device),
            device_token=token,
        )

    def _pairing_code_response(
        self, request_id: str, model: PairingCodeModel
    ) -> PairingCodeResponse:
        return PairingCodeResponse(
            request_id=request_id,
            pairing_code=self._protector.make_pairing_code(
                model.pairing_code_id, model.issue_request_id
            ),
            expires_at=self._as_utc(model.expires_at),
        )

    @staticmethod
    def _device_view(device: DeviceModel) -> DeviceView:
        return DeviceView(
            device_id=device.device_id,
            role=DeviceRole(device.role),
            created_at=PairingService._as_utc(device.created_at),
            last_used_at=(
                PairingService._as_utc(device.last_used_at)
                if device.last_used_at is not None
                else None
            ),
            revoked_at=(
                PairingService._as_utc(device.revoked_at) if device.revoked_at is not None else None
            ),
        )

    @staticmethod
    def _require_game(identity: AuthenticatedDevice) -> None:
        if identity.role is not DeviceRole.GAME_CLIENT:
            raise DeviceRoleNotAllowedError

    @staticmethod
    def _require_web(identity: AuthenticatedDevice) -> None:
        if identity.role is not DeviceRole.WEB_CLIENT:
            raise DeviceRoleNotAllowedError

    @staticmethod
    def _as_utc(value: datetime) -> datetime:
        if value.tzinfo is None:
            return value.replace(tzinfo=UTC)
        return value.astimezone(UTC)
