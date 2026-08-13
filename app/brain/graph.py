"""마코 라우팅을 LangGraph `StateGraph` 로 표현한 그래프 정의.

노드는 순수 파이썬 함수이며 LLM 호출 계층(`LLMProvider`)과 사실 소유권
(저장소·파서·`dialogue.render`)은 그대로 둔다. LangChain 채팅 모델 래퍼는 쓰지 않는다.
각 노드는 부분 상태(`CompanionUpdate`)만 반환하고 병합은 LangGraph가 맡는다.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Literal, NotRequired, TypedDict

from langgraph.graph import END, StateGraph
from langgraph.graph.state import CompiledStateGraph
from pydantic import JsonValue

from app.models import CommandType

from .contract import CompanionAction, CompanionTurn, InventoryFacts, WorldContextFacts
from .dialogue import SURFACE_PROFILES, DialogueScene, DialogueSpec, SurfaceProfile, render
from .enemies import EnemyRepository
from .gametime import describe
from .intent import CommandLabel, ResourceSlot, TopIntent
from .llm import LLMProvider
from .lore import LoreRepository
from .recipes import RecipeRepository
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
    # 최근에 오간 말. **대사 생성에만** 쓴다 — 분류 노드는 읽지 않는다. 기록이 분류에 끼면
    # 세 턴 전 "따라와" 가 지금 명령을 다시 쏘는 길이 열린다.
    history: NotRequired[tuple[ConversationTurn, ...]]
    # 지난 세션들에서 회수한 기억. 브레인이 그래프를 부르기 전에 골라 넣는다. `history` 와
    # 같은 규칙이 그대로 적용된다 — **대사 생성에만** 쓰고 분류 노드는 읽지 않는다.
    # 저장소가 아니라 이미 회수된 문장만 들어오므로 노드는 기억이 어디서 왔는지 모른다.
    long_term: NotRequired[tuple[str, ...]]
    # 라우팅 중간값
    pending_answered: NotRequired[bool]
    top_intent: NotRequired[TopIntent]
    command_label: NotRequired[CommandLabel]
    resource: NotRequired[ResourceSlot]
    quantity: NotRequired[int | None]
    craft_requested: NotRequired[bool]
    craft_recipe_id: NotRequired[str | None]
    # 출력 누산기 (터미널 노드가 채움)
    display_text: NotRequired[str]
    action: NotRequired[CompanionAction | None]
    # 이번 턴이 새로 되물었을 때만 채워진다. 비면 슬롯은 사라진다 — 되묻지 않았으면
    # 잊는 것이 기본값이라, 노드가 정리를 잊어 슬롯이 눌어붙는 일이 생기지 않는다.
    next_pending: NotRequired[PendingSlot | None]


class CompanionUpdate(TypedDict, total=False):
    """노드가 반환하는 부분 상태. LangGraph 가 `CompanionState` 에 병합한다."""

    top_intent: TopIntent
    command_label: CommandLabel
    resource: ResourceSlot
    quantity: int | None
    craft_requested: bool
    craft_recipe_id: str | None
    display_text: str
    action: CompanionAction | None
    next_pending: PendingSlot | None
    pending_answered: bool


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


def _inventory_fact(inventory: InventoryFacts) -> str:
    items = ", ".join(
        f"{item.item_id} {item.count}개" for item in inventory.item_totals
    ) or "아이템 없음"
    suffix = ", 일부 종류 생략" if inventory.truncated else ""
    return (
        f"인벤토리 {inventory.container_id}: 빈 슬롯 {inventory.free_slots}, "
        f"{items}{suffix}"
    )


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
            f"주변 위협은 {threat.count}개이며 가장 가까운 종류는 "
            f"{threat.nearest_kind}다"
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
        facts.append(
            f"사용 가능한 작업대는 {', '.join(context.available_workstations)}다"
        )
    else:
        facts.append("사용 가능한 작업대가 없다")

    if context.current_work is not None:
        facts.append(
            f"현재 작업은 {context.current_work.type}/{context.current_work.state}다"
        )
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

    if state.get("craft_requested") or state.get("craft_recipe_id") is not None:
        return "craft"
    label = state["command_label"]
    if label in _COMMANDS:
        return "movement_command"
    if label is CommandLabel.GATHER_RESOURCE:
        return "gather"
    if label is CommandLabel.ATTACK:
        return "attack"
    return "unsupported"


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
    ) -> str:
        """검증된 사실을 근거로 대사를 생성하고, 실패 시 고정 템플릿으로 복구한다.

        상태를 통째로 받는 이유: 현재 발화와 최근 대화, 장기기억, 창구를 여기 한 곳에서만
        꺼내면, 새 장면을 추가할 때 문맥을 빠뜨릴 수 없다.
        """

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
                situation=describe(state["turn"].game_time),
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
        slot = await llm.resolve_pending(state["text"], pending)
        if slot is None:
            return {"pending_answered": False}
        return {
            "pending_answered": True,
            "resource": slot,
            "quantity": pending.quantity,
        }

    async def classify_top_node(state: CompanionState) -> CompanionUpdate:
        intent = await llm.classify_top(state["text"], clarification_pending=False)
        # 제작법 질문은 기존 recipe facts-only 경로에 남긴다. 명시적인 allowlist 제작
        # 요청만 provider의 분류 결과와 무관하게 command 경로로 올린다.
        craft_requested = recipes.is_craft_request(state["text"])
        if craft_requested:
            intent = TopIntent.COMMAND
        elif recipes.fact_for(state["text"]) is not None:
            # Provider가 질문을 명령으로 잘못 분류해도 검증된 제작법 사실은 행동으로
            # 승격하지 않는다.
            intent = TopIntent.RECIPE
        return {"top_intent": intent}

    async def command_classify_node(state: CompanionState) -> CompanionUpdate:
        classification = await llm.classify_command(state["text"])
        craft_requested = recipes.is_craft_request(state["text"])
        return {
            "command_label": classification.command,
            "resource": classification.resource,
            "quantity": classification.quantity,
            "craft_requested": craft_requested,
            "craft_recipe_id": recipes.craft_recipe_id_for(state["text"]),
        }

    async def craft_node(state: CompanionState) -> CompanionUpdate:
        """allowlist된 철검 제작 요청을 고정 Recipe 계약으로 변환한다."""

        turn = state["turn"]
        recipe_id = state.get("craft_recipe_id")
        if (
            not state.get("craft_requested")
            or recipe_id != "recipe-11"
            or CommandType.CRAFT_ITEM not in turn.allowed_actions
        ):
            return await decline(state)

        return {
            "display_text": await say(
                state,
                "recipe",
                "알겠어. 철검 하나를 만들게.",
                ("제작 레시피 ID는 recipe-11이다", "제작 수량은 1개다"),
            ),
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
            "display_text": await say(state, scene, fallback),
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
            "display_text": await say(state, "attack", "알겠어. 공격할게."),
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
        # 수량은 플레이어가 말했을 때만 흐른다. 확정 사실에 넣어야 대사에 숫자를 쓸 수 있다.
        facts = () if quantity is None else (f"요청 수량은 {quantity}개다",)
        parameters = GatherParameters(resource=resource, quantity=quantity)
        return {
            "display_text": await say(state, scene, fallback, facts),
            "action": CompanionAction(
                type=CommandType.GATHER_RESOURCE,
                parameters=parameters.model_dump(mode="json", exclude_none=True),
            ),
        }

    async def recipe_node(state: CompanionState) -> CompanionUpdate:
        text = state["text"]
        fact = recipes.fact_for(text)
        if fact is not None:
            return {"display_text": await say(state, "recipe", fact.text, (fact.text,))}
        return {
            "display_text": await say(
                state,
                "unsupported",
                "확인된 제작법을 찾지 못했어.",
                ("확인된 제작법 정보가 없다",),
            )
        }

    async def enemy_node(state: CompanionState) -> CompanionUpdate:
        text = state["text"]
        fact = enemies.fact_for(text)
        if fact is not None:
            return {"display_text": await say(state, "enemy", fact.text, (fact.text,))}
        return {
            "display_text": await say(
                state,
                "unsupported",
                "확인된 적 정보를 찾지 못했어.",
                ("확인된 적 정보가 없다",),
            )
        }

    async def lore_node(state: CompanionState) -> CompanionUpdate:
        fact = lore.fact_for(state["turn"].world_context.location_id)
        if fact is not None:
            return {"display_text": await say(state, "lore", fact.text, (fact.text,))}
        # 게임은 "지금 위치" 를 말할 수 있지만 폰은 그럴 위치가 없다.
        line = profile(state).lore_missing
        return {"display_text": await say(state, "unsupported", line.text, (line.fact,))}

    async def conversation_node(state: CompanionState) -> CompanionUpdate:
        text = state["text"]
        surface = profile(state)
        fallback = surface.thanks if "고마" in text or "감사" in text else surface.greeting
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
