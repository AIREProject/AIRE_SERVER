"""Alembic coverage for CAI-P2 canonical source persistence."""

import os
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest

TABLES = {
    "conversations",
    "messages",
    "chat_operations",
    "command_candidates",
    "game_events",
    "command_results",
    "source_retention_references",
    "source_outbox",
    "source_cursors",
    "legacy_import_reports",
    "legacy_episodic_memories",
    "memories",
    "memory_sources",
    "memory_migration_reports",
}


def _upgrade(database_path: Path, revision: str, environment: dict[str, str]) -> None:
    environment["DATABASE_URL"] = f"sqlite+aiosqlite:///{database_path}"
    subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", revision],
        cwd=Path(__file__).parents[1],
        env=environment,
        check=True,
        capture_output=True,
        text=True,
    )


@pytest.mark.parametrize("start_revision", [None, "0010"])
def test_source_backed_memory_migrations_upgrade_from_fresh_and_0010(
    tmp_path: Path,
    start_revision: str | None,
) -> None:
    database_path = tmp_path / f"canonical-{start_revision or 'fresh'}.db"
    environment = dict(os.environ)
    if start_revision is not None:
        _upgrade(database_path, start_revision, environment)
    _upgrade(database_path, "head", environment)

    with sqlite3.connect(database_path) as connection:
        tables = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            )
        }
        revision = connection.execute(
            "SELECT version_num FROM alembic_version"
        ).fetchone()
        message_sql = connection.execute(
            "SELECT sql FROM sqlite_master WHERE name = 'messages'"
        ).fetchone()[0]
        outbox_sql = connection.execute(
            "SELECT sql FROM sqlite_master WHERE name = 'source_outbox'"
        ).fetchone()[0]
        event_sql = connection.execute(
            "SELECT sql FROM sqlite_master WHERE name = 'game_events'"
        ).fetchone()[0]
        result_sql = connection.execute(
            "SELECT sql FROM sqlite_master WHERE name = 'command_results'"
        ).fetchone()[0]
        memory_columns = {
            row[1] for row in connection.execute("PRAGMA table_info(memories)")
        }

    assert TABLES <= tables
    assert revision == ("0012",)
    assert {"recalled_at", "recall_count", "embedding", "embedding_model"} <= memory_columns
    assert "uq_messages_conversation_sequence" in message_sql
    assert "Transient" in message_sql and "MemorySource" in message_sql
    assert "Tombstone" in outbox_sql
    assert "schema_version = 1" in event_sql
    assert "schema_version = 1" in result_sql
