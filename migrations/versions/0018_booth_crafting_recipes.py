"""Seed the booth crafting recipes in databases created before recipe-14 existed.

Revision ID: 0018
Revises: 0017
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0018"
down_revision: str | None = "0017"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


_IRON_INGOT_VALUES: dict[str, object] = {
    "result_item_id": "IronIngot",
    "result_amount": 1,
    "required_workbench": "Workbench.Smelter",
    "duration_seconds": 2.0,
    "ingredients": [{"ItemId": "IronOre", "Amount": 2}],
}
_WOOD_HANDLE_VALUES: dict[str, object] = {
    "result_item_id": "WoodHandle",
    "result_amount": 1,
    "required_workbench": "Basic Workbench",
    "duration_seconds": 1.0,
    "ingredients": [{"ItemId": "PlantStem", "Amount": 2}],
}


def upgrade() -> None:
    """Align pre-booth Recipe data with the stable IDs already used by UE and Chat."""

    recipes = sa.table(
        "recipes",
        sa.column("recipe_id", sa.String(length=128)),
        sa.column("result_item_id", sa.String(length=128)),
        sa.column("result_amount", sa.Integer()),
        sa.column("required_workbench", sa.String(length=64)),
        sa.column("duration_seconds", sa.Float()),
        sa.column("ingredients", sa.JSON()),
    )
    connection = op.get_bind()

    connection.execute(
        recipes.update()
        .where(recipes.c.recipe_id == "recipe-9")
        .values(**_IRON_INGOT_VALUES)
    )
    existing_handle = connection.execute(
        sa.select(recipes.c.recipe_id).where(recipes.c.recipe_id == "recipe-14")
    ).first()
    if existing_handle is None:
        connection.execute(recipes.insert().values(recipe_id="recipe-14", **_WOOD_HANDLE_VALUES))
    else:
        connection.execute(
            recipes.update()
            .where(recipes.c.recipe_id == "recipe-14")
            .values(**_WOOD_HANDLE_VALUES)
        )


def downgrade() -> None:
    recipes = sa.table(
        "recipes",
        sa.column("recipe_id", sa.String(length=128)),
        sa.column("required_workbench", sa.String(length=64)),
    )
    connection = op.get_bind()
    connection.execute(recipes.delete().where(recipes.c.recipe_id == "recipe-14"))
    connection.execute(
        recipes.update()
        .where(recipes.c.recipe_id == "recipe-9")
        .values(required_workbench="Blacksmith Anvil/Furnace")
    )
