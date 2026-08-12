"""Create scoped latest Game State snapshots and operation ledger."""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0009"
down_revision: str | None = "0008"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "game_state_snapshots",
        sa.Column("row_id", sa.String(length=36), nullable=False),
        sa.Column("profile_id", sa.String(length=128), nullable=False),
        sa.Column("save_slot_row_id", sa.String(length=128), nullable=False),
        sa.Column("companion_id", sa.String(length=128), nullable=False),
        sa.Column("schema_version", sa.Integer(), nullable=False),
        sa.Column("content_version", sa.Integer(), nullable=False),
        sa.Column("operation_id", sa.String(length=128), nullable=False),
        sa.Column("state_version", sa.Integer(), nullable=False),
        sa.Column("world_session_id", sa.String(length=128), nullable=False),
        sa.Column("captured_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_synced_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column("payload_size_bytes", sa.Integer(), nullable=False),
        sa.CheckConstraint(
            "schema_version = 1",
            name="ck_game_state_snapshots_schema",
        ),
        sa.CheckConstraint(
            "content_version = 1",
            name="ck_game_state_snapshots_content",
        ),
        sa.CheckConstraint(
            "state_version > 0",
            name="ck_game_state_snapshots_state_version",
        ),
        sa.CheckConstraint(
            "payload_size_bytes >= 0 AND payload_size_bytes <= 262144",
            name="ck_game_state_snapshots_payload_size",
        ),
        sa.ForeignKeyConstraint(["profile_id"], ["profiles.profile_id"]),
        sa.ForeignKeyConstraint(["save_slot_row_id"], ["save_slots.row_id"]),
        sa.PrimaryKeyConstraint("row_id"),
        sa.UniqueConstraint(
            "profile_id",
            "save_slot_row_id",
            "companion_id",
            name="uq_game_state_snapshots_scope",
        ),
    )
    op.create_index(
        "ix_game_state_snapshots_profile_id",
        "game_state_snapshots",
        ["profile_id"],
    )
    op.create_index(
        "ix_game_state_snapshots_save_slot_row_id",
        "game_state_snapshots",
        ["save_slot_row_id"],
    )
    op.create_index(
        "ix_game_state_snapshots_companion_id",
        "game_state_snapshots",
        ["companion_id"],
    )

    op.create_table(
        "game_state_operations",
        sa.Column("row_id", sa.String(length=36), nullable=False),
        sa.Column("profile_id", sa.String(length=128), nullable=False),
        sa.Column("save_slot_row_id", sa.String(length=128), nullable=False),
        sa.Column("companion_id", sa.String(length=128), nullable=False),
        sa.Column("operation_id", sa.String(length=128), nullable=False),
        sa.Column("body_hash", sa.String(length=64), nullable=False),
        sa.Column("response_status", sa.Integer(), nullable=False),
        sa.Column("response_body", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "length(body_hash) = 64",
            name="ck_game_state_operations_hash",
        ),
        sa.ForeignKeyConstraint(["profile_id"], ["profiles.profile_id"]),
        sa.ForeignKeyConstraint(["save_slot_row_id"], ["save_slots.row_id"]),
        sa.PrimaryKeyConstraint("row_id"),
        sa.UniqueConstraint(
            "profile_id",
            "save_slot_row_id",
            "companion_id",
            "operation_id",
            name="uq_game_state_operations_scope_operation",
        ),
    )
    op.create_index(
        "ix_game_state_operations_profile_id",
        "game_state_operations",
        ["profile_id"],
    )
    op.create_index(
        "ix_game_state_operations_save_slot_row_id",
        "game_state_operations",
        ["save_slot_row_id"],
    )
    op.create_index(
        "ix_game_state_operations_companion_id",
        "game_state_operations",
        ["companion_id"],
    )


def downgrade() -> None:
    op.drop_table("game_state_operations")
    op.drop_table("game_state_snapshots")
