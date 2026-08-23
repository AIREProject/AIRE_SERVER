"""Add reviewable source-backed memory candidates.

Revision ID: 0017
Revises: 0016
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0017"
down_revision: str | None = "0016"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "memory_candidates",
        sa.Column("candidate_id", sa.String(length=128), primary_key=True),
        sa.Column("profile_id", sa.String(length=128), nullable=False),
        sa.Column("save_slot_row_id", sa.String(length=36), nullable=False),
        sa.Column("companion_id", sa.String(length=128), nullable=False),
        sa.Column("memory_type", sa.String(length=32), nullable=False),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("normalized_text", sa.Text(), nullable=False),
        sa.Column("importance", sa.Integer(), nullable=False),
        sa.Column("pinned", sa.Boolean(), nullable=False),
        sa.Column("confidence", sa.Float(), nullable=False),
        sa.Column("embedding", sa.JSON(), nullable=True),
        sa.Column("embedding_model", sa.String(length=128), nullable=True),
        sa.Column("source_type", sa.String(length=16), nullable=False),
        sa.Column("source_id", sa.String(length=128), nullable=False),
        sa.Column("source_mode", sa.String(length=16), nullable=False),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("review_reason", sa.String(length=64), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("decided_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("decision_reason", sa.String(length=512), nullable=True),
        sa.Column("approved_memory_id", sa.String(length=128), nullable=True),
        sa.ForeignKeyConstraint(["profile_id"], ["profiles.profile_id"]),
        sa.ForeignKeyConstraint(["save_slot_row_id"], ["save_slots.row_id"]),
        sa.ForeignKeyConstraint(["approved_memory_id"], ["memories.memory_id"]),
        sa.UniqueConstraint("source_type", "source_id", name="uq_memory_candidates_source"),
        sa.CheckConstraint(
            "memory_type IN "
            "('ProfileFact', 'Preference', 'Episode', 'Promise', 'RelationshipEvidence')",
            name="ck_memory_candidates_type",
        ),
        sa.CheckConstraint(
            "status IN ('PendingReview', 'Approved', 'Rejected', 'Expired')",
            name="ck_memory_candidates_status",
        ),
        sa.CheckConstraint(
            "importance >= 1 AND importance <= 10",
            name="ck_memory_candidates_importance",
        ),
        sa.CheckConstraint(
            "confidence >= 0 AND confidence <= 1",
            name="ck_memory_candidates_confidence",
        ),
        sa.CheckConstraint(
            "source_type IN ('Message', 'Event')",
            name="ck_memory_candidates_source_type",
        ),
    )
    op.create_index("ix_memory_candidates_profile_id", "memory_candidates", ["profile_id"])
    op.create_index(
        "ix_memory_candidates_save_slot_row_id",
        "memory_candidates",
        ["save_slot_row_id"],
    )
    op.create_index("ix_memory_candidates_companion_id", "memory_candidates", ["companion_id"])
    op.create_index("ix_memory_candidates_source_type", "memory_candidates", ["source_type"])
    op.create_index("ix_memory_candidates_source_id", "memory_candidates", ["source_id"])
    op.create_index("ix_memory_candidates_status", "memory_candidates", ["status"])
    op.create_index("ix_memory_candidates_expires_at", "memory_candidates", ["expires_at"])
    op.create_index(
        "ix_memory_candidates_approved_memory_id",
        "memory_candidates",
        ["approved_memory_id"],
    )


def downgrade() -> None:
    op.drop_table("memory_candidates")
