from app.brain.command_intent import RECIPE_PATTERN
from app.brain.intent import RecipeQueryMode
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


def test_recipe_query_modes_use_validated_targets() -> None:
    repository = RecipeRepository()

    listed = repository.query_for("레시피 알고 있는 거 있어?")
    detailed = repository.query_for("recipe-3 레시피 알려줘")
    compared = repository.query_for("돌도끼와 돌곡괭이 레시피를 비교해 줘")

    assert listed is not None and listed.mode is RecipeQueryMode.LIST_KNOWN
    assert listed.targets == ()
    assert detailed is not None and detailed.mode is RecipeQueryMode.DETAIL
    assert detailed.targets[0].recipe_ids == ("recipe-3",)
    assert compared is not None and compared.mode is RecipeQueryMode.COMPARE
    assert {target.recipe_ids for target in compared.targets} == {
        ("recipe-3",),
        ("recipe-4",),
    }


def test_recipe_compare_requires_exactly_two_validated_targets() -> None:
    repository = RecipeRepository()

    one = repository.query_for("돌도끼 레시피와 비교해 줘")
    three = repository.query_for("돌도끼, 돌곡괭이, 철검 레시피를 비교해 줘")

    assert one is not None and one.mode is RecipeQueryMode.AMBIGUOUS
    assert three is not None and three.mode is RecipeQueryMode.AMBIGUOUS


def test_recipe_unknown_name_and_id_are_not_guessed() -> None:
    repository = RecipeRepository()

    unknown_name = repository.query_for("전설검 레시피 알려줘")
    unknown_id = repository.query_for("recipe-999 레시피 알려줘")

    assert unknown_name is not None
    assert unknown_name.mode is RecipeQueryMode.UNKNOWN_RECIPE
    assert unknown_name.targets == ()
    assert unknown_id is not None
    assert unknown_id.mode is RecipeQueryMode.UNKNOWN_RECIPE
    assert unknown_id.targets == ()


def test_recipe_follow_up_uses_only_a_validated_target() -> None:
    repository = RecipeRepository()
    detail = repository.query_for("돌도끼 레시피 알려줘")
    assert detail is not None and len(detail.targets) == 1

    resolved = repository.query_for("그거 어떻게 만들어?", recent_target=detail.targets[0])
    unresolved = repository.query_for("그거 어떻게 만들어?")

    assert resolved is not None and resolved.mode is RecipeQueryMode.DETAIL
    assert resolved.targets == detail.targets
    assert unresolved is not None and unresolved.mode is RecipeQueryMode.AMBIGUOUS


def test_explicit_craft_request_is_not_a_recipe_query() -> None:
    repository = RecipeRepository()

    assert repository.is_craft_request("철검 하나 만들어") is True
    assert repository.query_for("철검 하나 만들어") is None
