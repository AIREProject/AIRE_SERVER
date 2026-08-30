from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

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
    "Workbench.Smelter": "제련소",
}

# Game surface에 광고하는 안정 Recipe ID만 유지한다. 표시 이름이나 UObject
# 경로를 Command parameter로 흘리지 않고 UE가 로컬 Recipe로 다시 매핑할 ID만 반환한다.
_GAME_CRAFT_RECIPE_BY_RESULT = {
    "ShoddyBandage": "recipe-1",
    "IronIngot": "recipe-9",
    "Sword_Iron": "recipe-11",
    "WoodHandle": "recipe-14",
}
_MOBILE_CRAFT_RECIPE_ID = "recipe-1"
_CRAFT_RESULT_ITEM_IDS = frozenset(("Sword_Iron", "IronSword"))
_CRAFT_RESULT_ALIASES = ("철검", "철 검", "쇠검", "Sword_Iron", "IronSword")
_CRAFT_VERB_PATTERN = re.compile(r"(?:만들|제작|제련|craft|forge|smelt)", re.IGNORECASE)
_CRAFT_NEGATION_PATTERN = re.compile(
    r"(?:만들|제작|제련).{0,8}(?:말아|마|하지\s*마|필요\s*없)|"
    r"(?:안|못)\s*(?:만들|제작|제련)",
    re.IGNORECASE,
)
_CRAFT_FACT_PATTERN = re.compile(
    r"(?:만드는?\s*(?:법|방법)|만들\s*기|제작\s*(?:법|방법|하기)|레시피|재료|"
    r"어떻게|알려|가능|할\s*수|만들\s*(?:면|까|지)|제작\s*까|몇|많이|잔뜩|전부|"
    r"\bcrafting\b)",
    re.IGNORECASE,
)
_CRAFT_QUANTITY_PATTERN = re.compile(r"(?<![\d.,-])(\d[\d,]*)\s*(?:개|자루)", re.IGNORECASE)
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
    r"(?:레시피|제작법).*(?:목록|리스트|뭐|무엇|어떤|있어)|"
    r"^\s*(?:레시피|제작법)\s*알아",
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

# ``alias_pattern`` intentionally requires an end-of-word boundary. Recipe questions
# commonly attach the query suffix directly to a Korean result name (``돌도끼레시피``),
# so keep the normal boundary while allowing only an explicit recipe suffix to follow.
_RECIPE_PARTICLE_PATTERN = r"(?:을|를|은|는|이|가|의|도|만|와|과|랑|이랑|하고)"
_RECIPE_SUFFIX_PATTERN = r"(?:레시피|제작법|만드는?\s*(?:법|방법))"
_ALIAS_ATTACHED_CONTINUATION_PATTERN = (
    r"(?:\d[\d,]*\s*(?:개|자루)|"
    r"(?:이|가|은|는)?\s*(?:뭐야|뭔데|무엇|뜻|줄임말|만들|제작|레시피|제작법))"
)
_ITEM_INFO_PATTERN = re.compile(
    r"(?:뭐야|뭔데|무엇(?:이야|인가요)?|무슨\s*(?:뜻|아이템)|뜻이야|줄임말)",
    re.IGNORECASE,
)


def _recipe_alias_pattern(alias: str) -> re.Pattern[str]:
    # Korean item names are commonly typed with or without the display-name spaces
    # (``엉성한 붕대`` / ``엉성한붕대``). Only spaces already present in a verified
    # alias become optional; arbitrary substrings are still protected by the normal
    # word boundary and Recipe suffix rules below.
    escaped = re.escape(alias).replace(r"\ ", r"\s*")
    return re.compile(
        rf"(?<!\w){escaped}(?:"
        rf"{_RECIPE_PARTICLE_PATTERN}?(?!\w)|"
        rf"(?={_RECIPE_SUFFIX_PATTERN}{_RECIPE_PARTICLE_PATTERN}?(?!\w))|"
        rf"(?={_ALIAS_ATTACHED_CONTINUATION_PATTERN})"
        rf")",
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


@dataclass(frozen=True, slots=True)
class RecipeFactResult:
    """Repository가 검증한 Recipe 응답과 그 응답에 사용한 stable source ID."""

    text: str
    fact_ids: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class RecipeSelectionOption:
    """LLM이 선택할 수 있는 검증된 결과물과 대표 stable Recipe ID."""

    recipe_id: str
    result_name: str
    aliases: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class CraftRequest:
    """사용자 원문에서 검증된 stable Recipe와 제작 수량."""

    recipe_id: str
    quantity: int


class RecipeSelection(BaseModel):
    """자연어 해석 결과. Recipe 내용은 없고 allowlist ID와 확신도만 담는다."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    decision: Literal["match", "ambiguous", "no_match"]
    candidate_recipe_ids: tuple[str, ...] = Field(max_length=3)
    confidence: int = Field(ge=0, le=100)

    @model_validator(mode="after")
    def validate_candidate_count(self) -> RecipeSelection:
        count = len(self.candidate_recipe_ids)
        if self.decision == "match" and count != 1:
            raise ValueError("match requires exactly one candidate Recipe ID")
        if self.decision == "ambiguous" and not 2 <= count <= 3:
            raise ValueError("ambiguous requires two or three candidate Recipe IDs")
        if self.decision == "no_match" and count != 0:
            raise ValueError("no_match must not include a candidate Recipe ID")
        return self


NO_RECIPE_SELECTION = RecipeSelection(decision="no_match", candidate_recipe_ids=(), confidence=0)

_RECIPE_MATCH_CONFIDENCE = 80
_RECIPE_AMBIGUOUS_CONFIDENCE = 60


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
        self._available_game_craft_recipes = {
            recipe.result_item_id: recipe.recipe_id
            for recipe in dataset.recipes
            if _GAME_CRAFT_RECIPE_BY_RESULT.get(recipe.result_item_id) == recipe.recipe_id
        }
        result_items: dict[str, Item] = {
            item.item_id: item
            for item in dataset.items
            if any(recipe.result_item_id == item.item_id for recipe in dataset.recipes)
            or any(recipe.result_item_id == item.item_id for recipe in dataset.smelting_recipes)
        }
        self._result_items = result_items
        aliases_by_result: dict[str, tuple[str, ...]] = {
            item_id: tuple(dict.fromkeys((*item.aliases, item.name_ko, item.item_id)))
            for item_id, item in result_items.items()
        }
        self._recipes_by_result: dict[str, tuple[Recipe, ...]] = {
            item_id: tuple(recipe for recipe in dataset.recipes if recipe.result_item_id == item_id)
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
        self._recipe_patterns: tuple[tuple[str, re.Pattern[str]], ...] = tuple(
            (item_id, pattern)
            for item_id, aliases in aliases_by_result.items()
            for pattern in (_recipe_alias_pattern(alias) for alias in aliases)
        )
        self._all_result_aliases: tuple[str, ...] = tuple(
            alias for aliases in aliases_by_result.values() for alias in aliases
        )
        self._selection_options: tuple[RecipeSelectionOption, ...] = tuple(
            RecipeSelectionOption(
                recipe_id=target.recipe_ids[0],
                result_name=result_items[item_id].name_ko,
                aliases=aliases_by_result[item_id],
            )
            for item_id, target in sorted(self._targets_by_result.items())
            if target.recipe_ids
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
            self._describe_recipe(recipe) for recipe in self._recipes_by_result.get(result_id, ())
        ]
        descriptions.extend(
            self._describe_smelting(recipe)
            for recipe in self._smelting_by_result.get(result_id, ())
        )
        if not descriptions:
            return None
        return DialogueFact(kind="recipe", text=" ".join(descriptions))

    def result_for_query(self, query: RecipeQuery) -> RecipeFactResult | None:
        """검증된 query mode를 목록·상세·비교 Repository 응답으로 변환한다."""

        if query.mode is RecipeQueryMode.LIST_KNOWN:
            if query.targets:
                return None
            targets = tuple(
                self._targets_by_result[result_id] for result_id in sorted(self._targets_by_result)
            )
            names = tuple(self._result_items[target.result_item_id].name_ko for target in targets)
            if not names:
                return None
            return RecipeFactResult(
                text=f"확인된 제작법은 {', '.join(names)}이야.",
                fact_ids=self._fact_ids_for(targets),
            )

        if query.mode is RecipeQueryMode.DETAIL and len(query.targets) == 1:
            fact = self.fact_for_target(query.targets[0])
            if fact is None:
                return None
            return RecipeFactResult(
                text=fact.text,
                fact_ids=self._fact_ids_for(query.targets),
            )

        if query.mode is RecipeQueryMode.ITEM_INFO and len(query.targets) == 1:
            target = query.targets[0]
            item = self._result_items.get(target.result_item_id)
            if item is None:
                return None
            item_object = f"{item.name_ko}{'을' if has_batchim(item.name_ko) else '를'}"
            return RecipeFactResult(
                text=f"그건 {item_object} 뜻해. 확인된 제작 아이템이야.",
                fact_ids=self._fact_ids_for(query.targets),
            )

        if query.mode is RecipeQueryMode.COMPARE and len(query.targets) == 2:
            if query.targets[0] == query.targets[1]:
                return None
            facts = tuple(self.fact_for_target(target) for target in query.targets)
            if any(fact is None for fact in facts):
                return None
            return RecipeFactResult(
                text="비교하면, " + " ".join(fact.text for fact in facts if fact is not None),
                fact_ids=self._fact_ids_for(query.targets),
            )

        return None

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

        if _ITEM_INFO_PATTERN.search(query) is not None:
            if len(targets) == 1:
                return RecipeQuery(RecipeQueryMode.ITEM_INFO, targets)
            if len(targets) > 1:
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

    def selection_options(self) -> tuple[RecipeSelectionOption, ...]:
        """Provider에 노출할 수 있는 검증된 후보 목록을 반환한다."""

        return self._selection_options

    def should_resolve_natural_language(self, query: str, parsed: RecipeQuery | None) -> bool:
        """정확 매칭 뒤 LLM 후보 선택을 시도해도 되는 상세 질문인지 판정한다."""

        if parsed is not None and parsed.mode not in {
            RecipeQueryMode.AMBIGUOUS,
            RecipeQueryMode.UNKNOWN_RECIPE,
        }:
            return False
        # 목록·비교·직전 참조는 각각 별도 구조가 있다. 후보 선택으로 상세 질문 하나로
        # 바꾸면 사용자의 요청 의미가 달라지므로 자연어 fallback을 사용하지 않는다.
        return not any(
            pattern.search(query) is not None
            for pattern in (
                _RECIPE_LIST_PATTERN,
                _RECIPE_COMPARE_PATTERN,
                _RECIPE_REFERENCE_PATTERN,
            )
        )

    def query_from_selection(self, selection: RecipeSelection) -> RecipeQuery | None:
        """LLM 선택을 allowlist와 확신도에 대조해 검증된 query로 승격한다."""

        targets: list[RecipeTarget] = []
        for recipe_id in selection.candidate_recipe_ids:
            target = self._targets_by_recipe_id.get(recipe_id.casefold())
            if target is None or target in targets:
                return None
            targets.append(target)

        if (
            selection.decision == "match"
            and selection.confidence >= _RECIPE_MATCH_CONFIDENCE
            and len(targets) == 1
        ):
            return RecipeQuery(RecipeQueryMode.DETAIL, tuple(targets))
        if (
            selection.decision == "ambiguous"
            and selection.confidence >= _RECIPE_AMBIGUOUS_CONFIDENCE
            and 2 <= len(targets) <= 3
        ):
            return RecipeQuery(RecipeQueryMode.AMBIGUOUS, tuple(targets))
        return None

    def clarification_for(self, query: RecipeQuery) -> str:
        """검증된 복수 후보가 있으면 표시 이름만 사용해 확인 질문을 만든다."""

        names = tuple(
            self._result_items[target.result_item_id].name_ko
            for target in query.targets
            if target.result_item_id in self._result_items
        )
        if len(names) >= 2:
            return f"{', '.join(names)} 중 어떤 제작법을 말하는지 알려 줘."
        return "어떤 제작법을 말하는지 대상 하나만 알려 줘."

    def craft_recipe_id_for(self, query: str) -> str | None:
        """명시적인 단일 제작 요청만 Game Recipe ID로 해석한다.

        제작법·재료 질문은 ``fact_for``가 담당하는 facts-only 경로에 남긴다. 이 메서드는
        요청형 동사와 allowlist된 결과물 이름, 수량 1을 모두 확인하고, 어느 하나라도
        불명확하면 ``None``을 반환한다. 따라서 Context나 LLM의 자유 텍스트만으로는
        `CraftItem` 후보가 생기지 않는다.
        """

        if not self._available_game_craft_recipes:
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
        recipe_tokens = {token.casefold() for token in _CRAFT_RECIPE_TOKEN_PATTERN.findall(query)}
        if len(recipe_tokens) > 1:
            return None
        if _CRAFT_BARE_NUMBER_PATTERN.search(query) is not None:
            return None
        # 소수·범위·한글 수량은 첫 슬라이스의 정수 1 계약으로 안전하게 거절한다.
        if _CRAFT_MALFORMED_QUANTITY_PATTERN.search(query) is not None and not quantities:
            return None
        if _CRAFT_WORD_QUANTITY_PATTERN.search(query) is not None:
            return None

        matched_result_ids = self._craft_result_ids_in(query)
        if len(matched_result_ids) != 1:
            return None
        result_item_id = next(iter(matched_result_ids))
        recipe_id = self._available_game_craft_recipes.get(result_item_id)
        if recipe_id is None:
            return None
        if recipe_tokens and recipe_tokens != {recipe_id}:
            return None
        return recipe_id

    def mobile_craft_request_for(self, query: str) -> CraftRequest | None:
        """모바일에서 지원하는 엉성한 붕대 제작 요청만 구조화한다.

        Recipe 질문은 ``is_craft_request`` 단계에서 제외되고, 수량은 원문의 온전한
        숫자 하나만 허용한다. LLM이 Recipe ID나 수량을 보충할 수 없다.
        """

        if not self.is_craft_request(query):
            return None
        if self._craft_result_ids_in(query) != {"ShoddyBandage"}:
            return None

        return self._mobile_craft_request(query)

    def mobile_craft_request_from_selection(
        self, query: str, selection: RecipeSelection
    ) -> CraftRequest | None:
        """LLM이 고른 allowlist Recipe를 모바일 제작 요청으로 검증한다."""

        resolved = self.query_from_selection(selection)
        if (
            resolved is None
            or resolved.mode is not RecipeQueryMode.DETAIL
            or len(resolved.targets) != 1
            or _MOBILE_CRAFT_RECIPE_ID not in resolved.targets[0].recipe_ids
        ):
            return None
        return self._mobile_craft_request(query)

    def _mobile_craft_request(self, query: str) -> CraftRequest | None:
        """검증된 recipe-1 대상에 적용할 원문 수량 계약을 확인한다."""

        quantities = [
            int(value.replace(",", "")) for value in _CRAFT_QUANTITY_PATTERN.findall(query)
        ]
        if len(quantities) > 1:
            return None
        quantity = quantities[0] if quantities else 1
        if not 1 <= quantity <= 50:
            return None
        if _CRAFT_WORD_QUANTITY_PATTERN.search(query) is not None:
            return None
        if _CRAFT_MALFORMED_QUANTITY_PATTERN.search(query) is not None and not quantities:
            return None
        assigned_quantities = _CRAFT_QUANTITY_ASSIGNMENT_PATTERN.findall(query)
        if assigned_quantities:
            if len(assigned_quantities) != 1:
                return None
            try:
                assigned = int(assigned_quantities[0])
            except ValueError:
                return None
            if assigned != quantity:
                return None
        recipe_tokens = {token.casefold() for token in _CRAFT_RECIPE_TOKEN_PATTERN.findall(query)}
        if recipe_tokens and recipe_tokens != {_MOBILE_CRAFT_RECIPE_ID}:
            return None
        if _CRAFT_BARE_NUMBER_PATTERN.search(query) is not None:
            return None
        return CraftRequest(recipe_id=_MOBILE_CRAFT_RECIPE_ID, quantity=quantity)

    def looks_like_craft_request(self, query: str) -> bool:
        """대상 해석 전에도 제작 행동 요청임을 판정한다."""

        return (
            _CRAFT_VERB_PATTERN.search(query) is not None
            and _CRAFT_FACT_PATTERN.search(query) is None
            and _CRAFT_NEGATION_PATTERN.search(query) is None
        )

    @staticmethod
    def looks_like_item_info(query: str) -> bool:
        """아이템의 뜻·정체를 묻는 표현인지 판정한다(대상 선택은 LLM/allowlist가 담당)."""

        return _ITEM_INFO_PATTERN.search(query) is not None

    def looks_like_recipe_question(self, query: str) -> bool:
        """Mock fallback에서만 쓰는 명시적 Recipe 질문 경계."""

        if self.looks_like_craft_request(query):
            return False
        targets = self.targets_in(query)
        if self.looks_like_item_info(query):
            return bool(targets)
        return bool(
            _STABLE_RECIPE_ID_PATTERN.search(query)
            or _RECIPE_QUERY_PATTERN.search(query)
            or _RECIPE_LIST_PATTERN.search(query)
            or _RECIPE_REFERENCE_PATTERN.search(query)
            or _RECIPE_COMPARE_PATTERN.search(query)
        )

    def is_craft_request(self, query: str) -> bool:
        """제작 의도처럼 보이는 발화를 분리해 malformed 요청도 안전하게 거절한다."""

        return self.looks_like_craft_request(query) and bool(self._craft_result_ids_in(query))

    def _craft_result_ids_in(self, query: str) -> set[str]:
        matched_result_ids = {
            "Sword_Iron" if item_id in _CRAFT_RESULT_ITEM_IDS else item_id
            for item_id, pattern in self._recipe_patterns
            if pattern.search(query) is not None
        }
        if any(alias_pattern(alias).search(query) is not None for alias in _CRAFT_RESULT_ALIASES):
            matched_result_ids.add("Sword_Iron")
        return matched_result_ids

    @staticmethod
    def _fact_ids_for(targets: Iterable[RecipeTarget]) -> tuple[str, ...]:
        return tuple(f"recipe:{recipe_id}" for target in targets for recipe_id in target.recipe_ids)

    def _describe_recipe(self, recipe: Recipe) -> str:
        result = self._result_items[recipe.result_item_id]
        ingredients = _join_ingredients(
            _amount_text(self._items.get(item.item_id), item.item_id, item.amount)
            for item in recipe.ingredients
        )
        station = _WORKBENCH_NAMES[recipe.required_workbench]
        result_amount = f" {recipe.result_amount}개" if recipe.result_amount != 1 else ""
        duration = f" {_duration_text(recipe.duration_seconds)} 만에"
        result_label = f"{result.name_ko}{result_amount}"
        return f"{topic(result_label)} {ingredients}로 {station}에서{duration} 만들 수 있어."

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
