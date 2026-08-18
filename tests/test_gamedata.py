from app.gamedata.dataset import (
    DATASET,
    ENEMIES,
    ITEMS,
    RECIPES,
    SMELTING_RECIPES,
)


def test_dataset_has_expected_erd_row_counts() -> None:
    assert DATASET.items is ITEMS
    assert DATASET.recipes is RECIPES
    assert len(ITEMS) == 26
    assert len(RECIPES) == 13
    assert len(SMELTING_RECIPES) == 4
    assert len(ENEMIES) == 3
    assert DATASET.locations == ()


def test_recipe_and_smelting_item_references_are_known() -> None:
    item_ids = {item.item_id for item in ITEMS}

    for recipe in RECIPES:
        assert recipe.result_item_id in item_ids
        assert all(ingredient.item_id in item_ids for ingredient in recipe.ingredients)

    for recipe in SMELTING_RECIPES:
        assert recipe.result_item_id in item_ids
        assert recipe.input.item_id in item_ids
        # ERD 제련 3·4번은 아이템 표에 없는 Wood를 연료로 지정한다.
        if recipe.fuel.item_id != "Wood":
            assert recipe.fuel.item_id in item_ids


def test_recipe_result_aliases_are_unique() -> None:
    result_ids = {recipe.result_item_id for recipe in RECIPES} | {
        recipe.result_item_id for recipe in SMELTING_RECIPES
    }
    result_items = [item for item in ITEMS if item.item_id in result_ids]
    aliases = [
        alias
        for item in result_items
        for alias in dict.fromkeys((*item.aliases, item.name_ko, item.item_id))
    ]

    assert len(aliases) == len(set(aliases))


def test_enemy_aliases_are_unique_and_do_not_overlap_items() -> None:
    enemy_aliases = [
        alias
        for enemy in ENEMIES
        for alias in dict.fromkeys((*enemy.aliases, enemy.name_ko, enemy.enemy_id))
    ]
    item_aliases = {
        alias
        for item in ITEMS
        for alias in dict.fromkeys((*item.aliases, item.name_ko, item.item_id))
    }

    assert len(enemy_aliases) == len(set(enemy_aliases))
    assert not set(enemy_aliases) & item_aliases


def test_all_workbench_values_have_display_names() -> None:
    expected = {
        "None (Handcraft)",
        "Basic Workbench",
        "Blacksmith Anvil/Furnace",
        "Alchemy Table",
        "Workbench.BlastFurnace",
    }
    actual = {recipe.required_workbench for recipe in RECIPES} | {
        recipe.required_workbench for recipe in SMELTING_RECIPES
    }

    assert actual == expected
