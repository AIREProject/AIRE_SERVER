"""관리자 CRUD 가 SQLAlchemy 세션에 대해 하는 일 — 11개 테이블에 기계적으로 동일하다.

필드 노출 통제(민감 컬럼 제외)와 테이블별 검증은 여기 없다 — 그건 `app/admin_models.py`
스키마와 `app/admin_service.py`의 몫이다. 이 계층은 `session.get`/`select`/`add`/`delete` 를
테이블 이름 없이 반복하지 않으려고 있을 뿐이다.
"""

from collections.abc import Sequence
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession


class AdminRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get(self, model: type[Any], pk: object) -> Any | None:
        return await self._session.get(model, pk)

    async def list(self, model: type[Any], *, limit: int, offset: int) -> Sequence[Any]:
        result = await self._session.execute(select(model).limit(limit).offset(offset))
        return result.scalars().all()

    async def create(self, model: type[Any], values: dict[str, object]) -> Any:
        instance = model(**values)
        self._session.add(instance)
        await self._session.flush()
        return instance

    async def create_many(
        self, model: type[Any], values_list: Sequence[dict[str, object]]
    ) -> Sequence[Any]:
        instances = [model(**values) for values in values_list]
        self._session.add_all(instances)
        await self._session.flush()
        return instances

    async def update(self, instance: Any, values: dict[str, object]) -> Any:
        for key, value in values.items():
            setattr(instance, key, value)
        await self._session.flush()
        return instance

    async def delete(self, instance: Any) -> None:
        await self._session.delete(instance)
        await self._session.flush()

    async def count_by_fk(self, child_model: type[Any], fk_column: str, value: object) -> int:
        column = getattr(child_model, fk_column)
        result = await self._session.execute(
            select(func.count()).select_from(child_model).where(column == value)
        )
        return int(result.scalar_one())

    async def commit(self) -> None:
        await self._session.commit()

    async def rollback(self) -> None:
        await self._session.rollback()
