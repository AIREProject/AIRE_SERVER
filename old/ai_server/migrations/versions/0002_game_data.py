"""Create and seed the static game data tables."""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

from app.gamedata.dataset import DATASET

revision: str = "0002"
down_revision: str | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _ingredient_value(item_id: str, amount: int) -> dict[str, object]:
    return {"ItemId": item_id, "Amount": amount}


def upgrade() -> None:
    op.create_table(
        "items",
        sa.Column("item_id", sa.String(length=128), nullable=False),
        sa.Column("item_type", sa.String(length=32), nullable=False),
        sa.Column("name_ko", sa.String(length=128), nullable=False),
        sa.Column("aliases", sa.JSON(), nullable=False),
        sa.Column("description", sa.String(length=1000), nullable=False),
        sa.PrimaryKeyConstraint("item_id"),
    )
    op.create_index("ix_items_item_type", "items", ["item_type"])
    op.create_index("ix_items_name_ko", "items", ["name_ko"])

    op.create_table(
        "recipes",
        sa.Column("recipe_id", sa.String(length=128), nullable=False),
        sa.Column("result_item_id", sa.String(length=128), nullable=False),
        sa.Column("result_amount", sa.Integer(), nullable=False),
        sa.Column("required_workbench", sa.String(length=64), nullable=False),
        sa.Column("duration_seconds", sa.Float(), nullable=False),
        sa.Column("ingredients", sa.JSON(), nullable=False),
        sa.PrimaryKeyConstraint("recipe_id"),
    )
    op.create_index("ix_recipes_result_item_id", "recipes", ["result_item_id"])

    op.create_table(
        "smelting_recipes",
        sa.Column("smelt_id", sa.String(length=128), nullable=False),
        sa.Column("result_item_id", sa.String(length=128), nullable=False),
        sa.Column("result_amount", sa.Integer(), nullable=False),
        sa.Column("required_workbench", sa.String(length=64), nullable=False),
        sa.Column("duration_seconds", sa.Float(), nullable=False),
        sa.Column("input_item", sa.JSON(), nullable=False),
        sa.Column("fuel", sa.JSON(), nullable=False),
        sa.PrimaryKeyConstraint("smelt_id"),
    )
    op.create_index(
        "ix_smelting_recipes_result_item_id",
        "smelting_recipes",
        ["result_item_id"],
    )

    op.create_table(
        "enemies",
        sa.Column("enemy_id", sa.String(length=128), nullable=False),
        sa.Column("name_ko", sa.String(length=128), nullable=False),
        sa.Column("description", sa.String(length=2000), nullable=False),
        sa.Column("weakness", sa.JSON(), nullable=False),
        sa.PrimaryKeyConstraint("enemy_id"),
    )
    op.create_index("ix_enemies_name_ko", "enemies", ["name_ko"])

    op.create_table(
        "locations",
        sa.Column("location_id", sa.String(length=128), nullable=False),
        sa.Column("coordinates", sa.JSON(), nullable=False),
        sa.PrimaryKeyConstraint("location_id"),
    )

    items_table = sa.table(
        "items",
        sa.column("item_id", sa.String(length=128)),
        sa.column("item_type", sa.String(length=32)),
        sa.column("name_ko", sa.String(length=128)),
        sa.column("aliases", sa.JSON()),
        sa.column("description", sa.String(length=1000)),
    )
    op.bulk_insert(
        items_table,
        [
            {
                "item_id": item.item_id,
                "item_type": item.item_type,
                "name_ko": item.name_ko,
                "aliases": list(item.aliases),
                "description": item.description,
            }
            for item in DATASET.items
        ],
    )

    recipes_table = sa.table(
        "recipes",
        sa.column("recipe_id", sa.String(length=128)),
        sa.column("result_item_id", sa.String(length=128)),
        sa.column("result_amount", sa.Integer()),
        sa.column("required_workbench", sa.String(length=64)),
        sa.column("duration_seconds", sa.Float()),
        sa.column("ingredients", sa.JSON()),
    )
    op.bulk_insert(
        recipes_table,
        [
            {
                "recipe_id": recipe.recipe_id,
                "result_item_id": recipe.result_item_id,
                "result_amount": recipe.result_amount,
                "required_workbench": recipe.required_workbench,
                "duration_seconds": recipe.duration_seconds,
                "ingredients": [
                    _ingredient_value(ingredient.item_id, ingredient.amount)
                    for ingredient in recipe.ingredients
                ],
            }
            for recipe in DATASET.recipes
        ],
    )

    smelting_table = sa.table(
        "smelting_recipes",
        sa.column("smelt_id", sa.String(length=128)),
        sa.column("result_item_id", sa.String(length=128)),
        sa.column("result_amount", sa.Integer()),
        sa.column("required_workbench", sa.String(length=64)),
        sa.column("duration_seconds", sa.Float()),
        sa.column("input_item", sa.JSON()),
        sa.column("fuel", sa.JSON()),
    )
    op.bulk_insert(
        smelting_table,
        [
            {
                "smelt_id": recipe.smelt_id,
                "result_item_id": recipe.result_item_id,
                "result_amount": recipe.result_amount,
                "required_workbench": recipe.required_workbench,
                "duration_seconds": recipe.duration_seconds,
                "input_item": _ingredient_value(recipe.input.item_id, recipe.input.amount),
                "fuel": _ingredient_value(recipe.fuel.item_id, recipe.fuel.amount),
            }
            for recipe in DATASET.smelting_recipes
        ],
    )

    enemies_table = sa.table(
        "enemies",
        sa.column("enemy_id", sa.String(length=128)),
        sa.column("name_ko", sa.String(length=128)),
        sa.column("description", sa.String(length=2000)),
        sa.column("weakness", sa.JSON()),
    )
    op.bulk_insert(
        enemies_table,
        [
            {
                "enemy_id": enemy.enemy_id,
                "name_ko": enemy.name_ko,
                "description": enemy.description,
                "weakness": {
                    "weak_element": enemy.weak_element,
                    "weak_part": enemy.weak_part,
                    "ai_advice": enemy.ai_advice,
                },
            }
            for enemy in DATASET.enemies
        ],
    )

    locations_table = sa.table(
        "locations",
        sa.column("location_id", sa.String(length=128)),
        sa.column("coordinates", sa.JSON()),
    )
    op.bulk_insert(
        locations_table,
        [
            {
                "location_id": location.location_id,
                "coordinates": {
                    "X": location.coordinates[0],
                    "Y": location.coordinates[1],
                    "Z": location.coordinates[2],
                },
            }
            for location in DATASET.locations
        ],
    )


def downgrade() -> None:
    op.drop_table("locations")
    op.drop_table("enemies")
    op.drop_table("smelting_recipes")
    op.drop_table("recipes")
    op.drop_table("items")
