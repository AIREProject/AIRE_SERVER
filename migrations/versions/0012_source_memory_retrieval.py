"""Add source-backed memory retrieval metadata."""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0012"
down_revision: str | None = "0011"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("memories") as batch:
        batch.add_column(sa.Column("recalled_at", sa.DateTime(timezone=True), nullable=True))
        batch.add_column(
            sa.Column("recall_count", sa.Integer(), nullable=False, server_default="0")
        )
        batch.add_column(sa.Column("embedding", sa.JSON(), nullable=True))
        batch.add_column(sa.Column("embedding_model", sa.String(length=128), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("memories") as batch:
        batch.drop_column("embedding_model")
        batch.drop_column("embedding")
        batch.drop_column("recall_count")
        batch.drop_column("recalled_at")
