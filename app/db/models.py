"""디바이스 인증과 게임 마스터 데이터의 SQLAlchemy 테이블.

`cd0be55` 에 있던 여섯 인증 모델 중 넷만 되살렸다. `CompanionModel`/`ConversationModel`/
`MessageModel`/`ChatRequestModel`(요청 멱등성 + 감사 기록)은 이번 범위 밖이다 —
`docs/temporary-scaffolds.md` §2 에 후속 과제로 남겨 뒀다. 게임 마스터 데이터는
`app/gamedata/dataset.py`를 Alembic으로 적재하며, 장기기억은 별도 `episodic_memories` 행으로
저장한다.

brain 의 레시피·적 사실은 이제 이 테이블들을 앱 시작 시점에 한 번 읽은 스냅샷을 쓴다
(`app/db/game_data_loader.py`, `app/main.py`) — `app/gamedata/dataset.py` 는 여전히 Alembic
시드의 소스지만, 런타임 대사는 DB 값을 따라간다. `/api/v1/admin` 관리자 CRUD
(`app/routes/admin.py`)로 이 테이블을 고치면 **다음 재시작부터** 대사에 반영된다(핫 리로드
아님). 위치(`locations`)는 여전히 0행이다.
"""

from datetime import datetime

from sqlalchemy import JSON, DateTime, ForeignKey, Integer, String, Text, UniqueConstraint
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


class EpisodicMemoryModel(Base):
    __tablename__ = "episodic_memories"
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
