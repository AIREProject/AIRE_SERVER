"""Create canonical chat, event, command-result and retention ledgers."""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0010"
down_revision: str | None = "0009"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _scope_columns() -> tuple[sa.Column[object], ...]:
    return (
        sa.Column("profile_id", sa.String(length=128), nullable=False),
        sa.Column("save_slot_row_id", sa.String(length=128), nullable=False),
        sa.Column("companion_id", sa.String(length=128), nullable=False),
    )


def _scope_foreign_keys() -> tuple[sa.ForeignKeyConstraint, ...]:
    return (
        sa.ForeignKeyConstraint(["profile_id"], ["profiles.profile_id"]),
        sa.ForeignKeyConstraint(["save_slot_row_id"], ["save_slots.row_id"]),
    )


def _scope_indexes(table: str) -> None:
    for column in ("profile_id", "save_slot_row_id", "companion_id"):
        op.create_index(f"ix_{table}_{column}", table, [column])


def upgrade() -> None:
    op.create_table(
        "conversations",
        sa.Column("row_id", sa.String(length=36), nullable=False),
        sa.Column("conversation_id", sa.String(length=128), nullable=False),
        *_scope_columns(),
        sa.Column("session_id", sa.String(length=128), nullable=False),
        sa.Column("surface", sa.String(length=16), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        *_scope_foreign_keys(),
        sa.PrimaryKeyConstraint("row_id"),
        sa.UniqueConstraint("conversation_id"),
        sa.UniqueConstraint(
            "profile_id", "save_slot_row_id", "companion_id", "session_id", "surface",
            name="uq_conversations_scope_session_surface",
        ),
    )
    _scope_indexes("conversations")

    op.create_table(
        "messages",
        sa.Column("row_id", sa.String(length=36), nullable=False),
        sa.Column("message_id", sa.String(length=128), nullable=False),
        sa.Column("conversation_row_id", sa.String(length=36), nullable=False),
        *_scope_columns(),
        sa.Column("request_id", sa.String(length=128), nullable=False),
        sa.Column("sequence", sa.Integer(), nullable=False),
        sa.Column("speaker", sa.String(length=16), nullable=True),
        sa.Column("source_mode", sa.String(length=16), nullable=True),
        sa.Column("content", sa.Text(), nullable=True),
        sa.Column("content_digest", sa.String(length=64), nullable=False),
        sa.Column("time_context", sa.JSON(), nullable=True),
        sa.Column("storage_class", sa.String(length=16), nullable=False),
        sa.Column("retention_reason", sa.String(length=64), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("audit_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("content_deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("delivered_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint("length(content_digest) = 64", name="ck_messages_content_digest"),
        sa.CheckConstraint(
            "storage_class IN ('Transient', 'MemorySource')",
            name="ck_messages_storage_class",
        ),
        sa.ForeignKeyConstraint(["conversation_row_id"], ["conversations.row_id"]),
        *_scope_foreign_keys(),
        sa.PrimaryKeyConstraint("row_id"),
        sa.UniqueConstraint("message_id", name="uq_messages_message_id"),
        sa.UniqueConstraint(
            "conversation_row_id",
            "sequence",
            name="uq_messages_conversation_sequence",
        ),
        sa.UniqueConstraint(
            "profile_id", "save_slot_row_id", "companion_id", "request_id", "speaker",
            name="uq_messages_scope_request_speaker",
        ),
    )
    _scope_indexes("messages")
    for column in (
        "message_id",
        "conversation_row_id",
        "request_id",
        "expires_at",
        "audit_expires_at",
    ):
        op.create_index(f"ix_messages_{column}", "messages", [column])

    op.create_table(
        "chat_operations",
        sa.Column("row_id", sa.String(length=36), nullable=False),
        *_scope_columns(),
        sa.Column("request_id", sa.String(length=128), nullable=False),
        sa.Column("request_digest", sa.String(length=64), nullable=False),
        sa.Column("state", sa.String(length=24), nullable=False),
        sa.Column("input_message_id", sa.String(length=128), nullable=False),
        sa.Column("response_message_id", sa.String(length=128), nullable=True),
        sa.Column("response_metadata", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("audit_expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("length(request_digest) = 64", name="ck_chat_operations_digest"),
        sa.CheckConstraint(
            "state IN ('Pending', 'Generated', 'Completed')",
            name="ck_chat_operations_state",
        ),
        *_scope_foreign_keys(),
        sa.PrimaryKeyConstraint("row_id"),
        sa.UniqueConstraint(
            "profile_id", "save_slot_row_id", "companion_id", "request_id",
            name="uq_chat_operations_scope_request",
        ),
    )
    _scope_indexes("chat_operations")
    op.create_index("ix_chat_operations_state", "chat_operations", ["state"])
    op.create_index("ix_chat_operations_audit_expires_at", "chat_operations", ["audit_expires_at"])

    op.create_table(
        "command_candidates",
        sa.Column("row_id", sa.String(length=36), nullable=False),
        sa.Column("command_id", sa.String(length=128), nullable=False),
        *_scope_columns(),
        sa.Column("session_id", sa.String(length=128), nullable=False),
        sa.Column("request_id", sa.String(length=128), nullable=False),
        sa.Column("command_type", sa.String(length=64), nullable=False),
        sa.Column("target_id", sa.String(length=128), nullable=True),
        sa.Column("priority", sa.String(length=16), nullable=False),
        sa.Column("issued_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("parameters", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("audit_expires_at", sa.DateTime(timezone=True), nullable=False),
        *_scope_foreign_keys(),
        sa.PrimaryKeyConstraint("row_id"),
        sa.UniqueConstraint(
            "profile_id",
            "save_slot_row_id",
            "companion_id",
            "session_id",
            "command_id",
            name="uq_command_candidates_scope_command",
        ),
    )
    _scope_indexes("command_candidates")
    op.create_index("ix_command_candidates_request_id", "command_candidates", ["request_id"])
    op.create_index(
        "ix_command_candidates_audit_expires_at",
        "command_candidates",
        ["audit_expires_at"],
    )

    op.create_table(
        "game_events",
        sa.Column("row_id", sa.String(length=36), nullable=False),
        sa.Column("event_id", sa.String(length=128), nullable=False),
        *_scope_columns(),
        sa.Column("session_id", sa.String(length=128), nullable=False),
        sa.Column("schema_version", sa.Integer(), nullable=False),
        sa.Column("event_type", sa.String(length=64), nullable=True),
        sa.Column("importance", sa.String(length=16), nullable=True),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("game_time", sa.JSON(), nullable=True),
        sa.Column("actor_id", sa.String(length=128), nullable=True),
        sa.Column("target_ids", sa.JSON(), nullable=True),
        sa.Column("body_hash", sa.String(length=64), nullable=False),
        sa.Column("storage_class", sa.String(length=16), nullable=False),
        sa.Column("retention_reason", sa.String(length=64), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("audit_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("content_deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("response_body", sa.JSON(), nullable=True),
        sa.Column("received_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint("length(body_hash) = 64", name="ck_game_events_body_hash"),
        sa.CheckConstraint("schema_version = 1", name="ck_game_events_schema_version"),
        sa.CheckConstraint(
            "storage_class IN ('Transient', 'MemorySource')",
            name="ck_game_events_storage_class",
        ),
        *_scope_foreign_keys(),
        sa.PrimaryKeyConstraint("row_id"),
        sa.UniqueConstraint(
            "profile_id", "save_slot_row_id", "companion_id", "event_id",
            name="uq_game_events_scope_event",
        ),
    )
    _scope_indexes("game_events")
    for column in ("event_id", "expires_at", "audit_expires_at"):
        op.create_index(f"ix_game_events_{column}", "game_events", [column])

    op.create_table(
        "command_results",
        sa.Column("row_id", sa.String(length=36), nullable=False),
        sa.Column("operation_id", sa.String(length=128), nullable=False),
        *_scope_columns(),
        sa.Column("schema_version", sa.Integer(), nullable=False),
        sa.Column("candidate_row_id", sa.String(length=36), nullable=False),
        sa.Column("command_id", sa.String(length=128), nullable=False),
        sa.Column("request_id", sa.String(length=128), nullable=False),
        sa.Column("command_type", sa.String(length=64), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("reason", sa.String(length=64), nullable=False),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("game_time", sa.JSON(), nullable=False),
        sa.Column("body_hash", sa.String(length=64), nullable=False),
        sa.Column("response_body", sa.JSON(), nullable=False),
        sa.Column("received_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("audit_expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("length(body_hash) = 64", name="ck_command_results_body_hash"),
        sa.CheckConstraint("schema_version = 1", name="ck_command_results_schema_version"),
        sa.CheckConstraint(
            "status IN ('Accepted', 'Running', 'Succeeded', 'Rejected', "
            "'Failed', 'Cancelled', 'Expired')",
            name="ck_command_results_status",
        ),
        sa.ForeignKeyConstraint(["candidate_row_id"], ["command_candidates.row_id"]),
        *_scope_foreign_keys(),
        sa.PrimaryKeyConstraint("row_id"),
        sa.UniqueConstraint(
            "profile_id", "save_slot_row_id", "companion_id", "operation_id",
            name="uq_command_results_scope_operation",
        ),
    )
    _scope_indexes("command_results")
    for column in (
        "operation_id",
        "candidate_row_id",
        "command_id",
        "status",
        "audit_expires_at",
    ):
        op.create_index(f"ix_command_results_{column}", "command_results", [column])

    op.create_table(
        "source_retention_references",
        sa.Column("row_id", sa.String(length=36), nullable=False),
        sa.Column("source_type", sa.String(length=16), nullable=False),
        sa.Column("source_id", sa.String(length=128), nullable=False),
        sa.Column("reference_id", sa.String(length=128), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "source_type IN ('Message', 'Event')",
            name="ck_source_retention_references_type",
        ),
        sa.PrimaryKeyConstraint("row_id"),
        sa.UniqueConstraint(
            "source_type",
            "source_id",
            "reference_id",
            name="uq_source_retention_ref",
        ),
    )
    op.create_index(
        "ix_source_retention_references_source_type",
        "source_retention_references",
        ["source_type"],
    )
    op.create_index(
        "ix_source_retention_references_source_id",
        "source_retention_references",
        ["source_id"],
    )

    op.create_table(
        "source_outbox",
        sa.Column("source_seq", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("source_type", sa.String(length=16), nullable=False),
        sa.Column("source_id", sa.String(length=128), nullable=False),
        sa.Column("state", sa.String(length=16), nullable=False),
        sa.Column("lease_token", sa.String(length=36), nullable=True),
        sa.Column("lease_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("attempt_count", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "state IN ('Pending', 'Claimed', 'Completed', 'Tombstone')",
            name="ck_source_outbox_state",
        ),
        sa.CheckConstraint(
            "source_type IN ('Message', 'Event')",
            name="ck_source_outbox_type",
        ),
        sa.CheckConstraint("attempt_count >= 0", name="ck_source_outbox_attempt_count"),
        sa.PrimaryKeyConstraint("source_seq"),
        sa.UniqueConstraint("source_type", "source_id", name="uq_source_outbox_source"),
    )
    for column in ("source_type", "source_id", "state"):
        op.create_index(f"ix_source_outbox_{column}", "source_outbox", [column])

    op.create_table(
        "source_cursors",
        sa.Column("consumer", sa.String(length=64), nullable=False),
        sa.Column("last_completed_seq", sa.Integer(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "last_completed_seq >= 0", name="ck_source_cursors_sequence"
        ),
        sa.PrimaryKeyConstraint("consumer"),
    )

    op.create_table(
        "legacy_import_reports",
        sa.Column("row_id", sa.String(length=36), nullable=False),
        sa.Column("file_name", sa.String(length=255), nullable=False),
        sa.Column("file_hash", sa.String(length=64), nullable=False),
        sa.Column("status", sa.String(length=24), nullable=False),
        sa.Column("imported_count", sa.Integer(), nullable=False),
        sa.Column("quarantined_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("delete_after", sa.DateTime(timezone=True), nullable=True),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("error_code", sa.String(length=64), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("length(file_hash) = 64", name="ck_legacy_import_reports_hash"),
        sa.CheckConstraint(
            "imported_count >= 0", name="ck_legacy_import_reports_imported_count"
        ),
        sa.PrimaryKeyConstraint("row_id"),
        sa.UniqueConstraint(
            "file_name",
            "file_hash",
            name="uq_legacy_import_reports_file_hash",
        ),
    )
    op.create_index("ix_legacy_import_reports_status", "legacy_import_reports", ["status"])
    op.create_index(
        "ix_legacy_import_reports_delete_after",
        "legacy_import_reports",
        ["delete_after"],
    )


def downgrade() -> None:
    for table in (
        "legacy_import_reports", "source_cursors", "source_outbox",
        "source_retention_references", "command_results", "game_events",
        "command_candidates", "chat_operations", "messages", "conversations",
    ):
        op.drop_table(table)
