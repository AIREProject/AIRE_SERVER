from app.brain.command_intent import RECIPE_PATTERN
from app.brain.recipes import RecipeRepository
from app.gamedata.dataset import ITEMS, RECIPES


def _result_name(result_item_id: str) -> str:
    return next(item.name_ko for item in ITEMS if item.item_id == result_item_id)


def test_every_recipe_result_is_lookupable_in_korean() -> None:
    repository = RecipeRepository()

    for recipe in RECIPES:
        result_name = _result_name(recipe.result_item_id)
        fact = repository.fact_for(f"{result_name} 만드는 법")

        assert fact is not None, result_name
        assert fact.kind == "recipe"
        assert result_name in fact.text


def test_iron_and_steel_ingots_include_both_crafting_paths() -> None:
    repository = RecipeRepository()

    fact = repository.fact_for("철괴 만드는 법")

    assert fact is not None
    assert "대장간 화로" in fact.text
    assert "용광로" in fact.text
    assert "철광석 2개" in fact.text
    assert "석탄 1개" in fact.text


def test_result_quantity_and_duration_are_rendered_when_present() -> None:
    repository = RecipeRepository()

    nail_fact = repository.fact_for("못 레시피")
    bandage_fact = repository.fact_for("붕대 레시피")

    assert nail_fact is not None
    assert "5개" in nail_fact.text
    assert "1초" in nail_fact.text
    assert bandage_fact is not None
    assert "0초" not in bandage_fact.text


def test_multiple_result_items_do_not_choose_one_recipe() -> None:
    repository = RecipeRepository()

    assert repository.fact_for("돌도끼와 돌곡괭이 만드는 법") is None


def test_alias_matching_does_not_match_inside_another_word() -> None:
    repository = RecipeRepository()

    assert repository.fact_for("부싯불 만드는 법") is None


def test_mock_recipe_router_accepts_every_result_alias() -> None:
    for recipe in RECIPES:
        result_name = _result_name(recipe.result_item_id)
        assert RECIPE_PATTERN.search(f"{result_name} 만드는 법") is not None
