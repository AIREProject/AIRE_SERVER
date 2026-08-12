import json
import os
import sqlite3
import subprocess
import sys
from pathlib import Path


def test_legacy_memory_files_are_imported_with_the_new_scale(tmp_path, monkeypatch) -> None:
    database_path = tmp_path / "episodic.db"
    memory_directory = tmp_path / "memories"
    memory_directory.mkdir()
    player_key = "a" * 64
    (memory_directory / f"{player_key}.json").write_text(
        json.dumps(
            {
                "version": 2,
                "memories": [
                    {
                        "kind": "profile",
                        "text": "플레이어는 밤을 싫어한다",
                        "importance": 3,
                        "created_at": "2026-07-30T12:00:00Z",
                    }
                ]
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("DATABASE_URL", f"sqlite+aiosqlite:///{database_path}")
    monkeypatch.setenv("LONG_TERM_MEMORY_DIR", str(memory_directory))

    subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", "head"],
        cwd=Path(__file__).parents[1],
        env=dict(os.environ),
        check=True,
        capture_output=True,
        text=True,
    )

    with sqlite3.connect(database_path) as connection:
        row = connection.execute(
            "SELECT player_key, importance, text FROM episodic_memories"
        ).fetchone()

    assert row == (player_key, 6, "플레이어는 밤을 싫어한다")


def test_episodic_memory_migration_succeeds_without_memory_directory(tmp_path, monkeypatch) -> None:
    database_path = tmp_path / "empty.db"
    monkeypatch.setenv("DATABASE_URL", f"sqlite+aiosqlite:///{database_path}")
    monkeypatch.setenv("LONG_TERM_MEMORY_DIR", str(tmp_path / "missing-memories"))

    subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", "head"],
        cwd=Path(__file__).parents[1],
        env=dict(os.environ),
        check=True,
        capture_output=True,
        text=True,
    )

    with sqlite3.connect(database_path) as connection:
        assert connection.execute("SELECT COUNT(*) FROM episodic_memories").fetchone() == (0,)
