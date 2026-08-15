from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass

from app.gamedata.dataset import DATASET, GameDataSet, Item, Recipe, SmeltingRecipe

from .facts import DialogueFact
from .intent import RecipeQueryMode
from .korean import alias_pattern, has_batchim, topic

# ERD가 준 작업대 이름을 대사에서 읽기 쉬운 이름으로 옮긴다.
_WORKBENCH_NAMES = {
    "None (Handcraft)": "맨손",
    "Basic Workbench": "작업대",
    "Blacksmith Anvil/Furnace": "대장간 화로",
    "Alchemy Table": "연금술 탁자",
    "Workbench.BlastFurnace": "용광로",
}

# AX-I06의 첫 제작 수직 슬라이스는 이 안정 Recipe 하나만 광고한다. 표시 이름이나
# UObject 경로를 Command parameter로 흘리지 않고, UE가 로컬 Recipe로 다시 매핑할 ID만
# 반환한다.
_CRAFT_RECIPE_ID = "recipe-11"
_CRAFT_RESULT_ITEM_IDS = frozenset(("Sword_Iron", "IronSword"))
_CRAFT_RESULT_ALIASES = ("철검", "철 검", "쇠검", "Sword_Iron", "IronSword")
_CRAFT_VERB_PATTERN = re.compile(r"(?:만들|제작|craft|forge)", re.IGNORECASE)
_CRAFT_FACT_PATTERN = re.compile(
    r"(?:만드는?\s*(?:법|방법)|만들\s*기|제작\s*(?:법|방법|하기)|레시피|재료|"
    r"어떻게|알려|가능|할\s*수|만들\s*(?:면|까|지)|제작\s*까|몇|많이|잔뜩|전부|"
    r"\bcrafting\b)",
    re.IGNORECASE,
)
_CRAFT_QUANTITY_PATTERN = re.compile(
    r"(?<![\d.,-])(\d[\d,]*)\s*(?:개|자루)", re.IGNORECASE
)
_CRAFT_MALFORMED_QUANTITY_PATTERN = re.compile(
    r"(?<!\w)(?:[+-]?\d[\d.,/]*|한|하나|두|세|네|다섯|여섯|일곱|여덟|아홉|열)"
    r"\s*(?:개|자루)",
    re.IGNORECASE,
)
_CRAFT_WORD_QUANTITY_PATTERN = re.compile(
    r"(?<!\w)(?:한|하나|두|세|네|다섯|여섯|일곱|여덟|아홉|열)\s*(?:개|자루)",
    re.IGNORECASE,
)
_CRAFT_QUANTITY_ASSIGNMENT_PATTERN = re.compile(
    r"(?:quantity|qty|수량)\s*[:=]\s*([+-]?\d+(?:[.,]\d+)?)", re.IGNORECASE
)
_CRAFT_RECIPE_TOKEN_PATTERN = re.compile(r"(?<![\w-])recipe-\d+(?![\w-])", re.IGNORECASE)
_CRAFT_BARE_NUMBER_PATTERN = re.compile(r"(?<![\w=:+-])\d+(?![\w-])")
_STABLE_RECIPE_ID_PATTERN = re.compile(
    r"(?<![\w-])(?:recipe|smelt)-[A-Za-z0-9_-]+(?![\w-])", re.IGNORECASE
)
_RECIPE_REFERENCE_PATTERN = re.compile(r"(?:그거|그것|그\s*레시피|그\s*제작법)")
_RECIPE_LIST_PATTERN = re.compile(
    r"(?:알고\s*있는|아는|확인된|가능한|보유한).*(?:레시피|제작법)|"
    r"(?:레시피|제작법).*(?:목록|리스트|뭐|무엇|어떤|있어)",
    re.IGNORECASE,
)
_RECIPE_COMPARE_PATTERN = re.compile(r"(?:비교|차이|어느\s*게|어떤\s*게|더\s*(?:좋|나))")
_RECIPE_QUERY_PATTERN = re.compile(
    r"(?:레시피|제작법|만드는?\s*(?:법|방법)|어떻게\s*(?:만들|제작)|재료)",
    re.IGNORECASE,
)
_EXPLICIT_UNKNOWN_PATTERN = re.compile(
    r"(?:[A-Za-z0-9가-힣_.:-]+)\s*(?:레시피|제작법|만드는?\s*(?:법|방법))|"
    r"(?:[A-Za-z0-9가-힣_.:-]+)(?:을|를|은|는)?\s*어떻게\s*(?:만들|제작)",
    re.IGNORECASE,
)


@dataclass(frozen=True, slots=True)
class RecipeTarget:
    """표시 이름이 아니라 게임 데이터의 stable ID로 검증된 제작 결과 대상."""

    result_item_id: str
    recipe_ids: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class RecipeQuery:
    """한 Recipe 질문의 mode와 검증된 대상 집합."""

    mode: RecipeQueryMode
    targets: tuple[RecipeTarget, ...] = ()


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
        self._craft_recipe_available = any(
            recipe.recipe_id == _CRAFT_RECIPE_ID
            and recipe.result_item_id in _CRAFT_RESULT_ITEM_IDS
            for recipe in dataset.recipes
        )
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
        self._targets_by_result: dict[str, RecipeTarget] = {
            item_id: RecipeTarget(
                result_item_id=item_id,
                recipe_ids=(
                    *(recipe.recipe_id for recipe in self._recipes_by_result[item_id]),
                    *(recipe.smelt_id for recipe in self._smelting_by_result[item_id]),
                ),
            )
            for item_id in result_items
        }
        self._targets_by_recipe_id: dict[str, RecipeTarget] = {
            recipe_id.casefold(): target
            for target in self._targets_by_result.values()
            for recipe_id in target.recipe_ids
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

        targets = self.targets_in(query)
        if len(targets) != 1:
            return None

        return self.fact_for_target(targets[0])

    def fact_for_target(self, target: RecipeTarget) -> DialogueFact | None:
        """검증된 target의 기존 상세 사실을 반환한다."""

        canonical = self._targets_by_result.get(target.result_item_id)
        if canonical != target:
            return None
        result_id = target.result_item_id
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

    def targets_in(self, query: str) -> tuple[RecipeTarget, ...]:
        """별칭 또는 stable Recipe ID가 가리키는 검증된 결과 대상을 반환한다."""

        result_ids = {
            item_id
            for item_id, pattern in self._recipe_patterns
            if pattern.search(query) is not None
        }
        for recipe_id in _STABLE_RECIPE_ID_PATTERN.findall(query):
            target = self._targets_by_recipe_id.get(recipe_id.casefold())
            if target is not None:
                result_ids.add(target.result_item_id)
        return tuple(self._targets_by_result[item_id] for item_id in sorted(result_ids))

    def query_for(
        self,
        query: str,
        *,
        recent_target: RecipeTarget | None = None,
    ) -> RecipeQuery | None:
        """현재 문장과 직전의 검증된 단일 대상을 Recipe query로 판정한다."""

        if self.is_craft_request(query):
            return None
        targets = self.targets_in(query)
        stable_ids = {
            recipe_id.casefold() for recipe_id in _STABLE_RECIPE_ID_PATTERN.findall(query)
        }
        has_recipe_signal = bool(
            targets
            or stable_ids
            or _RECIPE_QUERY_PATTERN.search(query)
            or _RECIPE_LIST_PATTERN.search(query)
            or _RECIPE_REFERENCE_PATTERN.search(query)
        )
        if not has_recipe_signal:
            return None

        unknown_stable_ids = stable_ids - self._targets_by_recipe_id.keys()
        if unknown_stable_ids:
            return RecipeQuery(RecipeQueryMode.UNKNOWN_RECIPE)

        if _RECIPE_LIST_PATTERN.search(query) is not None and not targets:
            return RecipeQuery(RecipeQueryMode.LIST_KNOWN)

        if _RECIPE_REFERENCE_PATTERN.search(query) is not None and not targets:
            if recent_target is not None and self.fact_for_target(recent_target) is not None:
                return RecipeQuery(RecipeQueryMode.DETAIL, (recent_target,))
            return RecipeQuery(RecipeQueryMode.AMBIGUOUS)

        if _RECIPE_COMPARE_PATTERN.search(query) is not None:
            if len(targets) == 2:
                return RecipeQuery(RecipeQueryMode.COMPARE, targets)
            return RecipeQuery(RecipeQueryMode.AMBIGUOUS, targets)

        if len(targets) == 1:
            return RecipeQuery(RecipeQueryMode.DETAIL, targets)
        if len(targets) > 1:
            return RecipeQuery(RecipeQueryMode.AMBIGUOUS, targets)
        if _EXPLICIT_UNKNOWN_PATTERN.search(query) is not None:
            return RecipeQuery(RecipeQueryMode.UNKNOWN_RECIPE)
        return RecipeQuery(RecipeQueryMode.AMBIGUOUS)

    def result_aliases(self) -> tuple[str, ...]:
        """Mock 라우터가 제작법 의도를 찾을 때 사용할 결과물 별칭을 반환한다."""

        return self._all_result_aliases

    def craft_recipe_id_for(self, query: str) -> str | None:
        """명시적인 철검 제작 요청만 AX-I06 Recipe ID로 해석한다.

        제작법·재료 질문은 ``fact_for``가 담당하는 facts-only 경로에 남긴다. 이 메서드는
        요청형 동사와 allowlist된 결과물 이름, 수량 1을 모두 확인하고, 어느 하나라도
        불명확하면 ``None``을 반환한다. 따라서 Context나 LLM의 자유 텍스트만으로는
        `CraftItem` 후보가 생기지 않는다.
        """

        if not self._craft_recipe_available:
            return None
        if not self.is_craft_request(query):
            return None

        quantities = [
            int(value.replace(",", "")) for value in _CRAFT_QUANTITY_PATTERN.findall(query)
        ]
        if len(quantities) > 1 or (quantities and quantities[0] != 1):
            return None
        assigned_quantities = _CRAFT_QUANTITY_ASSIGNMENT_PATTERN.findall(query)
        if assigned_quantities:
            if len(assigned_quantities) > 1:
                return None
            try:
                assigned_quantity = int(assigned_quantities[0])
            except ValueError:
                return None
            if assigned_quantity != 1:
                return None
        recipe_tokens = {
            token.casefold() for token in _CRAFT_RECIPE_TOKEN_PATTERN.findall(query)
        }
        if recipe_tokens and recipe_tokens != {_CRAFT_RECIPE_ID}:
            return None
        if _CRAFT_BARE_NUMBER_PATTERN.search(query) is not None:
            return None
        # 소수·범위·한글 수량은 첫 슬라이스의 정수 1 계약으로 안전하게 거절한다.
        if _CRAFT_MALFORMED_QUANTITY_PATTERN.search(query) is not None and not quantities:
            return None
        if _CRAFT_WORD_QUANTITY_PATTERN.search(query) is not None:
            return None

        matched_result_ids = self._craft_result_ids_in(query)
        if matched_result_ids != {"Sword_Iron"}:
            return None
        return _CRAFT_RECIPE_ID

    def is_craft_request(self, query: str) -> bool:
        """제작 의도처럼 보이는 발화를 분리해 malformed 요청도 안전하게 거절한다."""

        return (
            _CRAFT_VERB_PATTERN.search(query) is not None
            and _CRAFT_FACT_PATTERN.search(query) is None
            and bool(self._craft_result_ids_in(query))
        )

    def _craft_result_ids_in(self, query: str) -> set[str]:
        matched_result_ids = {
            "Sword_Iron" if item_id in _CRAFT_RESULT_ITEM_IDS else item_id
            for item_id, pattern in self._recipe_patterns
            if pattern.search(query) is not None
        }
        if any(alias_pattern(alias).search(query) is not None for alias in _CRAFT_RESULT_ALIASES):
            matched_result_ids.add("Sword_Iron")
        return matched_result_ids

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
