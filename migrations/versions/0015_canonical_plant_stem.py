"""Merge the legacy Branch item ID into canonical PlantStem."""

from __future__ import annotations

import json
from collections.abc import Sequence
from typing import Any

import sqlalchemy as sa
from alembic import op

from app.gamedata.dataset import DATASET

revision: str = "0015"
down_revision: str | None = "0014"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _replace_item_id(value: Any, old: str, new: str) -> Any:
    if isinstance(value, dict):
        return {key: _replace_item_id(entry, old, new) for key, entry in value.items()}
    if isinstance(value, list):
        return [_replace_item_id(entry, old, new) for entry in value]
    return new if value == old else value


def _plant_stem_values() -> dict[str, object]:
    item = next(item for item in DATASET.items if item.item_id == "PlantStem")
    return {
        "item_type": item.item_type,
        "name_ko": item.name_ko,
        "aliases": list(item.aliases),
        "description": item.description,
    }


def _rewrite_json_column(
    table: sa.TableClause,
    primary_key: sa.ColumnClause[Any],
    payload: sa.ColumnClause[Any],
    *,
    size_column: sa.ColumnClause[Any] | None = None,
) -> None:
    connection = op.get_bind()
    for row in connection.execute(sa.select(primary_key, payload)).mappings():
        current = row[payload.name]
        rewritten = _replace_item_id(current, "Branch", "PlantStem")
        if rewritten == current:
            continue
        values: dict[str, object] = {payload.name: rewritten}
        if size_column is not None:
            values[size_column.name] = len(
                json.dumps(rewritten, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
            )
        connection.execute(
            table.update().where(primary_key == row[primary_key.name]).values(**values)
        )


def upgrade() -> None:
    items = sa.table(
        "items",
        sa.column("item_id", sa.String()),
        sa.column("item_type", sa.String()),
        sa.column("name_ko", sa.String()),
        sa.column("aliases", sa.JSON()),
        sa.column("description", sa.String()),
    )
    recipes = sa.table(
        "recipes",
        sa.column("recipe_id", sa.String()),
        sa.column("ingredients", sa.JSON()),
    )
    policies = sa.table(
        "offline_task_policies",
        sa.column("policy_id", sa.String()),
        sa.column("item_id", sa.String()),
    )
    tasks = sa.table(
        "offline_tasks",
        sa.column("task_id", sa.String()),
        sa.column("item_id", sa.String()),
        sa.column("seconds_per_item", sa.Float()),
    )
    snapshots = sa.table(
        "game_state_snapshots",
        sa.column("row_id", sa.String()),
        sa.column("payload", sa.JSON()),
        sa.column("payload_size_bytes", sa.Integer()),
    )
    operations = sa.table(
        "game_state_operations",
        sa.column("row_id", sa.String()),
        sa.column("response_body", sa.JSON()),
    )
    connection = op.get_bind()

    connection.execute(
        items.update().where(items.c.item_id == "PlantStem").values(**_plant_stem_values())
    )
    _rewrite_json_column(recipes, recipes.c.recipe_id, recipes.c.ingredients)
    _rewrite_json_column(
        snapshots,
        snapshots.c.row_id,
        snapshots.c.payload,
        size_column=snapshots.c.payload_size_bytes,
    )
    _rewrite_json_column(operations, operations.c.row_id, operations.c.response_body)
    connection.execute(policies.delete().where(policies.c.item_id == "Branch"))
    connection.execute(
        tasks.update()
        .where(tasks.c.item_id == "Branch")
        .values(item_id="PlantStem", seconds_per_item=5.0)
    )
    connection.execute(items.delete().where(items.c.item_id == "Branch"))


def downgrade() -> None:
    items = sa.table(
        "items",
        sa.column("item_id", sa.String()),
        sa.column("item_type", sa.String()),
        sa.column("name_ko", sa.String()),
        sa.column("aliases", sa.JSON()),
        sa.column("description", sa.String()),
    )
    recipes = sa.table(
        "recipes",
        sa.column("recipe_id", sa.String()),
        sa.column("ingredients", sa.JSON()),
    )
    connection = op.get_bind()
    branch_exists = connection.execute(
        sa.select(items.c.item_id).where(items.c.item_id == "Branch")
    ).first()
    if branch_exists is None:
        connection.execute(
            items.insert().values(
                item_id="Branch",
                item_type="Material",
                name_ko="나뭇가지",
                aliases=["나뭇가지", "나무 가지", "가지"],
                description="나무에서 떨어진 굵은 나뭇가지. 기초적인 도구의 손잡이나 땔감으로 씀.",
            )
        )
    legacy_ingredients = {
        "recipe-3": [
            {"ItemId": "Branch", "Amount": 2},
            {"ItemId": "Stone", "Amount": 1},
        ],
        "recipe-4": [
            {"ItemId": "Branch", "Amount": 2},
            {"ItemId": "Stone", "Amount": 2},
        ],
        "recipe-5": [
            {"ItemId": "Branch", "Amount": 5},
            {"ItemId": "Stone", "Amount": 3},
        ],
    }
    for recipe_id, ingredients in legacy_ingredients.items():
        connection.execute(
            recipes.update().where(recipes.c.recipe_id == recipe_id).values(ingredients=ingredients)
        )
