"""Add archived memory lifecycle and immutable user corrections."""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0013"
down_revision: str | None = "0012"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # SQLite cannot alter a CHECK constraint in place, so batch mode rebuilds the table.
    with op.batch_alter_table("memories") as batch:
        batch.drop_constraint("ck_memories_status", type_="check")
        batch.add_column(sa.Column("archived_at", sa.DateTime(timezone=True), nullable=True))
        batch.add_column(sa.Column("archived_reason", sa.String(length=512), nullable=True))
        batch.create_check_constraint(
            "ck_memories_status", "status IN ('Active', 'Archived')"
        )
    op.create_table(
        "memory_corrections",
        sa.Column("row_id", sa.String(length=36), nullable=False),
        sa.Column("memory_id", sa.String(length=128), nullable=False),
        sa.Column("corrected_text", sa.Text(), nullable=False),
        sa.Column("reason", sa.String(length=512), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["memory_id"], ["memories.memory_id"]),
        sa.PrimaryKeyConstraint("row_id"),
    )
    op.create_index("ix_memory_corrections_memory_id", "memory_corrections", ["memory_id"])


def downgrade() -> None:
    op.drop_index("ix_memory_corrections_memory_id", table_name="memory_corrections")
    op.drop_table("memory_corrections")
    with op.batch_alter_table("memories") as batch:
        batch.drop_constraint("ck_memories_status", type_="check")
        batch.drop_column("archived_reason")
        batch.drop_column("archived_at")
        batch.create_check_constraint("ck_memories_status", "status = 'Active'")
