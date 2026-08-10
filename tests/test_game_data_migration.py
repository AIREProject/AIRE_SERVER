import json
import os
import sqlite3
import subprocess
import sys
from pathlib import Path

from app.gamedata.dataset import ENEMIES, ITEMS, LOCATIONS, RECIPES, SMELTING_RECIPES


def test_game_data_migration_creates_and_seeds_all_tables(tmp_path, monkeypatch) -> None:
    database_path = tmp_path / "game-data.db"
    database_url = f"sqlite+aiosqlite:///{database_path}"
    monkeypatch.setenv("DATABASE_URL", database_url)

    # Alembic의 logging 설정이 현재 pytest 프로세스의 로거를 바꾸지 않도록 격리한다.
    subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", "head"],
        cwd=Path(__file__).parents[1],
        env=dict(os.environ),
        check=True,
        capture_output=True,
        text=True,
    )

    with sqlite3.connect(database_path) as connection:
        counts = {
            table: connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
            for table in ("items", "recipes", "smelting_recipes", "enemies", "locations")
        }
        enemy_aliases = {
            enemy_id: json.loads(aliases)
            for enemy_id, aliases in connection.execute(
                "SELECT enemy_id, aliases FROM enemies"
            )
        }

    assert counts == {
        "items": len(ITEMS),
        "recipes": len(RECIPES),
        "smelting_recipes": len(SMELTING_RECIPES),
        "enemies": len(ENEMIES),
        "locations": len(LOCATIONS),
    }
    assert enemy_aliases == {enemy.enemy_id: list(enemy.aliases) for enemy in ENEMIES}
