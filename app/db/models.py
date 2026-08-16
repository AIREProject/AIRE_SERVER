"""디바이스 인증과 게임 마스터 데이터의 SQLAlchemy 테이블.

`cd0be55` 에 있던 여섯 인증 모델 중 넷만 되살렸다. `CompanionModel`/`ConversationModel`/
`MessageModel`/`ChatRequestModel`(요청 멱등성 + 감사 기록)은 이번 범위 밖이다 —
`docs/temporary-scaffolds.md` §2 에 후속 과제로 남겨 뒀다. 게임 마스터 데이터는
`app/gamedata/dataset.py`를 Alembic으로 적재하며, 장기기억은 별도 `episodic_memories` 행으로
저장했다. P3 이후 새 기억은 canonical Message/Event 출처를 가진 `memories`와
`memory_sources`에만 저장한다.

brain 의 레시피·적 사실은 이제 이 테이블들을 앱 시작 시점에 한 번 읽은 스냅샷을 쓴다
(`app/db/game_data_loader.py`, `app/main.py`) — `app/gamedata/dataset.py` 는 여전히 Alembic
시드의 소스지만, 런타임 대사는 DB 값을 따라간다. `/api/v1/admin` 관리자 CRUD
(`app/routes/admin.py`)로 이 테이블을 고치면 **다음 재시작부터** 대사에 반영된다(핫 리로드
아님). 위치(`locations`)는 여전히 0행이다.
"""

from datetime import datetime

from sqlalchemy import (
    JSON,
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class ProfileModel(Base):
    __tablename__ = "profiles"

    profile_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class DeviceModel(Base):
    __tablename__ = "devices"
    __table_args__ = (
        UniqueConstraint("token_lookup_id", name="uq_devices_token_lookup"),
        UniqueConstraint("creation_request_id", name="uq_devices_creation_request"),
        UniqueConstraint(
            "game_registration_key",
            name="uq_devices_game_registration_per_profile",
        ),
    )

    device_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    profile_id: Mapped[str] = mapped_column(
        ForeignKey("profiles.profile_id"),
        index=True,
    )
    role: Mapped[str] = mapped_column(String(16), index=True)
    token_lookup_id: Mapped[str] = mapped_column(String(128))
    token_hash: Mapped[str] = mapped_column(String(64))
    creation_request_id: Mapped[str] = mapped_column(String(128))
    game_registration_key: Mapped[str | None] = mapped_column(String(128))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class PairingCodeModel(Base):
    __tablename__ = "pairing_codes"
    __table_args__ = (
        UniqueConstraint(
            "profile_id",
            "issue_request_id",
            name="uq_pairing_codes_profile_issue_request",
        ),
        UniqueConstraint(
            "redeemed_request_id",
            name="uq_pairing_codes_redeemed_request",
        ),
    )

    pairing_code_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    profile_id: Mapped[str] = mapped_column(
        ForeignKey("profiles.profile_id"),
        index=True,
    )
    issuing_device_id: Mapped[str] = mapped_column(
        ForeignKey("devices.device_id"),
        index=True,
    )
    code_hash: Mapped[str] = mapped_column(String(64))
    issue_request_id: Mapped[str] = mapped_column(String(128))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    redeemed_request_id: Mapped[str | None] = mapped_column(String(128))
    paired_device_id: Mapped[str | None] = mapped_column(ForeignKey("devices.device_id"))


class SaveSlotModel(Base):
    __tablename__ = "save_slots"
    __table_args__ = (
        UniqueConstraint(
            "profile_id",
            "save_slot_id",
            name="uq_save_slots_profile_save_slot",
        ),
    )

    # 클라이언트가 보내는 save_slot_id 는 프로필 범위 안에서만 고유하면 되므로, 기본키는
    # 별도 대리키로 두고 (profile_id, save_slot_id) 를 유니크 제약으로 건다.
    row_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    save_slot_id: Mapped[str] = mapped_column(String(128), index=True)
    profile_id: Mapped[str] = mapped_column(
        ForeignKey("profiles.profile_id"),
        index=True,
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class ItemModel(Base):
    __tablename__ = "items"

    item_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    item_type: Mapped[str] = mapped_column(String(32), index=True)
    name_ko: Mapped[str] = mapped_column(String(128), index=True)
    aliases: Mapped[list[str]] = mapped_column(JSON)
    description: Mapped[str] = mapped_column(String(1000))


class RecipeModel(Base):
    __tablename__ = "recipes"

    recipe_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    result_item_id: Mapped[str] = mapped_column(String(128), index=True)
    result_amount: Mapped[int] = mapped_column()
    required_workbench: Mapped[str] = mapped_column(String(64))
    duration_seconds: Mapped[float] = mapped_column()
    ingredients: Mapped[list[dict[str, object]]] = mapped_column(JSON)


class SmeltingRecipeModel(Base):
    __tablename__ = "smelting_recipes"

    smelt_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    result_item_id: Mapped[str] = mapped_column(String(128), index=True)
    result_amount: Mapped[int] = mapped_column()
    required_workbench: Mapped[str] = mapped_column(String(64))
    duration_seconds: Mapped[float] = mapped_column()
    input_item: Mapped[dict[str, object]] = mapped_column(JSON)
    fuel: Mapped[dict[str, object]] = mapped_column(JSON)


class EnemyModel(Base):
    __tablename__ = "enemies"

    enemy_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    name_ko: Mapped[str] = mapped_column(String(128), index=True)
    aliases: Mapped[list[str]] = mapped_column(JSON)
    description: Mapped[str] = mapped_column(String(2000))
    weakness: Mapped[dict[str, str]] = mapped_column(JSON)


class LocationModel(Base):
    __tablename__ = "locations"

    location_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    coordinates: Mapped[dict[str, float]] = mapped_column(JSON)


class LegacyEpisodicMemoryModel(Base):
    """P3가 격리한 P2 이전의 무출처 기억 행.

    이 모델은 legacy JSON quarantine 확인용으로만 남긴다. 새 기억 경로에서는 사용하지
    않는다.
    """

    __tablename__ = "legacy_episodic_memories"
    __table_args__ = (
        UniqueConstraint("player_key", "text", name="uq_episodic_memories_player_text"),
    )

    row_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    # ERD에는 없지만, 인증된 프로필·세이브 슬롯을 가리키는 HMAC 스코프다.
    player_key: Mapped[str] = mapped_column(String(64), index=True)
    kind: Mapped[str] = mapped_column(String(16))
    text: Mapped[str] = mapped_column(Text)
    importance: Mapped[int] = mapped_column(Integer)
    source_key: Mapped[str | None] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    recalled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    recall_count: Mapped[int] = mapped_column(Integer, default=0)
    embedding: Mapped[list[float] | None] = mapped_column(JSON)
    embedding_model: Mapped[str | None] = mapped_column(String(128))


# 기존 retention/legacy-file 검증 코드의 명시적 호환 별칭이다. 서비스 조립은 이 모델을
# 더 이상 장기기억 저장소로 사용하지 않는다.
EpisodicMemoryModel = LegacyEpisodicMemoryModel


class MemoryModel(Base):
    __tablename__ = "memories"
    __table_args__ = (
        UniqueConstraint(
            "profile_id",
            "save_slot_row_id",
            "companion_id",
            "memory_type",
            "normalized_text",
            name="uq_memories_scope_type_text",
        ),
        CheckConstraint(
            "memory_type IN ('ProfileFact', 'Preference', 'Episode', "
            "'Promise', 'RelationshipEvidence')",
            name="ck_memories_type",
        ),
        CheckConstraint("status = 'Active'", name="ck_memories_status"),
        CheckConstraint("importance >= 1 AND importance <= 10", name="ck_memories_importance"),
    )

    memory_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    profile_id: Mapped[str] = mapped_column(ForeignKey("profiles.profile_id"), index=True)
    save_slot_row_id: Mapped[str] = mapped_column(ForeignKey("save_slots.row_id"), index=True)
    companion_id: Mapped[str] = mapped_column(String(128), index=True)
    memory_type: Mapped[str] = mapped_column(String(32), index=True)
    text: Mapped[str] = mapped_column(Text)
    normalized_text: Mapped[str] = mapped_column(Text)
    importance: Mapped[int] = mapped_column(Integer)
    pinned: Mapped[bool] = mapped_column(Boolean, default=False)
    status: Mapped[str] = mapped_column(String(16), default="Active", index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class MemorySourceModel(Base):
    __tablename__ = "memory_sources"
    __table_args__ = (
        UniqueConstraint("memory_id", "source_type", "source_id", name="uq_memory_sources"),
        CheckConstraint(
            "source_type IN ('Message', 'Event')", name="ck_memory_sources_type"
        ),
    )

    row_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    memory_id: Mapped[str] = mapped_column(ForeignKey("memories.memory_id"), index=True)
    source_type: Mapped[str] = mapped_column(String(16), index=True)
    source_id: Mapped[str] = mapped_column(String(128), index=True)
    source_mode: Mapped[str] = mapped_column(String(16))
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class MemoryMigrationReportModel(Base):
    __tablename__ = "memory_migration_reports"

    row_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    source_table: Mapped[str] = mapped_column(String(64), unique=True)
    status: Mapped[str] = mapped_column(String(64))
    quarantined_count: Mapped[int] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class OfflineTaskPolicyModel(Base):
    __tablename__ = "offline_task_policies"
    __table_args__ = (
        UniqueConstraint(
            "task_type",
            "item_id",
            name="uq_offline_task_policies_task_item",
        ),
    )

    policy_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    task_type: Mapped[str] = mapped_column(String(16), index=True)
    item_id: Mapped[str] = mapped_column(
        ForeignKey("items.item_id"),
        index=True,
    )
    seconds_per_item: Mapped[float] = mapped_column()


class OfflineTaskModel(Base):
    __tablename__ = "offline_tasks"
    __table_args__ = (
        UniqueConstraint(
            "profile_id",
            "save_slot_row_id",
            "creation_request_id",
            name="uq_offline_tasks_creation_request",
        ),
    )

    task_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    profile_id: Mapped[str] = mapped_column(
        ForeignKey("profiles.profile_id"),
        index=True,
    )
    save_slot_row_id: Mapped[str] = mapped_column(
        ForeignKey("save_slots.row_id"),
        index=True,
    )
    issuing_device_id: Mapped[str] = mapped_column(
        ForeignKey("devices.device_id"),
        index=True,
    )
    item_id: Mapped[str | None] = mapped_column(
        ForeignKey("items.item_id"),
        index=True,
    )
    task_type: Mapped[str] = mapped_column(String(16), index=True)
    status: Mapped[str] = mapped_column(String(16), index=True)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    creation_request_id: Mapped[str] = mapped_column(String(128))
    quantity: Mapped[int | None] = mapped_column(Integer)
    result_quantity: Mapped[int | None] = mapped_column(Integer)
    seconds_per_item: Mapped[float | None] = mapped_column()


class GameStateSnapshotModel(Base):
    __tablename__ = "game_state_snapshots"
    __table_args__ = (
        UniqueConstraint(
            "profile_id",
            "save_slot_row_id",
            "companion_id",
            name="uq_game_state_snapshots_scope",
        ),
        CheckConstraint("schema_version = 1", name="ck_game_state_snapshots_schema"),
        CheckConstraint("content_version = 1", name="ck_game_state_snapshots_content"),
        CheckConstraint("state_version > 0", name="ck_game_state_snapshots_state_version"),
        CheckConstraint(
            "payload_size_bytes >= 0 AND payload_size_bytes <= 262144",
            name="ck_game_state_snapshots_payload_size",
        ),
    )

    row_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    profile_id: Mapped[str] = mapped_column(
        ForeignKey("profiles.profile_id"), index=True
    )
    save_slot_row_id: Mapped[str] = mapped_column(
        ForeignKey("save_slots.row_id"), index=True
    )
    companion_id: Mapped[str] = mapped_column(String(128), index=True)
    schema_version: Mapped[int] = mapped_column(Integer)
    content_version: Mapped[int] = mapped_column(Integer)
    operation_id: Mapped[str] = mapped_column(String(128))
    state_version: Mapped[int] = mapped_column(Integer)
    world_session_id: Mapped[str] = mapped_column(String(128))
    captured_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    last_synced_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    payload: Mapped[dict[str, object]] = mapped_column(JSON)
    payload_size_bytes: Mapped[int] = mapped_column(Integer)


class GameStateOperationModel(Base):
    __tablename__ = "game_state_operations"
    __table_args__ = (
        UniqueConstraint(
            "profile_id",
            "save_slot_row_id",
            "companion_id",
            "operation_id",
            name="uq_game_state_operations_scope_operation",
        ),
        CheckConstraint("length(body_hash) = 64", name="ck_game_state_operations_hash"),
    )

    row_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    profile_id: Mapped[str] = mapped_column(
        ForeignKey("profiles.profile_id"), index=True
    )
    save_slot_row_id: Mapped[str] = mapped_column(
        ForeignKey("save_slots.row_id"), index=True
    )
    companion_id: Mapped[str] = mapped_column(String(128), index=True)
    operation_id: Mapped[str] = mapped_column(String(128))
    body_hash: Mapped[str] = mapped_column(String(64))
    response_status: Mapped[int] = mapped_column(Integer)
    response_body: Mapped[dict[str, object]] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class ConversationModel(Base):
    __tablename__ = "conversations"
    __table_args__ = (
        UniqueConstraint(
            "profile_id",
            "save_slot_row_id",
            "companion_id",
            "session_id",
            "surface",
            name="uq_conversations_scope_session_surface",
        ),
    )

    row_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    conversation_id: Mapped[str] = mapped_column(String(128), unique=True)
    profile_id: Mapped[str] = mapped_column(ForeignKey("profiles.profile_id"), index=True)
    save_slot_row_id: Mapped[str] = mapped_column(
        ForeignKey("save_slots.row_id"), index=True
    )
    companion_id: Mapped[str] = mapped_column(String(128), index=True)
    session_id: Mapped[str] = mapped_column(String(128))
    surface: Mapped[str] = mapped_column(String(16))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class MessageModel(Base):
    __tablename__ = "messages"
    __table_args__ = (
        UniqueConstraint("message_id", name="uq_messages_message_id"),
        UniqueConstraint(
            "conversation_row_id",
            "sequence",
            name="uq_messages_conversation_sequence",
        ),
        UniqueConstraint(
            "profile_id",
            "save_slot_row_id",
            "companion_id",
            "request_id",
            "speaker",
            name="uq_messages_scope_request_speaker",
        ),
        CheckConstraint("length(content_digest) = 64", name="ck_messages_content_digest"),
        CheckConstraint(
            "storage_class IN ('Transient', 'MemorySource')",
            name="ck_messages_storage_class",
        ),
    )

    row_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    message_id: Mapped[str] = mapped_column(String(128), index=True)
    conversation_row_id: Mapped[str] = mapped_column(
        ForeignKey("conversations.row_id"), index=True
    )
    profile_id: Mapped[str] = mapped_column(ForeignKey("profiles.profile_id"), index=True)
    save_slot_row_id: Mapped[str] = mapped_column(
        ForeignKey("save_slots.row_id"), index=True
    )
    companion_id: Mapped[str] = mapped_column(String(128), index=True)
    request_id: Mapped[str] = mapped_column(String(128), index=True)
    sequence: Mapped[int] = mapped_column(Integer)
    speaker: Mapped[str | None] = mapped_column(String(16))
    source_mode: Mapped[str | None] = mapped_column(String(16))
    content: Mapped[str | None] = mapped_column(Text)
    content_digest: Mapped[str] = mapped_column(String(64))
    time_context: Mapped[dict[str, object] | None] = mapped_column(JSON)
    storage_class: Mapped[str] = mapped_column(String(16))
    retention_reason: Mapped[str] = mapped_column(String(64))
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)
    audit_expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), index=True
    )
    content_deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    delivered_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class ChatOperationModel(Base):
    __tablename__ = "chat_operations"
    __table_args__ = (
        UniqueConstraint(
            "profile_id",
            "save_slot_row_id",
            "companion_id",
            "request_id",
            name="uq_chat_operations_scope_request",
        ),
        CheckConstraint("length(request_digest) = 64", name="ck_chat_operations_digest"),
        CheckConstraint(
            "state IN ('Pending', 'Generated', 'Completed')",
            name="ck_chat_operations_state",
        ),
    )

    row_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    profile_id: Mapped[str] = mapped_column(ForeignKey("profiles.profile_id"), index=True)
    save_slot_row_id: Mapped[str] = mapped_column(
        ForeignKey("save_slots.row_id"), index=True
    )
    companion_id: Mapped[str] = mapped_column(String(128), index=True)
    request_id: Mapped[str] = mapped_column(String(128))
    request_digest: Mapped[str] = mapped_column(String(64))
    state: Mapped[str] = mapped_column(String(24), index=True)
    input_message_id: Mapped[str] = mapped_column(String(128))
    response_message_id: Mapped[str | None] = mapped_column(String(128))
    response_metadata: Mapped[dict[str, object] | None] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    audit_expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)


class CommandCandidateModel(Base):
    __tablename__ = "command_candidates"
    __table_args__ = (
        UniqueConstraint(
            "profile_id",
            "save_slot_row_id",
            "companion_id",
            "session_id",
            "command_id",
            name="uq_command_candidates_scope_command",
        ),
    )

    row_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    command_id: Mapped[str] = mapped_column(String(128), index=True)
    profile_id: Mapped[str] = mapped_column(ForeignKey("profiles.profile_id"), index=True)
    save_slot_row_id: Mapped[str] = mapped_column(
        ForeignKey("save_slots.row_id"), index=True
    )
    companion_id: Mapped[str] = mapped_column(String(128), index=True)
    session_id: Mapped[str] = mapped_column(String(128))
    request_id: Mapped[str] = mapped_column(String(128), index=True)
    command_type: Mapped[str] = mapped_column(String(64))
    target_id: Mapped[str | None] = mapped_column(String(128))
    priority: Mapped[str] = mapped_column(String(16))
    issued_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    parameters: Mapped[dict[str, object]] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    audit_expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)


class GameEventModel(Base):
    __tablename__ = "game_events"
    __table_args__ = (
        UniqueConstraint(
            "profile_id",
            "save_slot_row_id",
            "companion_id",
            "event_id",
            name="uq_game_events_scope_event",
        ),
        CheckConstraint("length(body_hash) = 64", name="ck_game_events_body_hash"),
        CheckConstraint("schema_version = 1", name="ck_game_events_schema_version"),
        CheckConstraint(
            "storage_class IN ('Transient', 'MemorySource')",
            name="ck_game_events_storage_class",
        ),
    )

    row_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    event_id: Mapped[str] = mapped_column(String(128), index=True)
    profile_id: Mapped[str] = mapped_column(ForeignKey("profiles.profile_id"), index=True)
    save_slot_row_id: Mapped[str] = mapped_column(
        ForeignKey("save_slots.row_id"), index=True
    )
    companion_id: Mapped[str] = mapped_column(String(128), index=True)
    session_id: Mapped[str] = mapped_column(String(128))
    schema_version: Mapped[int] = mapped_column(Integer)
    event_type: Mapped[str | None] = mapped_column(String(64))
    importance: Mapped[str | None] = mapped_column(String(16))
    occurred_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    game_time: Mapped[dict[str, object] | None] = mapped_column(JSON)
    actor_id: Mapped[str | None] = mapped_column(String(128))
    target_ids: Mapped[list[str] | None] = mapped_column(JSON)
    body_hash: Mapped[str] = mapped_column(String(64))
    storage_class: Mapped[str] = mapped_column(String(16))
    retention_reason: Mapped[str] = mapped_column(String(64))
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)
    audit_expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), index=True
    )
    content_deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    response_body: Mapped[dict[str, object] | None] = mapped_column(JSON)
    received_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class CommandResultModel(Base):
    __tablename__ = "command_results"
    __table_args__ = (
        UniqueConstraint(
            "profile_id",
            "save_slot_row_id",
            "companion_id",
            "operation_id",
            name="uq_command_results_scope_operation",
        ),
        CheckConstraint("length(body_hash) = 64", name="ck_command_results_body_hash"),
        CheckConstraint("schema_version = 1", name="ck_command_results_schema_version"),
        CheckConstraint(
            "status IN ('Accepted', 'Running', 'Succeeded', 'Rejected', "
            "'Failed', 'Cancelled', 'Expired')",
            name="ck_command_results_status",
        ),
    )

    row_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    operation_id: Mapped[str] = mapped_column(String(128), index=True)
    profile_id: Mapped[str] = mapped_column(ForeignKey("profiles.profile_id"), index=True)
    save_slot_row_id: Mapped[str] = mapped_column(
        ForeignKey("save_slots.row_id"), index=True
    )
    companion_id: Mapped[str] = mapped_column(String(128), index=True)
    schema_version: Mapped[int] = mapped_column(Integer)
    candidate_row_id: Mapped[str] = mapped_column(
        ForeignKey("command_candidates.row_id"), index=True
    )
    command_id: Mapped[str] = mapped_column(String(128), index=True)
    request_id: Mapped[str] = mapped_column(String(128))
    command_type: Mapped[str] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(16), index=True)
    reason: Mapped[str] = mapped_column(String(64))
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    game_time: Mapped[dict[str, object]] = mapped_column(JSON)
    body_hash: Mapped[str] = mapped_column(String(64))
    response_body: Mapped[dict[str, object]] = mapped_column(JSON)
    received_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    audit_expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)


class SourceRetentionReferenceModel(Base):
    __tablename__ = "source_retention_references"
    __table_args__ = (
        UniqueConstraint(
            "source_type", "source_id", "reference_id", name="uq_source_retention_ref"
        ),
        CheckConstraint(
            "source_type IN ('Message', 'Event')",
            name="ck_source_retention_references_type",
        ),
    )

    row_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    source_type: Mapped[str] = mapped_column(String(16), index=True)
    source_id: Mapped[str] = mapped_column(String(128), index=True)
    reference_id: Mapped[str] = mapped_column(String(128))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class SourceOutboxModel(Base):
    __tablename__ = "source_outbox"
    __table_args__ = (
        UniqueConstraint("source_type", "source_id", name="uq_source_outbox_source"),
        CheckConstraint(
            "state IN ('Pending', 'Claimed', 'Completed', 'Tombstone')",
            name="ck_source_outbox_state",
        ),
        CheckConstraint(
            "source_type IN ('Message', 'Event')",
            name="ck_source_outbox_type",
        ),
        CheckConstraint("attempt_count >= 0", name="ck_source_outbox_attempt_count"),
    )

    source_seq: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    source_type: Mapped[str] = mapped_column(String(16), index=True)
    source_id: Mapped[str] = mapped_column(String(128), index=True)
    state: Mapped[str] = mapped_column(String(16), index=True)
    lease_token: Mapped[str | None] = mapped_column(String(36))
    lease_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    attempt_count: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class SourceCursorModel(Base):
    __tablename__ = "source_cursors"
    __table_args__ = (
        CheckConstraint("last_completed_seq >= 0", name="ck_source_cursors_sequence"),
    )

    consumer: Mapped[str] = mapped_column(String(64), primary_key=True)
    last_completed_seq: Mapped[int] = mapped_column(Integer)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class LegacyImportReportModel(Base):
    __tablename__ = "legacy_import_reports"
    __table_args__ = (
        UniqueConstraint(
            "file_name", "file_hash", name="uq_legacy_import_reports_file_hash"
        ),
        CheckConstraint("length(file_hash) = 64", name="ck_legacy_import_reports_hash"),
        CheckConstraint(
            "imported_count >= 0", name="ck_legacy_import_reports_imported_count"
        ),
    )

    row_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    file_name: Mapped[str] = mapped_column(String(255))
    file_hash: Mapped[str] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(24), index=True)
    imported_count: Mapped[int] = mapped_column(Integer)
    quarantined_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    delete_after: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    error_code: Mapped[str | None] = mapped_column(String(64))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
