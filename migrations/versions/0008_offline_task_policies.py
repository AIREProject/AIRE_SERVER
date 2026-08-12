"""Add configurable Offline Task duration policies and per-task snapshots."""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0008"
down_revision: str | None = "0007"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "offline_task_policies",
        sa.Column("policy_id", sa.String(length=128), nullable=False),
        sa.Column("task_type", sa.String(length=16), nullable=False),
        sa.Column("item_id", sa.String(length=128), nullable=False),
        sa.Column("seconds_per_item", sa.Float(), nullable=False),
        sa.CheckConstraint(
            "seconds_per_item > 0 AND seconds_per_item <= 86400",
            name="ck_offline_task_policies_seconds_per_item",
        ),
        sa.ForeignKeyConstraint(["item_id"], ["items.item_id"]),
        sa.PrimaryKeyConstraint("policy_id"),
        sa.UniqueConstraint(
            "task_type",
            "item_id",
            name="uq_offline_task_policies_task_item",
        ),
    )
    op.create_index(
        "ix_offline_task_policies_task_type",
        "offline_task_policies",
        ["task_type"],
        unique=False,
    )
    op.create_index(
        "ix_offline_task_policies_item_id",
        "offline_task_policies",
        ["item_id"],
        unique=False,
    )
    policies = sa.table(
        "offline_task_policies",
        sa.column("policy_id", sa.String()),
        sa.column("task_type", sa.String()),
        sa.column("item_id", sa.String()),
        sa.column("seconds_per_item", sa.Float()),
    )
    op.bulk_insert(
        policies,
        [
            {
                "policy_id": "gathering-plant-stem",
                "task_type": "Gathering",
                "item_id": "PlantStem",
                "seconds_per_item": 5.0,
            },
            {
                "policy_id": "crafting-shoddy-bandage",
                "task_type": "Crafting",
                "item_id": "ShoddyBandage",
                "seconds_per_item": 10.0,
            },
        ],
    )
    op.add_column(
        "offline_tasks",
        sa.Column("seconds_per_item", sa.Float(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("offline_tasks", "seconds_per_item")
    op.drop_index(
        "ix_offline_task_policies_item_id",
        table_name="offline_task_policies",
    )
    op.drop_index(
        "ix_offline_task_policies_task_type",
        table_name="offline_task_policies",
    )
    op.drop_table("offline_task_policies")
