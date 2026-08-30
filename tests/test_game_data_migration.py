import json
import os
import sqlite3
import subprocess
import sys
from pathlib import Path

from app.gamedata.dataset import ENEMIES, ITEMS, LOCATIONS, RECIPES, SMELTING_RECIPES


def _upgrade(database_url: str, revision: str) -> None:
    environment = dict(os.environ)
    environment["DATABASE_URL"] = database_url
    subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", revision],
        cwd=Path(__file__).parents[1],
        env=environment,
        check=True,
        capture_output=True,
        text=True,
    )


def test_game_data_migration_creates_and_seeds_all_tables(tmp_path, monkeypatch) -> None:
    database_path = tmp_path / "game-data.db"
    database_url = f"sqlite+aiosqlite:///{database_path}"
    monkeypatch.setenv("DATABASE_URL", database_url)

    # Alembic의 logging 설정이 현재 pytest 프로세스의 로거를 바꾸지 않도록 격리한다.
    _upgrade(database_url, "head")

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


def test_booth_recipe_migration_inserts_recipe_14_and_updates_iron_ingot(tmp_path) -> None:
    """0017까지 올라간 실제 운영 DB에도 새 제작법을 멱등 반영한다."""

    database_path = tmp_path / "existing-booth.db"
    database_url = f"sqlite+aiosqlite:///{database_path}"
    _upgrade(database_url, "0017")

    with sqlite3.connect(database_path) as connection:
        connection.execute("DELETE FROM recipes WHERE recipe_id = 'recipe-14'")
        connection.execute(
            "UPDATE recipes SET required_workbench = ? WHERE recipe_id = 'recipe-9'",
            ("Blacksmith Anvil/Furnace",),
        )
        connection.commit()

    _upgrade(database_url, "head")

    with sqlite3.connect(database_path) as connection:
        query = (
            "SELECT recipe_id, result_item_id, result_amount, required_workbench, "
            "duration_seconds, ingredients FROM recipes "
            "WHERE recipe_id IN ('recipe-9', 'recipe-14')"
        )
        rows = {}
        result = connection.execute(query)
        for row in result:
            (
                recipe_id,
                result_item_id,
                result_amount,
                required_workbench,
                duration_seconds,
                ingredients,
            ) = row
            rows[recipe_id] = (
                result_item_id,
                result_amount,
                required_workbench,
                duration_seconds,
                json.loads(ingredients),
            )

    assert rows == {
        "recipe-9": (
            "IronIngot",
            1,
            "Workbench.Smelter",
            2.0,
            [{"ItemId": "IronOre", "Amount": 2}],
        ),
        "recipe-14": (
            "WoodHandle",
            1,
            "Basic Workbench",
            1.0,
            [{"ItemId": "PlantStem", "Amount": 2}],
        ),
    }
