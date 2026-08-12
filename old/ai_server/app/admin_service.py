"""테이블 이름에 무관하게 동작하는 관리자 CRUD 서비스.

`AdminResourceSpec` 하나만 받아 list/get/create/update/delete 를 수행한다 — 스펙이 스키마와
`prepare_create`/`prepare_update` 훅으로 테이블별 차이를 다 흡수하므로, 이 클래스는 어떤
테이블을 다루는지 몰라도 된다. `ResponseT` 는 라우트 쪽(`app/routes/admin.py`)에서 테이블별
구체 타입으로 고정해, 55개 핸들러가 매번 캐스팅하지 않고도 정확한 반환 타입을 갖는다.
"""

from collections.abc import Sequence
from typing import Any, cast

from pydantic import BaseModel
from sqlalchemy.exc import IntegrityError

from app.admin_registry import AdminResourceSpec
from app.admin_repository import AdminRepository
from app.errors import (
    AdminChildReferenceExistsError,
    AdminDuplicateKeyError,
    AdminResourceNotFoundError,
)


class AdminCrudService[ResponseT: BaseModel]:
    def __init__(self, spec: AdminResourceSpec, repository: AdminRepository) -> None:
        self._spec = spec
        self._repository = repository

    async def list_resources(self, *, limit: int, offset: int) -> list[ResponseT]:
        rows = await self._repository.list(self._spec.model, limit=limit, offset=offset)
        return [self._to_response(row) for row in rows]

    async def get_resource(self, pk: str) -> ResponseT:
        return self._to_response(await self._get_or_404(pk))

    async def create_resource(self, body: BaseModel) -> ResponseT:
        values = body.model_dump(by_alias=True)
        if self._spec.prepare_create is not None:
            values = self._spec.prepare_create(values)
        try:
            row = await self._repository.create(self._spec.model, values)
            await self._repository.commit()
        except IntegrityError as exc:
            await self._repository.rollback()
            raise AdminDuplicateKeyError(
                f"{self._spec.name}: a resource with this primary key already exists."
            ) from exc
        return self._to_response(row)

    async def create_resources(self, bodies: Sequence[BaseModel]) -> list[ResponseT]:
        values_list: list[dict[str, object]] = []
        for body in bodies:
            values = body.model_dump(by_alias=True)
            if self._spec.prepare_create is not None:
                values = self._spec.prepare_create(values)
            values_list.append(values)
        try:
            rows = await self._repository.create_many(self._spec.model, values_list)
            await self._repository.commit()
        except IntegrityError as exc:
            await self._repository.rollback()
            raise AdminDuplicateKeyError(
                f"{self._spec.name}: a batch create failed because a resource with one of "
                "these primary keys already exists."
            ) from exc
        return [self._to_response(row) for row in rows]

    async def update_resource(self, pk: str, body: BaseModel) -> ResponseT:
        row = await self._get_or_404(pk)
        values = body.model_dump(exclude_unset=True, by_alias=True)
        if self._spec.prepare_update is not None:
            values = self._spec.prepare_update(values)
        row = await self._repository.update(row, values)
        await self._repository.commit()
        return self._to_response(row)

    async def delete_resource(self, pk: str) -> None:
        row = await self._get_or_404(pk)
        for child in self._spec.non_fk_children:
            count = await self._repository.count_by_fk(child.child_model, child.fk_column, pk)
            if count > 0:
                raise AdminChildReferenceExistsError(
                    f"{self._spec.name} {pk!r} is still referenced by {child.label}."
                )
        try:
            await self._repository.delete(row)
            await self._repository.commit()
        except IntegrityError as exc:
            await self._repository.rollback()
            raise AdminChildReferenceExistsError(
                f"{self._spec.name} {pk!r} is still referenced by another row."
            ) from exc

    async def _get_or_404(self, pk: str) -> Any:
        row = await self._repository.get(self._spec.model, pk)
        if row is None:
            raise AdminResourceNotFoundError(f"{self._spec.name} {pk!r} was not found.")
        return row

    def _to_response(self, row: Any) -> ResponseT:
        return cast(
            "ResponseT",
            self._spec.response_schema.model_validate(row, from_attributes=True),
        )
