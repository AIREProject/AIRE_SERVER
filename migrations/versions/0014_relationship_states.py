"""Add source-audited CAI-P4 relationship states."""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0014"
down_revision: str | None = "0013"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _scope_columns() -> tuple[sa.Column[object], ...]:
    return (
        sa.Column("profile_id", sa.String(length=128), nullable=False),
        sa.Column("save_slot_row_id", sa.String(length=128), nullable=False),
        sa.Column("companion_id", sa.String(length=128), nullable=False),
    )


def upgrade() -> None:
    op.create_table(
        "relationship_states",
        sa.Column("row_id", sa.String(length=36), nullable=False),
        *_scope_columns(),
        sa.Column("state", sa.String(length=16), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "state IN ('Low', 'Growing', 'High')",
            name="ck_relationship_states_state",
        ),
        sa.ForeignKeyConstraint(["profile_id"], ["profiles.profile_id"]),
        sa.ForeignKeyConstraint(["save_slot_row_id"], ["save_slots.row_id"]),
        sa.PrimaryKeyConstraint("row_id"),
        sa.UniqueConstraint(
            "profile_id",
            "save_slot_row_id",
            "companion_id",
            name="uq_relationship_states_scope",
        ),
    )
    for column in ("profile_id", "save_slot_row_id", "companion_id"):
        op.create_index(f"ix_relationship_states_{column}", "relationship_states", [column])

    op.create_table(
        "relationship_state_evidence",
        sa.Column("row_id", sa.String(length=36), nullable=False),
        *_scope_columns(),
        sa.Column("preference_memory_id", sa.String(length=128), nullable=False),
        sa.Column("message_source_id", sa.String(length=128), nullable=False),
        sa.Column("event_source_id", sa.String(length=128), nullable=False),
        sa.Column("event_type", sa.String(length=64), nullable=False),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("accepted_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "event_type IN ('Event.Danger.Detected', 'Event.Rescue.Completed')",
            name="ck_relationship_evidence_event_type",
        ),
        sa.ForeignKeyConstraint(["profile_id"], ["profiles.profile_id"]),
        sa.ForeignKeyConstraint(["save_slot_row_id"], ["save_slots.row_id"]),
        sa.PrimaryKeyConstraint("row_id"),
        sa.UniqueConstraint(
            "profile_id",
            "save_slot_row_id",
            "companion_id",
            "preference_memory_id",
            "event_source_id",
            name="uq_relationship_evidence_source_pair",
        ),
    )
    for column in (
        "profile_id",
        "save_slot_row_id",
        "companion_id",
        "preference_memory_id",
        "message_source_id",
        "event_source_id",
    ):
        op.create_index(
            f"ix_relationship_state_evidence_{column}", "relationship_state_evidence", [column]
        )

    op.create_table(
        "relationship_state_audits",
        sa.Column("row_id", sa.String(length=36), nullable=False),
        sa.Column("relationship_state_row_id", sa.String(length=36), nullable=False),
        sa.Column("evidence_row_id", sa.String(length=36), nullable=True),
        sa.Column("previous_state", sa.String(length=16), nullable=False),
        sa.Column("next_state", sa.String(length=16), nullable=False),
        sa.Column("reason", sa.String(length=32), nullable=False),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "previous_state IN ('Low', 'Growing', 'High')",
            name="ck_relationship_audits_previous_state",
        ),
        sa.CheckConstraint(
            "next_state IN ('Low', 'Growing', 'High')",
            name="ck_relationship_audits_next_state",
        ),
        sa.CheckConstraint(
            "reason IN ('EvidenceAccepted', 'SourceInvalidated')",
            name="ck_relationship_audits_reason",
        ),
        sa.ForeignKeyConstraint(["relationship_state_row_id"], ["relationship_states.row_id"]),
        sa.PrimaryKeyConstraint("row_id"),
    )
    op.create_index(
        "ix_relationship_state_audits_relationship_state_row_id",
        "relationship_state_audits",
        ["relationship_state_row_id"],
    )
    op.create_index(
        "ix_relationship_state_audits_evidence_row_id",
        "relationship_state_audits",
        ["evidence_row_id"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_relationship_state_audits_evidence_row_id",
        table_name="relationship_state_audits",
    )
    op.drop_index(
        "ix_relationship_state_audits_relationship_state_row_id",
        table_name="relationship_state_audits",
    )
    op.drop_table("relationship_state_audits")
    for column in (
        "event_source_id",
        "message_source_id",
        "preference_memory_id",
        "companion_id",
        "save_slot_row_id",
        "profile_id",
    ):
        op.drop_index(
            f"ix_relationship_state_evidence_{column}", table_name="relationship_state_evidence"
        )
    op.drop_table("relationship_state_evidence")
    for column in ("companion_id", "save_slot_row_id", "profile_id"):
        op.drop_index(f"ix_relationship_states_{column}", table_name="relationship_states")
    op.drop_table("relationship_states")
