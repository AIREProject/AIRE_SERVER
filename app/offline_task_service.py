"""Offline_Task 생성과 게임 클라이언트 상태 전이."""

from datetime import UTC, datetime
from uuid import uuid4

from sqlalchemy.exc import IntegrityError

from app.brain.resources import MAX_GATHER_QUANTITY
from app.db.models import OfflineTaskModel
from app.db.offline_task_repository import SqlAlchemyOfflineTaskRepository
from app.errors import (
    DeviceRoleNotAllowedError,
    OfflineTaskInvalidRequestError,
    OfflineTaskNotFoundError,
    OfflineTaskTransitionError,
)
from app.gamedata.dataset import DATASET
from app.identity import AuthenticatedDevice, DeviceRole
from app.offline_task_models import (
    CreateOfflineTaskRequest,
    OfflineTaskListResponse,
    OfflineTaskResponse,
    OfflineTaskStatus,
    OfflineTaskType,
    OfflineTaskView,
)

# 정책 migration 이전 Task나 정책 행이 없는 콘텐츠에만 쓰는 legacy fallback.
# 새 지원 Task는 offline_task_policies 값을 seconds_per_item에 snapshot해 계산한다.
_GATHER_SECONDS_PER_ITEM = 600.0
_PLANT_STEM_SECONDS_PER_ITEM = 5.0


def _crafting_seconds_per_item(item_id: str | None) -> float | None:
    if item_id is None:
        return None
    for recipe in DATASET.recipes:
        if recipe.result_item_id == item_id:
            return recipe.duration_seconds * 60
    for smelting_recipe in DATASET.smelting_recipes:
        if smelting_recipe.result_item_id == item_id:
            return smelting_recipe.duration_seconds * 60
    return None


def _seconds_per_item(task_type: OfflineTaskType, item_id: str | None) -> float | None:
    if task_type is OfflineTaskType.GATHERING:
        if item_id == "PlantStem":
            return _PLANT_STEM_SECONDS_PER_ITEM
        return _GATHER_SECONDS_PER_ITEM
    if task_type is OfflineTaskType.CRAFTING:
        return _crafting_seconds_per_item(item_id)
    return None


class OfflineTaskService:
    def __init__(self, repository: SqlAlchemyOfflineTaskRepository) -> None:
        self._repository = repository

    async def create(
        self,
        request: CreateOfflineTaskRequest,
        identity: AuthenticatedDevice,
        *,
        auto_start: bool = False,
        commit: bool = True,
        task_id: str | None = None,
    ) -> OfflineTaskResponse:
        """Offline_Task를 만든다.

        `auto_start`는 채팅 기반 채집 요청(`app/service.py:_create_gather_task`)에서만
        `True`로 넘어온다 — 그 시나리오엔 `/start`를 불러줄 살아있는 GameClient가 없으므로
        `Pending` 대신 곧바로 `InProgress`로 만든다. `POST /api/v1/tasks`로 직접 만드는
        인게임 흐름은 지금처럼 `Pending`으로 시작해 GameClient가 진행시킨다.
        """

        self._require_web(identity)
        self._validate_item(request.task_type, request.item_id)
        if request.item_id is not None and not await self._repository.item_exists(request.item_id):
            raise OfflineTaskInvalidRequestError("The requested item does not exist.")

        slot = await self._repository.get_or_create_slot(
            profile_id=identity.profile_id,
            save_slot_id=request.save_slot_id,
        )
        existing = await self._repository.find_by_creation_request(
            profile_id=identity.profile_id,
            save_slot_row_id=slot.row_id,
            request_id=request.request_id,
        )
        if existing is not None:
            return OfflineTaskResponse(
                request_id=request.request_id,
                task=await self._view(existing, request.save_slot_id),
            )
        policy = await self._repository.find_policy(
            task_type=request.task_type.value,
            item_id=request.item_id,
        )
        seconds_per_item = (
            policy.seconds_per_item
            if policy is not None
            else _seconds_per_item(request.task_type, request.item_id)
        )
        now = datetime.now(UTC)
        initial_status = (
            OfflineTaskStatus.IN_PROGRESS
            if auto_start or request.quantity is not None
            else OfflineTaskStatus.PENDING
        )
        task = await self._repository.create_task(
            task_id=task_id or f"task-{uuid4()}",
            profile_id=identity.profile_id,
            save_slot_row_id=slot.row_id,
            issuing_device_id=identity.device_id,
            item_id=request.item_id,
            task_type=request.task_type.value,
            status=initial_status.value,
            started_at=now,
            creation_request_id=request.request_id,
            quantity=request.quantity,
            seconds_per_item=seconds_per_item,
        )
        if commit:
            try:
                await self._repository.commit()
            except IntegrityError:
                await self._repository.rollback()
                existing = await self._repository.find_by_creation_request(
                    profile_id=identity.profile_id,
                    save_slot_row_id=slot.row_id,
                    request_id=request.request_id,
                )
                if existing is None:
                    raise
                task = existing
        else:
            await self._repository.flush()
        return OfflineTaskResponse(
            request_id=request.request_id,
            task=await self._view(task, request.save_slot_id),
        )

    async def list(
        self,
        request_id: str,
        identity: AuthenticatedDevice,
        *,
        save_slot_id: str,
        status: OfflineTaskStatus | None,
    ) -> OfflineTaskListResponse:
        slot = await self._repository.find_slot(identity.profile_id, save_slot_id)
        if slot is None:
            return OfflineTaskListResponse(request_id=request_id, tasks=[])
        tasks = await self._repository.list_tasks(
            profile_id=identity.profile_id,
            save_slot_row_id=slot.row_id,
            status=status.value if status is not None else None,
        )
        return OfflineTaskListResponse(
            request_id=request_id,
            tasks=[await self._view(task, save_slot_id) for task in tasks],
        )

    async def start(
        self, request_id: str, task_id: str, identity: AuthenticatedDevice
    ) -> OfflineTaskResponse:
        self._require_game(identity)
        return await self._transition(
            request_id,
            task_id,
            identity,
            expected=OfflineTaskStatus.PENDING,
            new=OfflineTaskStatus.IN_PROGRESS,
        )

    async def complete(
        self, request_id: str, task_id: str, identity: AuthenticatedDevice
    ) -> OfflineTaskResponse:
        """게임 클라이언트가 작업을 완료 처리한다.

        `quantity`가 채워진 작업(채팅으로 자동 시작된 것)은 게임이 먼저 접속해서 끝내는
        경우에도 모바일의 `collect()`와 똑같은 경과시간 계산을 타 — 그 순간까지 모은
        만큼만 인정한다. `quantity`가 없는(기존 방식) 작업은 지금처럼 무조건 즉시 완료.
        """

        self._require_game(identity)
        task = await self._repository.get_owned_task(
            task_id=task_id, profile_id=identity.profile_id
        )
        if task is None:
            raise OfflineTaskNotFoundError
        if task.quantity is not None:
            return await self._finalize_in_progress(request_id, task_id, identity, task=task)
        return await self._transition(
            request_id,
            task_id,
            identity,
            expected=OfflineTaskStatus.IN_PROGRESS,
            new=OfflineTaskStatus.COMPLETED,
        )

    async def claim(
        self, request_id: str, task_id: str, identity: AuthenticatedDevice
    ) -> OfflineTaskResponse:
        self._require_game(identity)
        return await self._transition(
            request_id,
            task_id,
            identity,
            expected=OfflineTaskStatus.COMPLETED,
            new=OfflineTaskStatus.CLAIMED,
        )

    async def collect(
        self, request_id: str, task_id: str, identity: AuthenticatedDevice
    ) -> OfflineTaskResponse:
        """모바일이 자기가 요청한(채팅으로 자동 시작된) 작업을 스스로 마무리한다.

        전체 요청 수량만큼 시간이 다 안 지났어도, 부르는 즉시 그 순간까지 모은 만큼만
        확정한다 — `GET /api/v1/tasks`로 몇 번을 미리 확인해도 상태를 안 바꾸는 것과
        달리, 이 호출은 항상 그 자리에서 `Completed`로 끝낸다.
        """

        self._require_web(identity)
        return await self._finalize_in_progress(request_id, task_id, identity)

    async def delete(self, task_id: str, identity: AuthenticatedDevice) -> None:
        """모바일 소유자가 아직 정산되지 않은 예약 작업을 취소한다."""

        self._require_web(identity)
        task = await self._repository.get_owned_task(
            task_id=task_id, profile_id=identity.profile_id
        )
        if task is None:
            raise OfflineTaskNotFoundError

        cancellable_statuses = (
            OfflineTaskStatus.PENDING.value,
            OfflineTaskStatus.IN_PROGRESS.value,
        )
        if task.status not in cancellable_statuses:
            raise OfflineTaskTransitionError

        deleted = await self._repository.delete_owned_if_status(
            task_id=task_id,
            profile_id=identity.profile_id,
            allowed_statuses=cancellable_statuses,
        )
        if not deleted:
            raise OfflineTaskTransitionError
        await self._repository.commit()

    async def _finalize_in_progress(
        self,
        request_id: str,
        task_id: str,
        identity: AuthenticatedDevice,
        *,
        task: OfflineTaskModel | None = None,
    ) -> OfflineTaskResponse:
        if task is None:
            task = await self._repository.get_owned_task(
                task_id=task_id, profile_id=identity.profile_id
            )
        if task is None:
            raise OfflineTaskNotFoundError
        if task.status != OfflineTaskStatus.IN_PROGRESS.value:
            raise OfflineTaskTransitionError
        rate = self._task_seconds_per_item(task)
        if rate is None or rate <= 0:
            raise OfflineTaskInvalidRequestError("No duration model exists for this task type yet.")
        requested = task.quantity if task.quantity is not None else MAX_GATHER_QUANTITY
        elapsed_seconds = (datetime.now(UTC) - self._as_utc(task.started_at)).total_seconds()
        result_quantity = min(requested, int(elapsed_seconds // rate))
        if result_quantity == 0:
            slot = await self._repository.find_slot_by_row_id(task.save_slot_row_id)
            if slot is None:
                raise OfflineTaskNotFoundError
            return OfflineTaskResponse(
                request_id=request_id,
                task=await self._view(task, slot.save_slot_id),
            )
        changed = await self._repository.transition(
            task_id=task_id,
            profile_id=identity.profile_id,
            expected_status=OfflineTaskStatus.IN_PROGRESS.value,
            new_status=OfflineTaskStatus.COMPLETED.value,
            result_quantity=result_quantity,
        )
        if not changed:
            raise OfflineTaskTransitionError
        await self._repository.commit()
        updated = await self._repository.get_owned_task(
            task_id=task_id, profile_id=identity.profile_id
        )
        if updated is None:
            raise OfflineTaskNotFoundError
        slot = await self._repository.find_slot_by_row_id(updated.save_slot_row_id)
        if slot is None:
            raise OfflineTaskNotFoundError
        return OfflineTaskResponse(
            request_id=request_id,
            task=await self._view(updated, slot.save_slot_id),
        )

    async def _transition(
        self,
        request_id: str,
        task_id: str,
        identity: AuthenticatedDevice,
        *,
        expected: OfflineTaskStatus,
        new: OfflineTaskStatus,
    ) -> OfflineTaskResponse:
        task = await self._repository.get_owned_task(
            task_id=task_id, profile_id=identity.profile_id
        )
        if task is None:
            raise OfflineTaskNotFoundError
        changed = await self._repository.transition(
            task_id=task_id,
            profile_id=identity.profile_id,
            expected_status=expected.value,
            new_status=new.value,
        )
        if not changed:
            raise OfflineTaskTransitionError
        await self._repository.commit()
        updated = await self._repository.get_owned_task(
            task_id=task_id, profile_id=identity.profile_id
        )
        if updated is None:
            raise OfflineTaskNotFoundError
        slot = await self._repository.find_slot_by_row_id(updated.save_slot_row_id)
        if slot is None:
            raise OfflineTaskNotFoundError
        return OfflineTaskResponse(
            request_id=request_id,
            task=await self._view(updated, slot.save_slot_id),
        )

    async def _view(self, task: OfflineTaskModel, save_slot_id: str) -> OfflineTaskView:
        progress_quantity: int | None = None
        if task.status == OfflineTaskStatus.IN_PROGRESS.value and task.quantity is not None:
            rate = self._task_seconds_per_item(task)
            if rate is not None and rate > 0:
                elapsed_seconds = (
                    datetime.now(UTC) - self._as_utc(task.started_at)
                ).total_seconds()
                progress_quantity = min(task.quantity, int(elapsed_seconds // rate))
        return OfflineTaskView(
            task_id=task.task_id,
            save_slot_id=save_slot_id,
            item_id=task.item_id,
            task_type=OfflineTaskType(task.task_type),
            status=OfflineTaskStatus(task.status),
            started_at=self._as_utc(task.started_at),
            quantity=task.quantity,
            result_quantity=task.result_quantity,
            progress_quantity=progress_quantity,
        )

    @staticmethod
    def _task_seconds_per_item(task: OfflineTaskModel) -> float | None:
        if task.seconds_per_item is not None:
            return task.seconds_per_item
        return _seconds_per_item(OfflineTaskType(task.task_type), task.item_id)

    @staticmethod
    def _validate_item(task_type: OfflineTaskType, item_id: str | None) -> None:
        if task_type in {OfflineTaskType.GATHERING, OfflineTaskType.CRAFTING} and item_id is None:
            raise OfflineTaskInvalidRequestError("Gathering and Crafting tasks require item_id.")

    @staticmethod
    def _require_web(identity: AuthenticatedDevice) -> None:
        if identity.role is not DeviceRole.WEB_CLIENT:
            raise DeviceRoleNotAllowedError

    @staticmethod
    def _require_game(identity: AuthenticatedDevice) -> None:
        if identity.role is not DeviceRole.GAME_CLIENT:
            raise DeviceRoleNotAllowedError

    @staticmethod
    def _as_utc(value: datetime) -> datetime:
        if value.tzinfo is None:
            return value.replace(tzinfo=UTC)
        return value.astimezone(UTC)
