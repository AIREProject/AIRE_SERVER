"""모바일이 만들고 게임 클라이언트가 수행하는 Offline_Task 계약."""

from datetime import datetime
from enum import StrEnum

from app.models import StableId, StrictModel


class OfflineTaskType(StrEnum):
    """모바일에서 지시할 수 있는 작업 종류."""

    GATHERING = "Gathering"
    CRAFTING = "Crafting"
    SCOUTING = "Scouting"


class OfflineTaskStatus(StrEnum):
    """게임 클라이언트가 갱신하는 작업 상태."""

    PENDING = "Pending"
    IN_PROGRESS = "InProgress"
    COMPLETED = "Completed"
    CLAIMED = "Claimed"


class CreateOfflineTaskRequest(StrictModel):
    request_id: StableId
    save_slot_id: StableId
    task_type: OfflineTaskType
    item_id: StableId | None = None
    # 채팅으로 자동 시작된(경과시간으로 진행량을 역산하는) 작업에서만 채워진다.
    quantity: int | None = None


class OfflineTaskView(StrictModel):
    task_id: StableId
    save_slot_id: StableId
    item_id: StableId | None = None
    task_type: OfflineTaskType
    status: OfflineTaskStatus
    started_at: datetime
    quantity: int | None = None
    result_quantity: int | None = None
    # InProgress일 때만 조회 시점에 계산되는 비영속 미리보기 — DB엔 쓰지 않는다.
    progress_quantity: int | None = None


class OfflineTaskResponse(StrictModel):
    request_id: StableId
    task: OfflineTaskView


class OfflineTaskListResponse(StrictModel):
    request_id: StableId
    tasks: list[OfflineTaskView]
