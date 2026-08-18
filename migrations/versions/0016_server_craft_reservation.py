"""Record server-authoritative material reservations for Web crafting.

Revision ID: 0016
Revises: 0015
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0016"
down_revision: str | None = "0015"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("offline_tasks") as batch:
        batch.add_column(sa.Column("reserved_item_id", sa.String(length=128), nullable=True))
        batch.add_column(sa.Column("reserved_quantity", sa.Integer(), nullable=True))
        batch.add_column(sa.Column("reserved_mako_quantity", sa.Integer(), nullable=True))
        batch.add_column(sa.Column("reserved_storage_quantity", sa.Integer(), nullable=True))
        batch.add_column(sa.Column("inventory_state_version", sa.Integer(), nullable=True))
        batch.create_foreign_key(
            "fk_offline_tasks_reserved_item_id_items",
            "items",
            ["reserved_item_id"],
            ["item_id"],
        )
        batch.create_index("ix_offline_tasks_reserved_item_id", ["reserved_item_id"])


def downgrade() -> None:
    with op.batch_alter_table("offline_tasks") as batch:
        batch.drop_index("ix_offline_tasks_reserved_item_id")
        batch.drop_constraint("fk_offline_tasks_reserved_item_id_items", type_="foreignkey")
        batch.drop_column("inventory_state_version")
        batch.drop_column("reserved_storage_quantity")
        batch.drop_column("reserved_mako_quantity")
        batch.drop_column("reserved_quantity")
        batch.drop_column("reserved_item_id")
