"""마코 라우팅을 LangGraph `StateGraph` 로 표현한 그래프 정의.

노드는 순수 파이썬 함수이며 LLM 호출 계층(`LLMProvider`)과 사실 소유권
(저장소·파서·`dialogue.render`)은 그대로 둔다. LangChain 채팅 모델 래퍼는 쓰지 않는다.
각 노드는 부분 상태(`CompanionUpdate`)만 반환하고 병합은 LangGraph가 맡는다.
"""

from __future__ import annotations

import re
from datetime import UTC, datetime
from typing import Literal, NotRequired, TypedDict

from langgraph.graph import END, StateGraph
from langgraph.graph.state import CompiledStateGraph
from pydantic import JsonValue

from app.models import CommandType, Surface

from .command_intent import (
    GENERAL_QUESTION_PATTERN,
    CommandIntentParser,
)
from .contract import CompanionAction, CompanionTurn, InventoryFacts, WorldContextFacts
from .dialogue import SURFACE_PROFILES, DialogueScene, DialogueSpec, SurfaceProfile, render
from .enemies import EnemyRepository
from .gametime import describe
from .intent import (
    CommandLabel,
    ConversationMode,
    RecipeQueryMode,
    RequestQueryMode,
    ResourceSlot,
    TopIntent,
)
from .llm import LLMProvider
from .lore import LoreRepository
from .recipes import RecipeQuery, RecipeRepository, RecipeTarget
from .resources import GatherParameters, ResourceId, ResourceRepository
from .store import ConversationTurn, PendingSlot

# 명령 라벨 → (행동, 대사 장면, 폴백 대사)
# 한 테이블에 묶어 "대사는 있는데 명령이 없는" 비대칭을 원천 차단한다.
#
# **마코가 낼 수 있는 명령의 범위는 이 표와 아래 craft·attack·gather 노드가 정한다.** `CommandType`
# 은 게임의 프로토콜 전체(게임 전용 `EngageTarget`·`MoveToLocation` 등 포함)라 타입만으로는
# 걸러지지 않는다. 다만 여기
# 항목을 늘리려면 `dialogue.py` 의 `DialogueScene` Literal 과 `SCENE_GUIDE` 도 같이 넓혀야
# 하고 그건 mypy 가 강제하므로, 대사 없는 명령이 조용히 들어오지는 못한다.
_COMMANDS: dict[CommandLabel, tuple[CommandType, DialogueScene, str]] = {
    CommandLabel.FOLLOW_PLAYER: (
        CommandType.FOLLOW,
        "follow_player",
        "알겠어. 따라갈게.",
    ),
    CommandLabel.WAIT: (
        CommandType.HOLD_POSITION,
        "wait",
        "알겠어. 여기서 기다릴게.",
    ),
    CommandLabel.STOP_CURRENT_TASK: (
        CommandType.CANCEL_CURRENT,
        "stop_current_task",
        "알겠어. 지금 하던 일을 멈출게.",
    ),
    CommandLabel.RETURN_TO_PLAYER: (
        CommandType.SWITCH,
        "return_to_player",
        "알겠어. 네 곁으로 돌아갈게.",
    ),
}

# 자원 → (대사 장면, 폴백 대사). 지원 자원이 늘면 저장소와 이 표를 함께 넓힌다.
_GATHER_SCENES: dict[ResourceId, tuple[DialogueScene, str]] = {
    ResourceId.WOOD: ("gather_wood", "알겠어. 근처의 나무를 찾아볼게."),
    ResourceId.STONE: ("gather_stone", "알겠어. 근처의 돌을 찾아볼게."),
}

# 못 하는 일을 알리는 대사와 사실은 **창구마다 다르다** — `dialogue.py` 의 `SURFACE_PROFILES`
# 가 소유한다. 게임의 "따라오기, 대기, 중지" 를 모바일 프롬프트에 확정 사실로 넣으면 폰에는
# 없는 동작을 사실이라고 말하게 된다.
#
# 위의 두 표(`_COMMANDS`/`_GATHER_SCENES`)는 반대로 창구를 나누지 않는다. 지금 모바일은
# `allowed_actions` 가 비어 있어 수락 대사에 닿지 않는다. 모바일에 작업 지시가 생기는 날
# 이 표들에 창구 축을 넓히면 되고, 그때가 유일하게 올바른 시점이다.


class CompanionState(TypedDict):
    """그래프 한 회 실행이 들고 다니는 입력·라우팅 중간값·출력 누산기."""

    # 입력 (그래프 시작 시 채워짐)
    turn: CompanionTurn
    text: str
    # 직전 턴이 되물어 둔 슬롯. 저장소에서 읽어 브레인이 넣는다.
    pending: NotRequired[PendingSlot | None]
    # PendingSlot과 의미가 다른, 직전 Recipe 질의의 검증된 단일 대상.
    recipe_reference: NotRequired[RecipeTarget | None]
    # 최근에 오간 말은 원칙적으로 대사 생성에만 쓴다. 단, 직전 companion 발화가 직접
    # 질문이고 현재 발화가 짧은 비명령 답변이면 conversation으로만 복구한다. 과거 발화로
    # Command를 만들거나 슬롯을 채우지는 않는다.
    history: NotRequired[tuple[ConversationTurn, ...]]
    # 지난 세션들에서 회수한 기억. 브레인이 그래프를 부르기 전에 골라 넣는다. `history` 와
    # 같은 규칙이 그대로 적용된다 — **대사 생성에만** 쓰고 분류 노드는 읽지 않는다.
    # 저장소가 아니라 이미 회수된 문장만 들어오므로 노드는 기억이 어디서 왔는지 모른다.
    long_term: NotRequired[tuple[str, ...]]
    memory_required: NotRequired[bool]
    # 라우팅 중간값
    pending_answered: NotRequired[bool]
    top_intent: NotRequired[TopIntent]
    query_mode: NotRequired[RecipeQueryMode | RequestQueryMode | ConversationMode | None]
    recipe_query: NotRequired[RecipeQuery | None]
    command_label: NotRequired[CommandLabel]
    resource: NotRequired[ResourceSlot]
    quantity: NotRequired[int | None]
    craft_requested: NotRequired[bool]
    craft_recipe_id: NotRequired[str | None]
    craft_quantity: NotRequired[int | None]
    # 출력 누산기 (터미널 노드가 채움)
    display_text: NotRequired[str]
    action: NotRequired[CompanionAction | None]
    # 이번 턴이 새로 되물었을 때만 채워진다. 비면 슬롯은 사라진다 — 되묻지 않았으면
    # 잊는 것이 기본값이라, 노드가 정리를 잊어 슬롯이 눌어붙는 일이 생기지 않는다.
    next_pending: NotRequired[PendingSlot | None]
    next_recipe_reference: NotRequired[RecipeTarget | None]
    repository_match: NotRequired[bool]
    fact_ids: NotRequired[tuple[str, ...]]


class CompanionUpdate(TypedDict, total=False):
    """노드가 반환하는 부분 상태. LangGraph 가 `CompanionState` 에 병합한다."""

    top_intent: TopIntent
    query_mode: RecipeQueryMode | RequestQueryMode | ConversationMode | None
    recipe_query: RecipeQuery | None
    command_label: CommandLabel
    resource: ResourceSlot
    quantity: int | None
    craft_requested: bool
    craft_recipe_id: str | None
    craft_quantity: int | None
    display_text: str
    action: CompanionAction | None
    next_pending: PendingSlot | None
    next_recipe_reference: RecipeTarget | None
    pending_answered: bool
    repository_match: bool
    fact_ids: tuple[str, ...]


TopRoute = Literal["command_classify", "recipe", "enemy", "lore", "conversation", "unsupported"]
CommandRoute = Literal["movement_command", "craft", "gather", "attack", "unsupported"]
EntryRoute = Literal["resolve_pending", "classify_top"]
PendingRoute = Literal["gather", "classify_top"]

_TOP_ROUTES: dict[TopIntent, TopRoute] = {
    TopIntent.COMMAND: "command_classify",
    TopIntent.RECIPE: "recipe",
    TopIntent.ENEMY: "enemy",
    TopIntent.LORE: "lore",
    TopIntent.CONVERSATION: "conversation",
    TopIntent.UNKNOWN: "unsupported",
}

_PREFERENCE_SHARE_PATTERN = re.compile(r"(?:좋아해|좋아하|싫어해|싫어하|선호해|선호하)")
_MEMORY_RECALL_PATTERN = re.compile(r"(?:기억(?:해|나|하)|전에\s*말|내\s*(?:취향|약속|이름))")
_EMOTIONAL_PATTERN = re.compile(r"(?:힘들|우울|속상|슬퍼|외로|불안|지쳤|피곤|괴로)")
_OPINION_PATTERN = re.compile(r"(?:어떻게\s*생각|네\s*생각|조언|추천|뭐가\s*(?:좋|나)|어떡할까)")
_QUESTION_PATTERN = re.compile(r"(?:뭐|무엇|왜|어떻게|언제|어디|누구|알려|설명|인가|일까|\?)")


def _request_query_mode(
    intent: TopIntent,
    text: str,
) -> RequestQueryMode | ConversationMode | None:
    if intent is TopIntent.CONVERSATION:
        if _MEMORY_RECALL_PATTERN.search(text) is not None:
            return ConversationMode.MEMORY_RECALL
        if _EMOTIONAL_PATTERN.search(text) is not None:
            return ConversationMode.EMOTIONAL_SUPPORT
        if _OPINION_PATTERN.search(text) is not None:
            return ConversationMode.OPINION_ADVICE
        if _PREFERENCE_SHARE_PATTERN.search(text) is not None:
            return ConversationMode.PREFERENCE_SHARE
        if _QUESTION_PATTERN.search(text) is not None:
            return ConversationMode.GENERAL_KNOWLEDGE
        if len(text.strip()) <= 1:
            return ConversationMode.AMBIGUOUS
        return ConversationMode.SMALL_TALK
    if intent in (TopIntent.ENEMY, TopIntent.LORE):
        return RequestQueryMode.INFORMATION_QUESTION
    if intent is TopIntent.UNKNOWN and GENERAL_QUESTION_PATTERN.search(text.strip()) is not None:
        return RequestQueryMode.UNSUPPORTED_FACT
    if intent is TopIntent.UNKNOWN:
        return ConversationMode.NOT_APPLICABLE
    return None


def _inventory_fact(inventory: InventoryFacts) -> str:
    items = (
        ", ".join(f"{item.item_id} {item.count}개" for item in inventory.item_totals)
        or "아이템 없음"
    )
    suffix = ", 일부 종류 생략" if inventory.truncated else ""
    return f"인벤토리 {inventory.container_id}: 빈 슬롯 {inventory.free_slots}, {items}{suffix}"


def _describe_world_context(context: WorldContextFacts) -> tuple[str, ...]:
    if not context.is_available:
        return ()

    facts: list[str] = [
        (
            f"현재 위치 ID는 {context.location_id}다"
            if context.location_id is not None
            else "현재 위치 ID는 확인되지 않았다"
        )
    ]
    threat = context.threat
    if threat is not None and threat.present:
        facts.append(
            f"주변 위협은 {threat.count}개이며 가장 가까운 종류는 {threat.nearest_kind}다"
            if threat.nearest_kind is not None
            else f"주변 위협은 {threat.count}개다"
        )
    else:
        facts.append("주변에 확인된 위협이 없다")

    if context.nearby_resources:
        resources = ", ".join(
            f"{resource.kind} {resource.count}개" for resource in context.nearby_resources
        )
        facts.append(f"주변 자원은 {resources}다")
    else:
        facts.append("주변에 확인된 자원이 없다")

    if context.available_workstations:
        facts.append(f"사용 가능한 작업대는 {', '.join(context.available_workstations)}다")
    else:
        facts.append("사용 가능한 작업대가 없다")

    if context.current_work is not None:
        facts.append(f"현재 작업은 {context.current_work.type}/{context.current_work.state}다")
    else:
        facts.append("현재 진행 중인 작업이 없다")

    if context.inventories:
        facts.extend(_inventory_fact(inventory) for inventory in context.inventories)
    else:
        facts.append("확인된 인벤토리 요약이 없다")
    return tuple(facts)


def route_by_entry(state: CompanionState) -> EntryRoute:
    """되물어 둔 슬롯이 있으면 그 답인지부터 확인한다.

    슬롯이 없으면 기존 경로와 완전히 같다. 멀티턴은 되물은 직후 한 턴에만 개입한다.
    """

    return "resolve_pending" if state.get("pending") is not None else "classify_top"


def route_after_pending(state: CompanionState) -> PendingRoute:
    """되묻기 답이면 채집으로, 화제가 바뀌었으면 평소대로 분류한다."""

    return "gather" if state.get("pending_answered") else "classify_top"


def route_by_top(state: CompanionState) -> TopRoute:
    """최상위 의도에 따라 다음 노드를 고른다."""

    return _TOP_ROUTES[state["top_intent"]]


def route_by_command(state: CompanionState) -> CommandRoute:
    """명령 라벨에 따라 다음 노드를 고른다."""

    if (
        state.get("craft_requested")
        or state.get("craft_recipe_id") is not None
        or state.get("command_label") is CommandLabel.CRAFT_ITEM
    ):
        return "craft"
    label = state["command_label"]
    if label in _COMMANDS:
        return "movement_command"
    if label is CommandLabel.GATHER_RESOURCE:
        return "gather"
    if label is CommandLabel.ATTACK:
        return "attack"
    return "unsupported"


def selected_route(state: CompanionState) -> str:
    """완료 상태에서 실제 terminal route를 재구성한다."""

    if state.get("pending_answered"):
        return "gather"
    top_route = route_by_top(state)
    return route_by_command(state) if top_route == "command_classify" else top_route


def build_companion_graph(
    llm: LLMProvider,
    recipes: RecipeRepository,
    lore: LoreRepository,
    resources: ResourceRepository,
    enemies: EnemyRepository,
) -> CompiledStateGraph[CompanionState, None, CompanionState, CompanionState]:
    """저장소·공급자를 클로저로 캡처한 노드로 마코 라우팅 그래프를 컴파일한다."""

    def profile(state: CompanionState) -> SurfaceProfile:
        """이 턴이 어느 창구에서 왔는지. 창구를 읽는 곳은 여기 하나뿐이다."""

        return SURFACE_PROFILES[state["turn"].surface]

    async def say(
        state: CompanionState,
        scene: DialogueScene,
        fallback: str,
        facts: tuple[str, ...] = (),
        *,
        command_candidate_present: bool = False,
    ) -> str:
        """검증된 사실을 근거로 대사를 생성하고, 실패 시 고정 템플릿으로 복구한다.

        상태를 통째로 받는 이유: 현재 발화와 최근 대화, 장기기억, 창구를 여기 한 곳에서만
        꺼내면, 새 장면을 추가할 때 문맥을 빠뜨릴 수 없다.
        """

        context_memory_required = bool(
            state.get("memory_required")
            and scene == "conversation"
            and "고마" not in state["text"]
            and "감사" not in state["text"]
            and re.search(r"(?:안녕|반가워|하이)", state["text"]) is None
        )
        return await render(
            llm,
            DialogueSpec(
                scene=scene,
                fallback=fallback,
                surface=state["turn"].surface,
                user_text=state["text"],
                facts=(*facts, *_describe_world_context(state["turn"].world_context)),
                history=state.get("history", ()),
                memories=state.get("long_term", ()),
                memory_use_policy=(
                    "Required"
                    if context_memory_required
                    else "Optional"
                    if state.get("long_term")
                    else "None"
                ),
                situation=describe(state["turn"].game_time),
                relationship_state=state["turn"].relationship_state,
                command_candidate_present=command_candidate_present,
            ),
        )

    async def decline(state: CompanionState) -> CompanionUpdate:
        """명령을 낼 수 없을 때 수락 대사 대신 거절 대사만 남긴다."""

        line = profile(state).not_allowed
        return {
            "display_text": await say(state, "unsupported", line.text, (line.fact,)),
            "action": None,
        }

    async def resolve_pending_node(state: CompanionState) -> CompanionUpdate:
        """되물은 슬롯에 대한 답인지 판정하고, 답이면 채집 슬롯을 채운다.

        플레이어가 이미 말했던 수량은 슬롯에서 되살린다. 되물었다고 버리면
        "20개 캐 줘" → "뭘 캐?" → "나무" 에서 20이 사라진다.
        """

        pending = state["pending"]
        assert pending is not None  # route_by_entry 가 있을 때만 이 노드로 온다
        # 질문은 되물은 자원의 답으로 해석하지 않는다. 그렇지 않으면
        # "무엇을 캘까?" → "나무를 어떻게 캐?"가 Game 후보로 승격된다.
        if state["turn"].surface is Surface.GAME and CommandIntentParser.is_gather_question(
            state["text"]
        ):
            return {"pending_answered": False}
        proposed_slot = await llm.resolve_pending(state["text"], pending)
        matched_resources = resources.find_all(state["text"])
        if len(matched_resources) == 1:
            verified_slot = ResourceSlot(matched_resources[0].value)
        elif len(matched_resources) > 1:
            verified_slot = ResourceSlot.UNSPECIFIED
        else:
            verified_slot = None
        if proposed_slot is None or proposed_slot is not verified_slot:
            return {"pending_answered": False}
        return {
            "pending_answered": True,
            "resource": verified_slot,
            "quantity": pending.quantity,
        }

    async def classify_top_node(state: CompanionState) -> CompanionUpdate:
        intent = await llm.classify_top(
            state["text"],
            clarification_pending=False,
            history=state.get("history", ()),
        )
        # LLM이 먼저 발화 목적을 판정한다. 검증된 아이템 이름이 포함됐다는 이유만으로
        # conversation을 recipe로 덮어쓰지 않는다(`안녕 돌도끼?` 경계).
        craft_requested = False
        mobile_craft = None
        recipe_query = None
        if (
            intent is TopIntent.RECIPE
            and re.search(r"(?:안녕|반가워|하이)", state["text"]) is not None
            and not recipes.looks_like_recipe_question(state["text"])
        ):
            intent = TopIntent.CONVERSATION
        if intent is TopIntent.CONVERSATION and recipes.looks_like_item_info(state["text"]):
            selection = await llm.resolve_recipe(state["text"], recipes.selection_options())
            resolved_query = recipes.query_from_selection(selection)
            if resolved_query is not None:
                intent = TopIntent.RECIPE
                recipe_query = RecipeQuery(RecipeQueryMode.ITEM_INFO, resolved_query.targets)
        if intent is TopIntent.RECIPE and recipe_query is None:
            recipe_query = recipes.query_for(
                state["text"], recent_target=state.get("recipe_reference")
            )
        if state["turn"].surface is Surface.GAME and CommandIntentParser.is_gather_question(
            state["text"]
        ):
            # Provider가 채집 방법·가능 여부 질문을 command로 잘못 분류해도
            # Game GatherResource 후보 경계로 들어오지 못하게 한다.
            intent = TopIntent.UNKNOWN
            recipe_query = None
        if intent is TopIntent.RECIPE and recipes.should_resolve_natural_language(
            state["text"], recipe_query
        ):
            selection = await llm.resolve_recipe(state["text"], recipes.selection_options())
            resolved_query = recipes.query_from_selection(selection)
            if resolved_query is not None:
                recipe_query = (
                    RecipeQuery(RecipeQueryMode.ITEM_INFO, resolved_query.targets)
                    if recipes.looks_like_item_info(state["text"])
                    else resolved_query
                )
        if intent is TopIntent.RECIPE and recipe_query is None:
            recipe_query = RecipeQuery(RecipeQueryMode.AMBIGUOUS)
        query_mode: RecipeQueryMode | RequestQueryMode | ConversationMode | None = (
            recipe_query.mode
            if intent is TopIntent.RECIPE and recipe_query is not None
            else _request_query_mode(intent, state["text"])
        )
        return {
            "top_intent": intent,
            "query_mode": query_mode,
            "recipe_query": recipe_query,
            "craft_requested": craft_requested,
            "craft_recipe_id": mobile_craft.recipe_id if mobile_craft is not None else None,
            "craft_quantity": mobile_craft.quantity if mobile_craft is not None else None,
        }

    async def command_classify_node(state: CompanionState) -> CompanionUpdate:
        proposed = await llm.classify_command(state["text"])
        classification = CommandIntentParser.corroborate(state["text"], proposed)
        craft_requested = classification.command is CommandLabel.CRAFT_ITEM
        craft_recipe_id = state.get("craft_recipe_id")
        craft_quantity = state.get("craft_quantity")
        if craft_requested and state["turn"].surface is Surface.MOBILE:
            mobile_craft = recipes.mobile_craft_request_for(state["text"])
            if mobile_craft is None:
                selection = await llm.resolve_recipe(state["text"], recipes.selection_options())
                mobile_craft = recipes.mobile_craft_request_from_selection(
                    state["text"], selection
                )
            if mobile_craft is not None:
                craft_recipe_id = mobile_craft.recipe_id
                craft_quantity = mobile_craft.quantity
        elif craft_requested and state["turn"].surface is Surface.GAME:
            craft_recipe_id = recipes.craft_recipe_id_for(state["text"])
        return {
            "command_label": classification.command,
            "resource": classification.resource,
            "quantity": classification.quantity,
            "craft_requested": craft_requested,
            "craft_recipe_id": craft_recipe_id,
            "craft_quantity": craft_quantity,
        }

    async def craft_node(state: CompanionState) -> CompanionUpdate:
        """창구별 allowlist 제작 요청을 stable Recipe 계약으로 변환한다."""

        turn = state["turn"]
        recipe_id = state.get("craft_recipe_id")
        quantity = state.get("craft_quantity")
        if turn.surface is Surface.MOBILE:
            if (
                not state.get("craft_requested")
                or recipe_id != "recipe-1"
                or quantity is None
                or CommandType.CRAFT_ITEM not in turn.allowed_actions
            ):
                return await decline(state)
            return {
                "display_text": f"좋아. 엉성한 붕대 {quantity}개 제작을 예약할게.",
                "action": CompanionAction(
                    type=CommandType.CRAFT_ITEM,
                    parameters={"recipe_id": recipe_id, "quantity": quantity},
                ),
            }
        if (
            not state.get("craft_requested")
            or recipe_id != "recipe-11"
            or CommandType.CRAFT_ITEM not in turn.allowed_actions
        ):
            return await decline(state)

        return {
            # 명령 수락 대사는 Recipe 설명 장면이 아니다. LLM 재작성이나 World Context를
            # 거치면 Inventory 사실을 재료로 섞거나 실행 대신 제작법을 설명할 수 있으므로
            # 검증된 고정 문구를 Candidate와 함께 반환한다.
            "display_text": "알겠어. 철검 하나를 만들게.",
            "action": CompanionAction(
                type=CommandType.CRAFT_ITEM,
                parameters={"recipe_id": recipe_id, "quantity": 1},
            ),
        }

    async def movement_command_node(state: CompanionState) -> CompanionUpdate:
        """이동 계열 명령을 대사와 (허용된 경우) 행동으로 변환한다."""

        turn = state["turn"]
        action_type, scene, fallback = _COMMANDS[state["command_label"]]
        # 허용 목록에 없으면 수락 대사를 내면 안 된다. 말만 하고 움직이지 않게 된다.
        if action_type not in turn.allowed_actions:
            return await decline(state)

        return {
            "display_text": await say(state, scene, fallback, command_candidate_present=True),
            "action": CompanionAction(type=action_type),
        }

    async def attack_node(state: CompanionState) -> CompanionUpdate:
        """공격 명령을 대사와 (허용된 경우) 행동으로 변환한다.

        대상은 LLM 슬롯이 아니라 발화 원문에서 직접 해석한다 — `enemy_node` 가 `fact_for` 로
        하는 것과 같은 방식이다. 이름이 없거나 특정되지 않으면 `target_id` 없이 명령만 내고
        게임이 현재 타깃에 적용하게 둔다.
        """

        turn = state["turn"]
        if CommandType.ATTACK not in turn.allowed_actions:
            return await decline(state)

        target_id = enemies.resolve_target(state["text"])
        parameters: dict[str, JsonValue] = {"target_id": target_id} if target_id else {}
        return {
            "display_text": await say(
                state,
                "attack",
                "알겠어. 공격할게.",
                command_candidate_present=True,
            ),
            "action": CompanionAction(type=CommandType.ATTACK, parameters=parameters),
        }

    async def gather_node(state: CompanionState) -> CompanionUpdate:
        """채집 슬롯을 검증해 대사를 만들고, 지원 범위 안이면 행동도 낸다."""

        turn = state["turn"]
        slot = state["resource"]
        quantity = state["quantity"]

        # 채집이 허용되지 않으면 자원을 따지기 전에 거절한다.
        if CommandType.GATHER_RESOURCE not in turn.allowed_actions:
            return await decline(state)

        # 질문을 행동으로 바꾸거나, 원문에 수량 표현이 있는데 parser가 온전한 정수 하나로
        # 확정하지 못한 요청을 기본 50개로 바꾸지 않는다.
        if CommandIntentParser.is_gather_question(state["text"]):
            return await decline(state)
        if (
            turn.surface is Surface.MOBILE
            and CommandIntentParser.has_gather_quantity(state["text"])
            and quantity is None
        ):
            return await decline(state)

        if slot is ResourceSlot.UNSPECIFIED:
            names = "와 ".join(resources.supported_names())
            # 되묻기를 이어가는 것은 방금 그 질문에 모호하게 답했을 때뿐이다.
            # 플레이어가 새 채집 요청을 시작했다면 옛 슬롯은 남의 것이다 — 그대로 이어받으면
            # 지난 요청의 수량이 이번 요청으로 새어 들어온다.
            asked = state.get("pending") if state.get("pending_answered") else None
            # 상한을 넘기면 더 되묻지 않는다. 계속 물으면 같은 질문만 되풀이하게 된다.
            if asked is not None and not asked.may_ask_again:
                return {
                    "display_text": await say(
                        state,
                        "unsupported",
                        f"지금은 {names} 채집만 도와줄 수 있어.",
                        (f"채집할 수 있는 자원은 {names}뿐이다",),
                    )
                }
            now = datetime.now(UTC)
            return {
                "display_text": await say(
                    state,
                    "gather_ambiguous",
                    "무엇을 캐면 될까?",
                    (f"고를 수 있는 자원은 {names}뿐이다",),
                ),
                # 되물었으니 다음 턴이 답을 이어받을 수 있게 슬롯을 남긴다.
                "next_pending": (
                    asked.asked_again(now=now)
                    if asked is not None
                    else PendingSlot(
                        kind="gather_resource",
                        quantity=quantity,
                        ask_count=1,
                        asked_at=now,
                    )
                ),
            }

        resource = resources.resolve_slot(slot)
        if resource is None:
            names = "와 ".join(resources.supported_names())
            return {
                "display_text": await say(
                    state,
                    "unsupported",
                    f"지금은 {names} 채집만 도와줄 수 있어.",
                    (f"채집할 수 있는 자원은 {names}뿐이다",),
                )
            }

        if turn.surface is Surface.GAME and (
            resource is not ResourceId.WOOD
            or quantity is not None
            or CommandIntentParser.has_gather_quantity(state["text"])
        ):
            # InGame 첫 수직 슬라이스는 명시적 wood 한 그루 WorkOrder 요청만 허용한다. 수량을
            # 해석하지 못한 malformed/vague 표현도 `has_gather_quantity`가 잡는다.
            return await decline(state)

        if not resources.allows_quantity(quantity):
            return {
                "display_text": await say(
                    state,
                    "unsupported",
                    f"한 번에 {resources.max_quantity}개까지만 캘 수 있어.",
                    (f"한 번에 채집할 수 있는 최대 수량은 {resources.max_quantity}개다",),
                )
            }

        scene, fallback = _GATHER_SCENES[resource]
        if turn.surface is Surface.MOBILE:
            task_quantity = quantity or resources.max_quantity
            fallback = (
                f"좋아. {resources.display_name(resource)} {task_quantity}개를 모으는 "
                "작업을 시작할게."
            )
        # 수량은 플레이어가 말했을 때만 흐른다. 확정 사실에 넣어야 대사에 숫자를 쓸 수 있다.
        facts = () if quantity is None else (f"요청 수량은 {quantity}개다",)
        parameters = GatherParameters(resource=resource, quantity=quantity)
        return {
            "display_text": await say(
                state,
                scene,
                fallback,
                facts,
                command_candidate_present=True,
            ),
            "action": CompanionAction(
                type=CommandType.GATHER_RESOURCE,
                parameters=parameters.model_dump(mode="json", exclude_none=True),
            ),
        }

    async def recipe_node(state: CompanionState) -> CompanionUpdate:
        query = state.get("recipe_query")
        if query is not None:
            result = recipes.result_for_query(query)
            if result is not None:
                # RecipeRepository가 이미 검증된 목록 또는 재료·수량·작업대·시간 문장을
                # 만든다. 다시 LLM에 맡기면 사실을 생략하거나 World Context Item을 섞을 수
                # 있다. 직전 참조는 상세 target 하나일 때만 남긴다.
                reference = (
                    query.targets[0]
                    if query.mode is RecipeQueryMode.DETAIL and len(query.targets) == 1
                    else None
                )
                return {
                    "display_text": result.text,
                    "action": None,
                    "repository_match": True,
                    "fact_ids": result.fact_ids,
                    "next_recipe_reference": reference,
                }
            if query.mode is RecipeQueryMode.AMBIGUOUS:
                return {
                    "display_text": recipes.clarification_for(query),
                    "action": None,
                }
            if query.mode is RecipeQueryMode.UNKNOWN_RECIPE:
                return {
                    "display_text": (
                        "확인된 제작법에 없는 대상이야. 이름이나 Recipe ID를 다시 확인해 줘."
                    ),
                    "action": None,
                }
        return {
            "display_text": "확인된 제작법을 찾지 못했어.",
            "action": None,
        }

    async def enemy_node(state: CompanionState) -> CompanionUpdate:
        text = state["text"]
        fact = enemies.fact_for(text)
        if fact is not None:
            fact_id = enemies.resolve_target(text)
            return {
                "display_text": await say(state, "enemy", fact.text, (fact.text,)),
                "repository_match": True,
                "fact_ids": (fact_id,) if fact_id is not None else (),
            }
        return {
            "display_text": await say(
                state,
                "unsupported",
                "확인된 적 정보를 찾지 못했어.",
                ("확인된 적 정보가 없다",),
            )
        }

    async def lore_node(state: CompanionState) -> CompanionUpdate:
        location_id = state["turn"].world_context.location_id
        fact = lore.fact_for(location_id)
        if fact is not None:
            return {
                "display_text": await say(state, "lore", fact.text, (fact.text,)),
                "repository_match": True,
                "fact_ids": (location_id,) if location_id is not None else (),
            }
        # 게임은 "지금 위치" 를 말할 수 있지만 폰은 그럴 위치가 없다.
        line = profile(state).lore_missing
        return {"display_text": await say(state, "unsupported", line.text, (line.fact,))}

    async def conversation_node(state: CompanionState) -> CompanionUpdate:
        text = state["text"]
        surface = profile(state)
        if "고마" in text or "감사" in text:
            fallback = surface.thanks
        elif re.search(r"(?:안녕|반가워|하이)", text) is not None:
            fallback = surface.greeting
        elif state.get("query_mode") is ConversationMode.EMOTIONAL_SUPPORT:
            fallback = "많이 버거웠겠다. 지금은 해결책보다 네 얘기를 먼저 들어줄게."
        elif state.get("query_mode") is ConversationMode.OPINION_ADVICE:
            fallback = "상황을 조금만 더 알려 주면, 내가 생각하는 선택지와 이유를 솔직히 말해볼게."
        elif state.get("query_mode") is ConversationMode.GENERAL_KNOWLEDGE:
            fallback = (
                "그건 내가 아는 범위에서 설명할 수 있어. "
                "다만 최신 정보라면 지금 확인할 수는 없어."
            )
        elif state.get("query_mode") is ConversationMode.MEMORY_RECALL:
            fallback = "그 부분은 아직 확실히 기억나는 게 없어. 네가 다시 알려 주면 좋겠어."
        elif state.get("query_mode") is ConversationMode.PREFERENCE_SHARE:
            fallback = "그걸 좋아하는구나. 지금 대화에서는 잘 새겨들을게."
        elif state.get("query_mode") is ConversationMode.AMBIGUOUS:
            fallback = "그 말은 내가 제대로 이해했는지 조금 애매해. 한마디만 더 이어서 말해 줄래?"
        else:
            fallback = "응, 계속 말해 줘. 같이 이야기해보자."
        return {"display_text": await say(state, "conversation", fallback)}

    async def unsupported_node(state: CompanionState) -> CompanionUpdate:
        line = profile(state).unsupported
        return {"display_text": await say(state, "unsupported", line.text, (line.fact,))}

    graph = StateGraph(CompanionState)
    graph.add_node("resolve_pending", resolve_pending_node)
    graph.add_node("classify_top", classify_top_node)
    graph.add_node("command_classify", command_classify_node)
    graph.add_node("craft", craft_node)
    graph.add_node("movement_command", movement_command_node)
    graph.add_node("attack", attack_node)
    graph.add_node("gather", gather_node)
    graph.add_node("recipe", recipe_node)
    graph.add_node("enemy", enemy_node)
    graph.add_node("lore", lore_node)
    graph.add_node("conversation", conversation_node)
    graph.add_node("unsupported", unsupported_node)

    graph.set_conditional_entry_point(route_by_entry)
    graph.add_conditional_edges("resolve_pending", route_after_pending)
    graph.add_conditional_edges("classify_top", route_by_top)
    graph.add_conditional_edges("command_classify", route_by_command)
    for terminal in (
        "movement_command",
        "craft",
        "attack",
        "gather",
        "recipe",
        "enemy",
        "lore",
        "conversation",
        "unsupported",
    ):
        graph.add_edge(terminal, END)

    return graph.compile()
