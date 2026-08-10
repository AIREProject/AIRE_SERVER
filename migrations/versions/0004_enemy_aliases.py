"""Add enemy aliases for fact matching."""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

from app.gamedata.dataset import DATASET

revision: str = "0004"
down_revision: str | None = "0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("enemies") as batch_op:
        batch_op.add_column(
            sa.Column("aliases", sa.JSON(), nullable=False, server_default="[]")
        )

    enemies_table = sa.table(
        "enemies",
        sa.column("enemy_id", sa.String(length=128)),
        sa.column("aliases", sa.JSON()),
    )
    connection = op.get_bind()
    for enemy in DATASET.enemies:
        connection.execute(
            sa.update(enemies_table)
            .where(enemies_table.c.enemy_id == enemy.enemy_id)
            .values(aliases=list(enemy.aliases))
        )


def downgrade() -> None:
    with op.batch_alter_table("enemies") as batch_op:
        batch_op.drop_column("aliases")
