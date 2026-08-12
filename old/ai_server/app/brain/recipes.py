from __future__ import annotations

import re
from collections.abc import Iterable

from app.gamedata.dataset import DATASET, GameDataSet, Item, Recipe, SmeltingRecipe

from .facts import DialogueFact
from .korean import alias_pattern, has_batchim, topic

# ERD가 준 작업대 이름을 대사에서 읽기 쉬운 이름으로 옮긴다.
_WORKBENCH_NAMES = {
    "None (Handcraft)": "맨손",
    "Basic Workbench": "작업대",
    "Blacksmith Anvil/Furnace": "대장간 화로",
    "Alchemy Table": "연금술 탁자",
    "Workbench.BlastFurnace": "용광로",
}


def _join_ingredients(ingredients: Iterable[str]) -> str:
    parts = list(ingredients)
    if len(parts) < 2:
        return parts[0] if parts else "재료"
    joined = parts[0]
    for part in parts[1:]:
        joined = f"{joined}{'과' if has_batchim(joined[-1]) else '와'} {part}"
    return joined


def _amount_text(item: Item | None, item_id: str, amount: int) -> str:
    name = item.name_ko if item is not None else {"Wood": "나무"}.get(item_id, item_id)
    return f"{name} {amount}개"


def _duration_text(seconds: float) -> str:
    if seconds.is_integer():
        return f"{int(seconds)}초"
    return f"{seconds:g}초"


class RecipeRepository:
    """게임 데이터셋에서 검증된 제작법과 제련법을 제공한다.

    기본값은 정적 `DATASET`(`app/gamedata/dataset.py`)이지만, `app/main.py` 가 시작 시점에
    DB 를 읽어 만든 `GameDataSet` 을 대신 넘길 수도 있다(`app/service.py`). 인덱스는 여기
    `__init__` 에서 한 번만 만들어진다 — 이전엔 모듈 임포트 시점의 전역이었다.
    """

    def __init__(self, dataset: GameDataSet = DATASET) -> None:
        self._items: dict[str, Item] = {item.item_id: item for item in dataset.items}
        result_items: dict[str, Item] = {
            item.item_id: item
            for item in dataset.items
            if any(recipe.result_item_id == item.item_id for recipe in dataset.recipes)
            or any(recipe.result_item_id == item.item_id for recipe in dataset.smelting_recipes)
        }
        self._result_items = result_items
        self._recipes_by_result: dict[str, tuple[Recipe, ...]] = {
            item_id: tuple(
                recipe for recipe in dataset.recipes if recipe.result_item_id == item_id
            )
            for item_id in result_items
        }
        self._smelting_by_result: dict[str, tuple[SmeltingRecipe, ...]] = {
            item_id: tuple(
                recipe for recipe in dataset.smelting_recipes if recipe.result_item_id == item_id
            )
            for item_id in result_items
        }
        aliases_by_result: dict[str, tuple[str, ...]] = {
            item_id: tuple(dict.fromkeys((*item.aliases, item.name_ko, item.item_id)))
            for item_id, item in result_items.items()
        }
        self._recipe_patterns: tuple[tuple[str, re.Pattern[str]], ...] = tuple(
            (item_id, pattern)
            for item_id, aliases in aliases_by_result.items()
            for pattern in (alias_pattern(alias) for alias in aliases)
        )
        self._all_result_aliases: tuple[str, ...] = tuple(
            alias for aliases in aliases_by_result.values() for alias in aliases
        )

    def fact_for(self, query: str) -> DialogueFact | None:
        """현재 발화가 가리키는 결과물의 제작·제련법을 반환한다."""

        result_ids = {
            item_id
            for item_id, pattern in self._recipe_patterns
            if pattern.search(query) is not None
        }
        if len(result_ids) != 1:
            return None

        result_id = next(iter(result_ids))
        descriptions = [
            self._describe_recipe(recipe)
            for recipe in self._recipes_by_result.get(result_id, ())
        ]
        descriptions.extend(
            self._describe_smelting(recipe)
            for recipe in self._smelting_by_result.get(result_id, ())
        )
        if not descriptions:
            return None
        return DialogueFact(kind="recipe", text=" ".join(descriptions))

    def result_aliases(self) -> tuple[str, ...]:
        """Mock 라우터가 제작법 의도를 찾을 때 사용할 결과물 별칭을 반환한다."""

        return self._all_result_aliases

    def _describe_recipe(self, recipe: Recipe) -> str:
        result = self._result_items[recipe.result_item_id]
        ingredients = _join_ingredients(
            _amount_text(self._items.get(item.item_id), item.item_id, item.amount)
            for item in recipe.ingredients
        )
        station = _WORKBENCH_NAMES[recipe.required_workbench]
        result_amount = f" {recipe.result_amount}개" if recipe.result_amount != 1 else ""
        duration = (
            f" {_duration_text(recipe.duration_seconds)} 만에"
            if recipe.duration_seconds > 0
            else ""
        )
        result_label = f"{result.name_ko}{result_amount}"
        return (
            f"{topic(result_label)} {ingredients}로 "
            f"{station}에서{duration} 만들 수 있어."
        )

    def _describe_smelting(self, recipe: SmeltingRecipe) -> str:
        result = self._result_items[recipe.result_item_id]
        input_text = _amount_text(
            self._items.get(recipe.input.item_id), recipe.input.item_id, recipe.input.amount
        )
        fuel_text = _amount_text(
            self._items.get(recipe.fuel.item_id), recipe.fuel.item_id, recipe.fuel.amount
        )
        result_amount = f" {recipe.result_amount}개" if recipe.result_amount != 1 else ""
        result_label = f"{result.name_ko}{result_amount}"
        duration = _duration_text(recipe.duration_seconds)
        return (
            f"{topic(result_label)} {_WORKBENCH_NAMES[recipe.required_workbench]}에서 "
            f"{input_text}와 {fuel_text}를 써서 {duration} 만에 만들 수 있어."
        )
