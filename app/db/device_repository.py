from datetime import datetime
from typing import Any, cast

from sqlalchemy import CursorResult, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import DeviceModel, PairingCodeModel, ProfileModel
from app.errors import DeviceWriteConflictError


class SqlAlchemyDeviceRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def find_device_by_creation_request(self, request_id: str) -> DeviceModel | None:
        result = await self._session.execute(
            select(DeviceModel).where(DeviceModel.creation_request_id == request_id)
        )
        return result.scalar_one_or_none()

    async def find_pairing_code_by_issue_request(
        self, profile_id: str, request_id: str
    ) -> PairingCodeModel | None:
        result = await self._session.execute(
            select(PairingCodeModel).where(
                PairingCodeModel.profile_id == profile_id,
                PairingCodeModel.issue_request_id == request_id,
            )
        )
        return result.scalar_one_or_none()

    async def find_pairing_code_by_redemption_request(
        self, request_id: str
    ) -> PairingCodeModel | None:
        result = await self._session.execute(
            select(PairingCodeModel).where(PairingCodeModel.redeemed_request_id == request_id)
        )
        return result.scalar_one_or_none()

    async def list_recent_pairing_codes(self, not_before: datetime) -> list[PairingCodeModel]:
        """제시된 코드와 대조할 후보. **전체 이력이 아니다.**

        코드는 해시로만 저장되므로 인덱스로 찾을 수 없고 후보를 하나씩 HMAC 비교해야
        한다. 그래서 후보 집합에 시간 상한을 둔다 — 그러지 않으면 발급 이력이 쌓일수록
        페어링 시도 한 번의 비용이 계속 늘어난다. `not_before` 보다 오래 전에 만료된
        코드는 후보에서 빠지고, 그 결과 `InvalidPairingCode` 가 된다(만료 직후라면
        여전히 `ExpiredPairingCode` 로 정확히 답한다).
        """

        result = await self._session.execute(
            select(PairingCodeModel)
            .where(PairingCodeModel.expires_at > not_before)
            .order_by(PairingCodeModel.created_at.desc())
        )
        return list(result.scalars())

    async def redeem_pairing_code(
        self,
        *,
        pairing_code_id: str,
        used_at: datetime,
        redeemed_request_id: str,
        paired_device_id: str,
    ) -> bool:
        """아직 쓰이지 않은 코드만 사용 처리한다. 이미 쓰였으면 `False`.

        `used_at` 을 읽어 확인하고 나중에 쓰면, 두 요청이 모두 "미사용" 을 읽고 각자
        디바이스를 만들 수 있다. 조건을 `WHERE used_at IS NULL` 로 옮겨 판정과 기록을 한
        문장에 담는다 — 갱신된 행이 0이면 다른 요청이 먼저 썼다는 뜻이다.
        """

        # DML 의 실제 반환은 `CursorResult` 지만 `AsyncSession.execute` 의 시그니처는
        # `Result` 로 넓게 잡혀 있어 `rowcount` 가 보이지 않는다.
        result = cast(
            "CursorResult[Any]",
            await self._session.execute(
                update(PairingCodeModel)
                .where(
                    PairingCodeModel.pairing_code_id == pairing_code_id,
                    PairingCodeModel.used_at.is_(None),
                )
                .values(
                    used_at=used_at,
                    redeemed_request_id=redeemed_request_id,
                    paired_device_id=paired_device_id,
                )
            ),
        )
        return result.rowcount == 1

    async def lock_profile(self, profile_id: str) -> None:
        """프로필 행에 쓰기 잠금을 건다. 값은 바꾸지 않는다.

        디바이스 캡 판정(COUNT → INSERT)을 직렬화하기 위한 것이다. 잠금이 없으면 두 동시
        요청이 같은 개수를 읽고 둘 다 통과한다 — 실제로 재현된다. SQLAlchemy 의 SQLite
        드라이버는 첫 DML 까지 `BEGIN` 을 미루므로 앞선 SELECT 들은 트랜잭션 밖에서 돌고,
        따라서 스냅샷 충돌조차 나지 않는다. 이 no-op UPDATE 가 트랜잭션의 첫 쓰기가 되어
        SQLite 는 데이터베이스 쓰기 락을, PostgreSQL 은 해당 행의 배타 락을 커밋까지 잡는다.
        """

        await self._session.execute(
            update(ProfileModel)
            .where(ProfileModel.profile_id == profile_id)
            .values(created_at=ProfileModel.created_at)
        )

    async def list_devices(self, profile_id: str) -> list[DeviceModel]:
        result = await self._session.execute(
            select(DeviceModel)
            .where(DeviceModel.profile_id == profile_id)
            .order_by(DeviceModel.created_at)
        )
        return list(result.scalars())

    async def count_active_devices(self, profile_id: str) -> int:
        """디바이스 캡 판정용. 해지된 디바이스는 자리를 차지하지 않는다."""

        devices = await self.list_devices(profile_id)
        return sum(1 for device in devices if device.revoked_at is None)

    async def get_device(self, device_id: str) -> DeviceModel | None:
        return await self._session.get(DeviceModel, device_id)

    async def create_profile(self, profile_id: str, created_at: datetime) -> None:
        self._session.add(ProfileModel(profile_id=profile_id, created_at=created_at))

    async def create_device(self, **values: object) -> DeviceModel:
        device = DeviceModel(**values)
        self._session.add(device)
        return device

    async def create_pairing_code(self, **values: object) -> PairingCodeModel:
        pairing_code = PairingCodeModel(
            **values,
            used_at=None,
            redeemed_request_id=None,
            paired_device_id=None,
        )
        self._session.add(pairing_code)
        return pairing_code

    async def flush(self) -> None:
        try:
            await self._session.flush()
        except IntegrityError as error:
            raise DeviceWriteConflictError from error

    async def commit(self) -> None:
        try:
            await self._session.commit()
        except IntegrityError as error:
            raise DeviceWriteConflictError from error

    async def rollback(self) -> None:
        await self._session.rollback()
