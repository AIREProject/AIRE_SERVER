"""Add requested/result quantity to offline tasks."""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0007"
down_revision: str | None = "0006"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("offline_tasks", sa.Column("quantity", sa.Integer(), nullable=True))
    op.add_column(
        "offline_tasks", sa.Column("result_quantity", sa.Integer(), nullable=True)
    )


def downgrade() -> None:
    op.drop_column("offline_tasks", "result_quantity")
    op.drop_column("offline_tasks", "quantity")
