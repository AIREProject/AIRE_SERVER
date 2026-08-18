from app.brain.command_intent import RECIPE_PATTERN
from app.brain.intent import RecipeQueryMode
from app.brain.recipes import RecipeQuery, RecipeRepository, RecipeTarget
from app.gamedata.dataset import ITEMS, RECIPES, SMELTING_RECIPES


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


def test_recipe_list_questions_accept_attached_and_spaced_wording() -> None:
    repository = RecipeRepository()

    for query in ("레시피알아?", "레시피 알아?", "아는 제작법 말해"):
        parsed = repository.query_for(query)

        assert parsed is not None
        assert parsed.mode is RecipeQueryMode.LIST_KNOWN
        assert parsed.targets == ()


def test_recipe_detail_accepts_attached_recipe_suffix() -> None:
    repository = RecipeRepository()

    for query in ("돌도끼레시피 알려줘", "돌도끼 레시피 알려줘", "돌도끼레시피를 알려줘"):
        parsed = repository.query_for(query)

        assert parsed is not None
        assert parsed.mode is RecipeQueryMode.DETAIL
        assert parsed.targets[0].recipe_ids == ("recipe-3",)


def test_attached_suffix_does_not_turn_unknown_substrings_into_targets() -> None:
    repository = RecipeRepository()

    for query in (
        "부싯불레시피 알려줘",
        "부싯돌레시피 알려줘",
        "부싯불 레시피 알아?",
        "전설검 레시피 알아?",
    ):
        parsed = repository.query_for(query)

        assert parsed is not None
        assert parsed.mode is RecipeQueryMode.UNKNOWN_RECIPE
        assert parsed.targets == ()


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


def test_list_known_returns_every_unique_crafting_and_smelting_result_once() -> None:
    repository = RecipeRepository()
    query = repository.query_for("레시피 알고 있는 거 있어?")

    assert query is not None
    result = repository.result_for_query(query)

    assert result is not None
    expected_by_result: dict[str, list[str]] = {}
    for recipe in RECIPES:
        expected_by_result.setdefault(recipe.result_item_id, []).append(recipe.recipe_id)
    for recipe in SMELTING_RECIPES:
        expected_by_result.setdefault(recipe.result_item_id, []).append(recipe.smelt_id)
    names_by_id = {item.item_id: item.name_ko for item in ITEMS}
    listed_names = result.text.removeprefix("확인된 제작법은 ").removesuffix("이야.").split(", ")

    assert listed_names == [names_by_id[result_id] for result_id in sorted(expected_by_result)]
    assert result.fact_ids == tuple(
        f"recipe:{recipe_id}"
        for result_id in sorted(expected_by_result)
        for recipe_id in expected_by_result[result_id]
    )


def test_compare_returns_only_two_canonical_target_facts() -> None:
    repository = RecipeRepository()
    query = repository.query_for("돌도끼와 돌곡괭이 레시피를 비교해 줘")

    assert query is not None
    result = repository.result_for_query(query)

    assert result is not None
    assert result.text.startswith("비교하면, ")
    assert "돌도끼" in result.text
    assert "돌곡괭이" in result.text
    assert result.fact_ids == ("recipe:recipe-3", "recipe:recipe-4")

    assert repository.result_for_query(
        RecipeQuery(RecipeQueryMode.COMPARE, query.targets[:1])
    ) is None
    assert repository.result_for_query(
        RecipeQuery(RecipeQueryMode.COMPARE, (*query.targets, query.targets[0]))
    ) is None
    assert repository.result_for_query(
        RecipeQuery(
            RecipeQueryMode.COMPARE,
            (query.targets[0], RecipeTarget("Pickaxe_Stone", ("recipe-999",))),
        )
    ) is None
