import os
import sqlite3
import subprocess
import sys
from pathlib import Path


def test_offline_task_migration_creates_scoped_table(tmp_path, monkeypatch) -> None:
    database_path = tmp_path / "offline-tasks.db"
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
        columns = {
            row[1]: (row[2], row[3])
            for row in connection.execute("PRAGMA table_info(offline_tasks)")
        }
        foreign_keys = {
            row[2]
            for row in connection.execute("PRAGMA foreign_key_list(offline_tasks)")
        }
        unique_indexes = {
            row[1]: [
                column[2]
                for column in connection.execute(f"PRAGMA index_info('{row[1]}')")
            ]
            for row in connection.execute("PRAGMA index_list(offline_tasks)")
            if row[2]
        }

    assert columns["task_id"] == ("VARCHAR(128)", 1)
    assert columns["item_id"] == ("VARCHAR(128)", 0)
    assert columns["task_type"] == ("VARCHAR(16)", 1)
    assert columns["status"] == ("VARCHAR(16)", 1)
    assert columns["started_at"] == ("DATETIME", 1)
    assert columns["quantity"] == ("INTEGER", 0)
    assert columns["result_quantity"] == ("INTEGER", 0)
    assert foreign_keys == {"profiles", "save_slots", "devices", "items"}
    assert [
        "profile_id",
        "save_slot_row_id",
        "creation_request_id",
    ] in unique_indexes.values()
