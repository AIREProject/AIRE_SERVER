"""Create profiles, save slots, devices, and pairing codes."""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "profiles",
        sa.Column("profile_id", sa.String(length=128), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("profile_id"),
    )
    op.create_table(
        "save_slots",
        sa.Column("row_id", sa.String(length=128), nullable=False),
        sa.Column("save_slot_id", sa.String(length=128), nullable=False),
        sa.Column("profile_id", sa.String(length=128), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["profile_id"], ["profiles.profile_id"]),
        sa.PrimaryKeyConstraint("row_id"),
        sa.UniqueConstraint(
            "profile_id", "save_slot_id", name="uq_save_slots_profile_save_slot"
        ),
    )
    op.create_index("ix_save_slots_profile_id", "save_slots", ["profile_id"])
    op.create_index("ix_save_slots_save_slot_id", "save_slots", ["save_slot_id"])
    op.create_table(
        "devices",
        sa.Column("device_id", sa.String(length=128), nullable=False),
        sa.Column("profile_id", sa.String(length=128), nullable=False),
        sa.Column("role", sa.String(length=16), nullable=False),
        sa.Column("token_lookup_id", sa.String(length=128), nullable=False),
        sa.Column("token_hash", sa.String(length=64), nullable=False),
        sa.Column("creation_request_id", sa.String(length=128), nullable=False),
        sa.Column("game_registration_key", sa.String(length=32), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_used_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["profile_id"], ["profiles.profile_id"]),
        sa.PrimaryKeyConstraint("device_id"),
        sa.UniqueConstraint("token_lookup_id", name="uq_devices_token_lookup"),
        sa.UniqueConstraint("creation_request_id", name="uq_devices_creation_request"),
        sa.UniqueConstraint(
            "game_registration_key",
            name="uq_devices_single_game_registration",
        ),
    )
    op.create_index("ix_devices_profile_id", "devices", ["profile_id"])
    op.create_index("ix_devices_role", "devices", ["role"])
    op.create_table(
        "pairing_codes",
        sa.Column("pairing_code_id", sa.String(length=128), nullable=False),
        sa.Column("profile_id", sa.String(length=128), nullable=False),
        sa.Column("issuing_device_id", sa.String(length=128), nullable=False),
        sa.Column("code_hash", sa.String(length=64), nullable=False),
        sa.Column("issue_request_id", sa.String(length=128), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("used_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("redeemed_request_id", sa.String(length=128), nullable=True),
        sa.Column("paired_device_id", sa.String(length=128), nullable=True),
        sa.ForeignKeyConstraint(["issuing_device_id"], ["devices.device_id"]),
        sa.ForeignKeyConstraint(["paired_device_id"], ["devices.device_id"]),
        sa.ForeignKeyConstraint(["profile_id"], ["profiles.profile_id"]),
        sa.PrimaryKeyConstraint("pairing_code_id"),
        sa.UniqueConstraint(
            "profile_id",
            "issue_request_id",
            name="uq_pairing_codes_profile_issue_request",
        ),
        sa.UniqueConstraint(
            "redeemed_request_id",
            name="uq_pairing_codes_redeemed_request",
        ),
    )
    op.create_index("ix_pairing_codes_profile_id", "pairing_codes", ["profile_id"])
    op.create_index(
        "ix_pairing_codes_issuing_device_id", "pairing_codes", ["issuing_device_id"]
    )
    op.create_index("ix_pairing_codes_expires_at", "pairing_codes", ["expires_at"])


def downgrade() -> None:
    op.drop_table("pairing_codes")
    op.drop_table("devices")
    op.drop_table("save_slots")
    op.drop_table("profiles")
