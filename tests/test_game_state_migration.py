"""Alembic coverage for AX-I09 Game State persistence."""

import os
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest


def _unique_indexes(connection: sqlite3.Connection, table: str) -> list[list[str]]:
    return [
        [column[2] for column in connection.execute(f"PRAGMA index_info('{index[1]}')")]
        for index in connection.execute(f"PRAGMA index_list('{table}')")
        if index[2]
    ]


def test_game_state_migration_creates_snapshot_and_operation_tables(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    database_path = tmp_path / "game-state.db"
    monkeypatch.setenv("DATABASE_URL", f"sqlite+aiosqlite:///{database_path}")
    environment = dict(os.environ)
    repository_root = Path(__file__).parents[1]

    subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", "head"],
        cwd=repository_root,
        env=environment,
        check=True,
        capture_output=True,
        text=True,
    )

    with sqlite3.connect(database_path) as connection:
        snapshot_columns = {
            row[1]: (row[2], row[3])
            for row in connection.execute("PRAGMA table_info(game_state_snapshots)")
        }
        operation_columns = {
            row[1]: (row[2], row[3])
            for row in connection.execute("PRAGMA table_info(game_state_operations)")
        }
        snapshot_foreign_keys = {
            row[2] for row in connection.execute("PRAGMA foreign_key_list(game_state_snapshots)")
        }
        operation_foreign_keys = {
            row[2] for row in connection.execute("PRAGMA foreign_key_list(game_state_operations)")
        }
        snapshot_indexes = _unique_indexes(connection, "game_state_snapshots")
        operation_indexes = _unique_indexes(connection, "game_state_operations")
        snapshot_sql = connection.execute(
            "SELECT sql FROM sqlite_master WHERE type='table' AND name='game_state_snapshots'"
        ).fetchone()[0]
        operation_sql = connection.execute(
            "SELECT sql FROM sqlite_master WHERE type='table' AND name='game_state_operations'"
        ).fetchone()[0]
        revision = connection.execute("SELECT version_num FROM alembic_version").fetchone()

    assert revision == ("0016",)
    assert snapshot_columns == {
        "row_id": ("VARCHAR(36)", 1),
        "profile_id": ("VARCHAR(128)", 1),
        "save_slot_row_id": ("VARCHAR(128)", 1),
        "companion_id": ("VARCHAR(128)", 1),
        "schema_version": ("INTEGER", 1),
        "content_version": ("INTEGER", 1),
        "operation_id": ("VARCHAR(128)", 1),
        "state_version": ("INTEGER", 1),
        "world_session_id": ("VARCHAR(128)", 1),
        "captured_at": ("DATETIME", 1),
        "last_synced_at": ("DATETIME", 1),
        "payload": ("JSON", 1),
        "payload_size_bytes": ("INTEGER", 1),
    }
    assert operation_columns == {
        "row_id": ("VARCHAR(36)", 1),
        "profile_id": ("VARCHAR(128)", 1),
        "save_slot_row_id": ("VARCHAR(128)", 1),
        "companion_id": ("VARCHAR(128)", 1),
        "operation_id": ("VARCHAR(128)", 1),
        "body_hash": ("VARCHAR(64)", 1),
        "response_status": ("INTEGER", 1),
        "response_body": ("JSON", 1),
        "created_at": ("DATETIME", 1),
        "completed_at": ("DATETIME", 1),
    }
    assert snapshot_foreign_keys == {"profiles", "save_slots"}
    assert operation_foreign_keys == {"profiles", "save_slots"}
    assert ["profile_id", "save_slot_row_id", "companion_id"] in snapshot_indexes
    assert [
        "profile_id",
        "save_slot_row_id",
        "companion_id",
        "operation_id",
    ] in operation_indexes
    assert "schema_version = 1" in snapshot_sql
    assert "content_version = 1" in snapshot_sql
    assert "state_version > 0" in snapshot_sql
    assert "payload_size_bytes >= 0 AND payload_size_bytes <= 262144" in snapshot_sql
    assert "length(body_hash) = 64" in operation_sql
