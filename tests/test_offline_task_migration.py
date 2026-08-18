import json
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
            row[2] for row in connection.execute("PRAGMA foreign_key_list(offline_tasks)")
        }
        unique_indexes = {
            row[1]: [column[2] for column in connection.execute(f"PRAGMA index_info('{row[1]}')")]
            for row in connection.execute("PRAGMA index_list(offline_tasks)")
            if row[2]
        }
        policy_columns = {
            row[1]: (row[2], row[3])
            for row in connection.execute("PRAGMA table_info(offline_task_policies)")
        }
        policies = list(
            connection.execute(
                "SELECT policy_id, task_type, item_id, seconds_per_item "
                "FROM offline_task_policies ORDER BY policy_id"
            )
        )
        item_ids = {row[0] for row in connection.execute("SELECT item_id FROM items")}
        plant_stem = connection.execute(
            "SELECT name_ko, aliases FROM items WHERE item_id = 'PlantStem'"
        ).fetchone()
        recipe_ingredients = {
            row[0]: row[1]
            for row in connection.execute(
                "SELECT recipe_id, ingredients FROM recipes "
                "WHERE recipe_id IN ('recipe-3', 'recipe-4', 'recipe-5')"
            )
        }

    assert columns["task_id"] == ("VARCHAR(128)", 1)
    assert columns["item_id"] == ("VARCHAR(128)", 0)
    assert columns["task_type"] == ("VARCHAR(16)", 1)
    assert columns["status"] == ("VARCHAR(16)", 1)
    assert columns["started_at"] == ("DATETIME", 1)
    assert columns["quantity"] == ("INTEGER", 0)
    assert columns["result_quantity"] == ("INTEGER", 0)
    assert columns["seconds_per_item"] == ("FLOAT", 0)
    assert foreign_keys == {"profiles", "save_slots", "devices", "items"}
    assert [
        "profile_id",
        "save_slot_row_id",
        "creation_request_id",
    ] in unique_indexes.values()
    assert policy_columns["policy_id"] == ("VARCHAR(128)", 1)
    assert policy_columns["seconds_per_item"] == ("FLOAT", 1)
    assert policies == [
        ("crafting-shoddy-bandage", "Crafting", "ShoddyBandage", 10.0),
        ("gathering-plant-stem", "Gathering", "PlantStem", 5.0),
    ]
    assert "Branch" not in item_ids
    assert plant_stem is not None
    assert plant_stem[0] == "나무"
    assert "나무" in json.loads(plant_stem[1])
    assert all("Branch" not in ingredients for ingredients in recipe_ingredients.values())
    assert all("PlantStem" in ingredients for ingredients in recipe_ingredients.values())


def test_plant_stem_migration_merges_legacy_branch_rows(tmp_path, monkeypatch) -> None:
    database_path = tmp_path / "legacy-branch.db"
    monkeypatch.setenv("DATABASE_URL", f"sqlite+aiosqlite:///{database_path}")
    environment = dict(os.environ)
    repository_root = Path(__file__).parents[1]

    subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", "0014"],
        cwd=repository_root,
        env=environment,
        check=True,
        capture_output=True,
        text=True,
    )
    with sqlite3.connect(database_path) as connection:
        connection.execute(
            "INSERT INTO items(item_id, item_type, name_ko, aliases, description) "
            "VALUES('Branch', 'Material', '나뭇가지', '[\"나뭇가지\"]', 'legacy')"
        )
        connection.execute(
            "UPDATE recipes SET ingredients = "
            '\'[{"ItemId":"Branch","Amount":2},'
            '{"ItemId":"Stone","Amount":1}]\' '
            "WHERE recipe_id = 'recipe-3'"
        )
        connection.execute(
            "INSERT INTO offline_tasks("
            "task_id, profile_id, save_slot_row_id, issuing_device_id, item_id, "
            "task_type, status, started_at, creation_request_id, quantity, "
            "result_quantity, seconds_per_item"
            ") VALUES("
            "'legacy-task', 'legacy-profile', 'legacy-slot', 'legacy-device', 'Branch', "
            "'Gathering', 'InProgress', '2026-01-01', 'legacy-request', 3, NULL, 600.0"
            ")"
        )
        connection.commit()

    subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", "head"],
        cwd=repository_root,
        env=environment,
        check=True,
        capture_output=True,
        text=True,
    )

    with sqlite3.connect(database_path) as connection:
        branch_count = connection.execute(
            "SELECT COUNT(*) FROM items WHERE item_id = 'Branch'"
        ).fetchone()
        recipe = connection.execute(
            "SELECT ingredients FROM recipes WHERE recipe_id = 'recipe-3'"
        ).fetchone()
        task = connection.execute(
            "SELECT item_id, seconds_per_item FROM offline_tasks WHERE task_id = 'legacy-task'"
        ).fetchone()

    assert branch_count == (0,)
    assert recipe is not None and "PlantStem" in recipe[0] and "Branch" not in recipe[0]
    assert task == ("PlantStem", 5.0)
