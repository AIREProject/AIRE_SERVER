"""Quarantine legacy memories and add source-backed memory storage."""

from collections.abc import Sequence
from datetime import UTC, datetime
from uuid import uuid4

import sqlalchemy as sa
from alembic import op

revision: str = "0011"
down_revision: str | None = "0010"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _scope_columns() -> tuple[sa.Column[object], ...]:
    return (
        sa.Column("profile_id", sa.String(length=128), nullable=False),
        sa.Column("save_slot_row_id", sa.String(length=128), nullable=False),
        sa.Column("companion_id", sa.String(length=128), nullable=False),
    )


def upgrade() -> None:
    connection = op.get_bind()
    legacy_count = int(
        connection.execute(sa.text("SELECT COUNT(*) FROM episodic_memories")).scalar_one()
    )
    op.rename_table("episodic_memories", "legacy_episodic_memories")
    op.drop_index("ix_episodic_memories_player_key", table_name="legacy_episodic_memories")
    op.create_index(
        "ix_legacy_episodic_memories_player_key",
        "legacy_episodic_memories",
        ["player_key"],
    )

    op.create_table(
        "memories",
        sa.Column("memory_id", sa.String(length=128), nullable=False),
        *_scope_columns(),
        sa.Column("memory_type", sa.String(length=32), nullable=False),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("normalized_text", sa.Text(), nullable=False),
        sa.Column("importance", sa.Integer(), nullable=False),
        sa.Column("pinned", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("status", sa.String(length=16), nullable=False, server_default="Active"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "memory_type IN ('ProfileFact', 'Preference', 'Episode', "
            "'Promise', 'RelationshipEvidence')",
            name="ck_memories_type",
        ),
        sa.CheckConstraint("status = 'Active'", name="ck_memories_status"),
        sa.CheckConstraint("importance >= 1 AND importance <= 10", name="ck_memories_importance"),
        sa.ForeignKeyConstraint(["profile_id"], ["profiles.profile_id"]),
        sa.ForeignKeyConstraint(["save_slot_row_id"], ["save_slots.row_id"]),
        sa.PrimaryKeyConstraint("memory_id"),
        sa.UniqueConstraint(
            "profile_id",
            "save_slot_row_id",
            "companion_id",
            "memory_type",
            "normalized_text",
            name="uq_memories_scope_type_text",
        ),
    )
    for column in ("profile_id", "save_slot_row_id", "companion_id", "memory_type", "status"):
        op.create_index(f"ix_memories_{column}", "memories", [column])

    op.create_table(
        "memory_sources",
        sa.Column("row_id", sa.String(length=36), nullable=False),
        sa.Column("memory_id", sa.String(length=128), nullable=False),
        sa.Column("source_type", sa.String(length=16), nullable=False),
        sa.Column("source_id", sa.String(length=128), nullable=False),
        sa.Column("source_mode", sa.String(length=16), nullable=False),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("source_type IN ('Message', 'Event')", name="ck_memory_sources_type"),
        sa.ForeignKeyConstraint(["memory_id"], ["memories.memory_id"]),
        sa.PrimaryKeyConstraint("row_id"),
        sa.UniqueConstraint("memory_id", "source_type", "source_id", name="uq_memory_sources"),
    )
    for column in ("memory_id", "source_type", "source_id"):
        op.create_index(f"ix_memory_sources_{column}", "memory_sources", [column])

    op.create_table(
        "memory_migration_reports",
        sa.Column("row_id", sa.String(length=36), nullable=False),
        sa.Column("source_table", sa.String(length=64), nullable=False),
        sa.Column("status", sa.String(length=64), nullable=False),
        sa.Column("quarantined_count", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("row_id"),
        sa.UniqueConstraint("source_table"),
    )
    connection.execute(
        sa.text(
            "INSERT INTO memory_migration_reports "
            "(row_id, source_table, status, quarantined_count, created_at) "
            "VALUES (:row_id, :source_table, :status, :quarantined_count, :created_at)"
        ),
        {
            "row_id": str(uuid4()),
            "source_table": "episodic_memories",
            "status": "quarantined_without_canonical_source",
            "quarantined_count": legacy_count,
            "created_at": datetime.now(UTC),
        },
    )


def downgrade() -> None:
    op.drop_table("memory_migration_reports")
    op.drop_table("memory_sources")
    op.drop_table("memories")
    op.drop_index("ix_legacy_episodic_memories_player_key", table_name="legacy_episodic_memories")
    op.rename_table("legacy_episodic_memories", "episodic_memories")
    op.create_index("ix_episodic_memories_player_key", "episodic_memories", ["player_key"])
