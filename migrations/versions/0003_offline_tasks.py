"""Create scoped offline task records."""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0003"
down_revision: str | None = "0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "offline_tasks",
        sa.Column("task_id", sa.String(length=128), nullable=False),
        sa.Column("profile_id", sa.String(length=128), nullable=False),
        sa.Column("save_slot_row_id", sa.String(length=128), nullable=False),
        sa.Column("issuing_device_id", sa.String(length=128), nullable=False),
        sa.Column("item_id", sa.String(length=128), nullable=True),
        sa.Column("task_type", sa.String(length=16), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("creation_request_id", sa.String(length=128), nullable=False),
        sa.ForeignKeyConstraint(["profile_id"], ["profiles.profile_id"]),
        sa.ForeignKeyConstraint(["save_slot_row_id"], ["save_slots.row_id"]),
        sa.ForeignKeyConstraint(["issuing_device_id"], ["devices.device_id"]),
        sa.ForeignKeyConstraint(["item_id"], ["items.item_id"]),
        sa.PrimaryKeyConstraint("task_id"),
        sa.UniqueConstraint(
            "profile_id",
            "save_slot_row_id",
            "creation_request_id",
            name="uq_offline_tasks_creation_request",
        ),
    )
    op.create_index("ix_offline_tasks_profile_id", "offline_tasks", ["profile_id"])
    op.create_index(
        "ix_offline_tasks_save_slot_row_id", "offline_tasks", ["save_slot_row_id"]
    )
    op.create_index(
        "ix_offline_tasks_issuing_device_id", "offline_tasks", ["issuing_device_id"]
    )
    op.create_index("ix_offline_tasks_item_id", "offline_tasks", ["item_id"])
    op.create_index("ix_offline_tasks_task_type", "offline_tasks", ["task_type"])
    op.create_index("ix_offline_tasks_status", "offline_tasks", ["status"])


def downgrade() -> None:
    op.drop_table("offline_tasks")
