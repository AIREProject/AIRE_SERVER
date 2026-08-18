"""Offline_Task 생성과 게임 클라이언트 상태 전이."""

import json
from datetime import UTC, datetime
from uuid import uuid4

from sqlalchemy.exc import IntegrityError

from app.brain.resources import MAX_GATHER_QUANTITY
from app.db.models import GameStateSnapshotModel, OfflineTaskModel
from app.db.offline_task_repository import SqlAlchemyOfflineTaskRepository
from app.errors import (
    DeviceRoleNotAllowedError,
    InsufficientCraftingMaterialsError,
    InventorySnapshotRequiredError,
    OfflineTaskInvalidRequestError,
    OfflineTaskNotFoundError,
    OfflineTaskTransitionError,
)
from app.game_state_models import GameStateContainer, GameStateStack, PutGameStateRequest
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
_SHODDY_BANDAGE_SECONDS_PER_ITEM = 10.0
_CRAFT_MATERIAL_ID = "PlantStem"
_SHODDY_BANDAGE_ID = "ShoddyBandage"
_MAKO_CONTAINER_ID = "AIRE.Inventory.MAKO"
_STORAGE_CONTAINER_ID = "AIRE.Inventory.SharedStorage"
_MAX_STACK_COUNT = 99


def _crafting_seconds_per_item(item_id: str | None) -> float | None:
    if item_id is None:
        return None
    if item_id == _SHODDY_BANDAGE_ID:
        return _SHODDY_BANDAGE_SECONDS_PER_ITEM
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
        resolved_task_id = task_id or f"task-{uuid4()}"
        reservation: tuple[int, int, int, int] | None = None
        if request.task_type is OfflineTaskType.CRAFTING:
            if request.item_id != _SHODDY_BANDAGE_ID or request.quantity is None:
                raise OfflineTaskInvalidRequestError(
                    "Web crafting currently requires ShoddyBandage and an explicit quantity."
                )
            reservation = await self._reserve_crafting_materials(
                identity=identity,
                save_slot_row_id=slot.row_id,
                task_id=resolved_task_id,
                result_quantity=request.quantity,
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
            task_id=resolved_task_id,
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
            reserved_item_id=_CRAFT_MATERIAL_ID if reservation is not None else None,
            reserved_quantity=reservation[0] if reservation is not None else None,
            reserved_mako_quantity=reservation[1] if reservation is not None else None,
            reserved_storage_quantity=reservation[2] if reservation is not None else None,
            inventory_state_version=reservation[3] if reservation is not None else None,
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

        if task.reserved_quantity is not None:
            await self._refund_crafting_materials(task, identity)

        deleted = await self._repository.delete_owned_if_status(
            task_id=task_id,
            profile_id=identity.profile_id,
            allowed_statuses=cancellable_statuses,
        )
        if not deleted:
            raise OfflineTaskTransitionError
        await self._repository.commit()

    async def _reserve_crafting_materials(
        self,
        *,
        identity: AuthenticatedDevice,
        save_slot_row_id: str,
        task_id: str,
        result_quantity: int,
    ) -> tuple[int, int, int, int]:
        """서버 Game State에서 붕대 재료를 task 생성과 같은 transaction으로 예약한다."""

        snapshot = await self._repository.lock_game_state(
            profile_id=identity.profile_id,
            save_slot_row_id=save_slot_row_id,
            companion_id="mako",
        )
        if snapshot is None:
            raise InventorySnapshotRequiredError
        state = PutGameStateRequest.model_validate(snapshot.payload).model_copy(deep=True)
        required = result_quantity * 2
        available = sum(
            stack.count
            for container in state.inventory.containers
            for stack in container.stacks
            if stack.item_id == _CRAFT_MATERIAL_ID
        )
        if available < required:
            raise InsufficientCraftingMaterialsError

        remaining = required
        removed: dict[str, int] = {_MAKO_CONTAINER_ID: 0, _STORAGE_CONTAINER_ID: 0}
        for container_id in (_MAKO_CONTAINER_ID, _STORAGE_CONTAINER_ID):
            container = self._container(state, container_id)
            kept = []
            before = remaining
            for stack in container.stacks:
                if stack.item_id != _CRAFT_MATERIAL_ID or remaining == 0:
                    kept.append(stack)
                    continue
                consumed = min(stack.count, remaining)
                remaining -= consumed
                if stack.count > consumed:
                    stack.count -= consumed
                    kept.append(stack)
            removed[container_id] = before - remaining
            if removed[container_id] > 0:
                container.stacks = kept
                container.revision += 1
            if remaining == 0:
                break
        if remaining != 0:
            raise InsufficientCraftingMaterialsError
        self._store_server_inventory_change(snapshot, state, f"craft-reserve-{task_id}")
        return (
            required,
            removed[_MAKO_CONTAINER_ID],
            removed[_STORAGE_CONTAINER_ID],
            state.state_version,
        )

    async def _refund_crafting_materials(
        self, task: OfflineTaskModel, identity: AuthenticatedDevice
    ) -> None:
        snapshot = await self._repository.lock_game_state(
            profile_id=identity.profile_id,
            save_slot_row_id=task.save_slot_row_id,
            companion_id="mako",
        )
        if snapshot is None:
            raise InventorySnapshotRequiredError
        state = PutGameStateRequest.model_validate(snapshot.payload).model_copy(deep=True)
        for container_id, quantity in (
            (_MAKO_CONTAINER_ID, task.reserved_mako_quantity or 0),
            (_STORAGE_CONTAINER_ID, task.reserved_storage_quantity or 0),
        ):
            if quantity > 0 and not self._add_material(
                self._container(state, container_id), quantity
            ):
                raise OfflineTaskTransitionError
        self._store_server_inventory_change(snapshot, state, f"craft-refund-{task.task_id}")

    @staticmethod
    def _container(state: PutGameStateRequest, container_id: str) -> GameStateContainer:
        return next(
            container
            for container in state.inventory.containers
            if container.container_id == container_id
        )

    @staticmethod
    def _add_material(container: GameStateContainer, quantity: int) -> bool:
        remaining = quantity
        changed = False
        for stack in container.stacks:
            if stack.item_id != _CRAFT_MATERIAL_ID or stack.count >= _MAX_STACK_COUNT:
                continue
            added = min(_MAX_STACK_COUNT - stack.count, remaining)
            stack.count += added
            remaining -= added
            changed = changed or added > 0
            if remaining == 0:
                break
        occupied = {stack.slot_index for stack in container.stacks}
        while remaining > 0:
            slot = next(
                (index for index in range(container.capacity) if index not in occupied), None
            )
            if slot is None:
                return False
            count = min(_MAX_STACK_COUNT, remaining)
            container.stacks.append(
                GameStateStack(slot_index=slot, item_id=_CRAFT_MATERIAL_ID, count=count)
            )
            occupied.add(slot)
            remaining -= count
            changed = True
        if changed:
            container.revision += 1
        return True

    @staticmethod
    def _store_server_inventory_change(
        snapshot: GameStateSnapshotModel,
        state: PutGameStateRequest,
        operation_id: str,
    ) -> None:
        now = datetime.now(UTC)
        state.state_version += 1
        state.operation_id = operation_id[:128]
        state.captured_at = now
        payload = state.model_dump(mode="json")
        encoded = json.dumps(payload, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
        snapshot.state_version = state.state_version
        snapshot.operation_id = state.operation_id
        snapshot.captured_at = now
        snapshot.last_synced_at = now
        snapshot.payload = payload
        snapshot.payload_size_bytes = len(encoded)

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
        # 제작은 요청 수량의 재료를 시작 시 서버 Inventory에서 전부 예약한다. 일부만
        # Completed로 닫으면 미완성분 재료가 고아가 되므로 전체 수량 시간이 지난 뒤에만
        # 완료한다. Gathering은 기존처럼 현재까지 모은 수량으로 부분 완료할 수 있다.
        if result_quantity == 0 or (
            task.task_type == OfflineTaskType.CRAFTING.value and result_quantity < requested
        ):
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
