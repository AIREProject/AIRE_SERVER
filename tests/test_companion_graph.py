"""마코 라우팅 그래프(StateGraph)의 분기 함수와 실행 경계 검증.

라우팅 함수는 순수 함수이므로 상태만 만들어 단위로 확인하고,
그래프 실행은 모든 터미널 노드가 대사를 채운다는 불변식을 확인한다.
"""

from datetime import UTC, datetime

import pytest
from langgraph.graph.state import CompiledStateGraph

from app.brain import CompanionBrain, CompanionReply, CompanionTurn
from app.brain.contract import (
    InventoryFacts,
    InventoryItemFacts,
    MemoryScope,
    ResourceFacts,
    ThreatFacts,
    WorkFacts,
    WorldContextFacts,
)
from app.brain.dialogue import SURFACE_PROFILES, DialogueOutput, DialogueSpec
from app.brain.enemies import EnemyRepository
from app.brain.graph import (
    CompanionState,
    _request_query_mode,
    build_companion_graph,
    route_by_command,
    route_by_top,
)
from app.brain.intent import (
    CommandClassification,
    CommandLabel,
    ConversationMode,
    ResourceSlot,
    TopIntent,
)
from app.brain.llm import LLMProvider, MockLLMProvider, TimingLLMProvider
from app.brain.lore import LoreRepository
from app.brain.memory import (
    Consolidation,
    ConsolidationSpec,
    MemoryExtraction,
    MemoryExtractionSpec,
    SessionSummary,
    SessionSummarySpec,
)
from app.brain.recipes import RecipeRepository, RecipeSelection, RecipeSelectionOption
from app.brain.resources import MAX_GATHER_QUANTITY, ResourceRepository
from app.brain.store import (
    MAX_ASK_COUNT,
    ConversationTurn,
    InMemoryConversationStore,
    PendingSlot,
)
from app.gamedata.dataset import ITEMS
from app.models import CommandType, Surface, TimeContext, TimeSource
from app.source_memory_store import RecalledMemory


@pytest.mark.parametrize(
    ("intent", "text", "expected"),
    (
        (TopIntent.CONVERSATION, "오늘도 같이 이야기하자", ConversationMode.SMALL_TALK),
        (TopIntent.CONVERSATION, "오늘 너무 힘들어", ConversationMode.EMOTIONAL_SUPPORT),
        (TopIntent.CONVERSATION, "어떻게 생각해?", ConversationMode.OPINION_ADVICE),
        (TopIntent.CONVERSATION, "파이썬이 뭐야?", ConversationMode.GENERAL_KNOWLEDGE),
        (TopIntent.CONVERSATION, "나는 겨울을 좋아해", ConversationMode.PREFERENCE_SHARE),
        (TopIntent.CONVERSATION, "내 취향 기억해?", ConversationMode.MEMORY_RECALL),
        (TopIntent.CONVERSATION, "음", ConversationMode.AMBIGUOUS),
        (TopIntent.UNKNOWN, "...", ConversationMode.NOT_APPLICABLE),
    ),
)
def test_conversation_modes_cover_each_response_boundary(
    intent: TopIntent, text: str, expected: ConversationMode
) -> None:
    assert _request_query_mode(intent, text) is expected


def test_memory_context_query_uses_only_safe_context_fields() -> None:
    context = WorldContextFacts(
        is_available=True,
        location_id="location-cave",
        threat=ThreatFacts(present=True, count=2, nearest_kind="wolf"),
        nearby_resources=(ResourceFacts(kind="secret-resource", count=9),),
        available_workstations=("secret-workstation",),
        current_work=WorkFacts(type="Gather", state="Running"),
        inventories=(
            InventoryFacts(
                container_id="secret-inventory",
                free_slots=3,
                item_totals=(InventoryItemFacts(item_id="secret-item", count=4),),
                truncated=False,
            ),
        ),
    )
    query = CompanionBrain._context_memory_query(
        TimeContext(source=TimeSource.GAME_WORLD, day=2, hour=23, period="Night"),
        context,
    )

    assert query == "Night location-cave 위협 wolf Gather Running"
    assert "secret-resource" not in query
    assert "secret-workstation" not in query
    assert "secret-inventory" not in query
    assert "secret-item" not in query


def make_turn(
    text: str,
    allowed_actions: frozenset[CommandType] | None = None,
    *,
    conversation_key: str = "conv-graph",
    surface: Surface = Surface.GAME,
    game_time: TimeContext | None = None,
) -> CompanionTurn:
    return CompanionTurn(
        text=text,
        conversation_key=conversation_key,
        surface=surface,
        game_time=game_time,
        allowed_actions=(frozenset(CommandType) if allowed_actions is None else allowed_actions),
    )


def make_graph(
    llm: LLMProvider | None = None,
) -> CompiledStateGraph[CompanionState, None, CompanionState, CompanionState]:
    return build_companion_graph(
        llm or MockLLMProvider(),
        RecipeRepository(),
        LoreRepository(),
        ResourceRepository(),
        EnemyRepository(),
    )


def top_state(intent: TopIntent) -> CompanionState:
    return {"turn": make_turn("따라와"), "text": "따라와", "top_intent": intent}


def command_state(label: CommandLabel) -> CompanionState:
    return {"turn": make_turn("따라와"), "text": "따라와", "command_label": label}


class ExplodingLLMProvider(LLMProvider):
    """분류 단계에서 실패해 그래프 실행 경계를 시험하는 공급자."""

    async def classify_top(
        self,
        text: str,
        *,
        clarification_pending: bool,
        history: tuple[ConversationTurn, ...] = (),
    ) -> TopIntent:
        del text, clarification_pending, history
        raise RuntimeError("classification down")

    async def classify_command(self, text: str) -> CommandClassification:
        raise RuntimeError("classification down")

    async def resolve_pending(self, text: str, pending: PendingSlot) -> ResourceSlot | None:
        raise RuntimeError("classification down")

    async def generate_dialogue(self, spec: DialogueSpec) -> str:
        raise RuntimeError("dialogue down")

    async def extract_memories(self, spec: MemoryExtractionSpec) -> MemoryExtraction:
        raise RuntimeError("extraction down")

    async def summarize_session(self, spec: SessionSummarySpec) -> SessionSummary:
        raise RuntimeError("summary down")

    async def consolidate_memories(self, spec: ConsolidationSpec) -> Consolidation:
        raise RuntimeError("consolidation down")


class ForcedCommandProvider(MockLLMProvider):
    def __init__(self, classification: CommandClassification) -> None:
        super().__init__()
        self._classification = classification

    async def classify_top(
        self,
        text: str,
        *,
        clarification_pending: bool,
        history: tuple[ConversationTurn, ...] = (),
    ) -> TopIntent:
        del text, clarification_pending, history
        return TopIntent.COMMAND

    async def classify_command(self, text: str) -> CommandClassification:
        del text
        return self._classification


class ForcedPendingProvider(MockLLMProvider):
    async def resolve_pending(self, text: str, pending: PendingSlot) -> ResourceSlot | None:
        del text, pending
        return ResourceSlot.WOOD


class ForcedRecipeTopProvider(MockLLMProvider):
    async def classify_top(
        self,
        text: str,
        *,
        clarification_pending: bool,
        history: tuple[ConversationTurn, ...] = (),
    ) -> TopIntent:
        del text, clarification_pending, history
        return TopIntent.RECIPE


@pytest.mark.parametrize(
    ("intent", "destination"),
    [
        (TopIntent.COMMAND, "command_classify"),
        (TopIntent.RECIPE, "recipe"),
        (TopIntent.ENEMY, "enemy"),
        (TopIntent.LORE, "lore"),
        (TopIntent.CONVERSATION, "conversation"),
        (TopIntent.UNKNOWN, "unsupported"),
    ],
)
def test_route_by_top_covers_every_intent(intent: TopIntent, destination: str) -> None:
    assert route_by_top(top_state(intent)) == destination


@pytest.mark.parametrize(
    ("label", "destination"),
    [
        (CommandLabel.FOLLOW_PLAYER, "movement_command"),
        (CommandLabel.WAIT, "movement_command"),
        (CommandLabel.STOP_CURRENT_TASK, "movement_command"),
        (CommandLabel.RETURN_TO_PLAYER, "movement_command"),
        (CommandLabel.GATHER_RESOURCE, "gather"),
        (CommandLabel.ATTACK, "attack"),
        (CommandLabel.UNKNOWN, "unsupported"),
    ],
)
def test_route_by_command_covers_every_label(label: CommandLabel, destination: str) -> None:
    assert route_by_command(command_state(label)) == destination


@pytest.mark.parametrize(
    "text",
    [
        "따라와",
        "여기서 기다려",
        "그만",
        "참호병 공격해",
        "내 옆으로 돌아와",
        "나무를 모아 줘",
        "나무 20개 캐 줘",
        "장작 좀 모아줘",
        "부싯돌 캐 줘",
        "뭘 캘까",
        "풀을 캐 줘",
        "철검 만드는 법을 알려 줘",
        "골리앗 약점이 뭐야?",
        "이 마을은 어떤 곳이야?",
        "안녕, 마코",
        "오늘 비가 올까?",
    ],
)
async def test_every_terminal_node_fills_display_text(text: str) -> None:
    final = await make_graph().ainvoke({"turn": make_turn(text), "text": text})

    assert final["display_text"]


@pytest.mark.parametrize(
    ("text", "expected_parameters"),
    [
        # 수량을 말했을 때만 수량이 흐른다.
        ("나무 20개 캐 줘", {"resource": "wood", "quantity": 20}),
        ("나무30개 캐줘", {"resource": "wood", "quantity": 30}),
        ("나무 30개만 캐놔줘", {"resource": "wood", "quantity": 30}),
        ("나무 30개 캐 놓아줘", {"resource": "wood", "quantity": 30}),
        ("나무 30개 캐둬", {"resource": "wood", "quantity": 30}),
        ("나무 30개 모아놔줘", {"resource": "wood", "quantity": 30}),
        ("바위 3개 캐 줘", {"resource": "stone", "quantity": 3}),
        # 수량 미명시는 실패가 아니다. 키를 비워 게임이 기본량을 정하게 한다.
        ("나무를 모아 줘", {"resource": "wood"}),
        ("장작 좀 모아줘", {"resource": "wood"}),
        ("돌 캐줘", {"resource": "stone"}),
    ],
)
async def test_gather_emits_action_with_resolved_parameters(
    text: str, expected_parameters: dict[str, object]
) -> None:
    # 수량·stone은 Mobile OfflineTask 경로의 기존 계약이다. Game은 아래 strict
    # 테스트에서 wood/no-quantity 첫 수직 슬라이스만 허용한다.
    final = await make_graph().ainvoke(
        {"turn": make_turn(text, surface=Surface.MOBILE), "text": text}
    )

    action = final["action"]
    assert action is not None
    assert action.type is CommandType.GATHER_RESOURCE
    assert action.parameters == expected_parameters


@pytest.mark.parametrize(
    "classification",
    [
        CommandClassification(
            command=CommandLabel.FOLLOW_PLAYER,
            resource=ResourceSlot.UNSPECIFIED,
            quantity=None,
        ),
        CommandClassification(
            command=CommandLabel.GATHER_RESOURCE,
            resource=ResourceSlot.WOOD,
            quantity=30,
        ),
        CommandClassification(
            command=CommandLabel.ATTACK,
            resource=ResourceSlot.UNSPECIFIED,
            quantity=None,
        ),
    ],
)
async def test_llm_semantic_command_is_still_blocked_by_capability_allowlist(
    classification: CommandClassification,
) -> None:
    final = await make_graph(ForcedCommandProvider(classification)).ainvoke(
        {
            "turn": make_turn("오늘 기분 어때?", allowed_actions=frozenset()),
            "text": "오늘 기분 어때?",
        }
    )

    assert final.get("action") is None


async def test_short_answer_to_companion_question_stays_in_conversation() -> None:
    text = "팔"
    final = await make_graph().ainvoke(
        {
            "turn": make_turn(text, surface=Surface.MOBILE),
            "text": text,
            "history": (
                ConversationTurn(speaker="player", text="나 잘렸어"),
                ConversationTurn(
                    speaker="companion",
                    text="어디가 어떻게 잘린 거야? 상태를 좀 더 자세히 말해줘.",
                ),
            ),
        }
    )

    assert final["top_intent"] is TopIntent.CONVERSATION
    assert final.get("action") is None
    assert final["display_text"] != SURFACE_PROFILES[Surface.MOBILE].unsupported.text


async def test_mobile_shoddy_bandage_request_emits_offline_craft_action() -> None:
    text = "엉성한붕대 3개 만들어놔줘"
    final = await make_graph().ainvoke(
        {
            "turn": make_turn(
                text,
                frozenset({CommandType.CRAFT_ITEM}),
                surface=Surface.MOBILE,
            ),
            "text": text,
        }
    )

    assert final["display_text"] == "좋아. 엉성한 붕대 3개 제작을 예약할게."
    assert final["action"] is not None
    assert final["action"].type is CommandType.CRAFT_ITEM
    assert final["action"].parameters == {"recipe_id": "recipe-1", "quantity": 3}


async def test_attached_bandage_alias_and_quantity_emit_offline_craft_action() -> None:
    text = "엉붕2개만 만들어줘"
    provider = NaturalRecipeProvider(
        RecipeSelection(decision="match", candidate_recipe_ids=("recipe-1",), confidence=95)
    )
    final = await make_graph(provider).ainvoke(
        {
            "turn": make_turn(
                text,
                frozenset({CommandType.CRAFT_ITEM}),
                surface=Surface.MOBILE,
            ),
            "text": text,
        }
    )

    assert final["display_text"] == "좋아. 엉성한 붕대 2개 제작을 예약할게."
    assert final["action"] is not None
    assert final["action"].parameters == {"recipe_id": "recipe-1", "quantity": 2}


async def test_item_name_in_greeting_does_not_force_the_recipe_route() -> None:
    text = "안녕 돌도끼?"
    final = await make_graph().ainvoke(
        {"turn": make_turn(text, surface=Surface.MOBILE), "text": text}
    )

    assert final["top_intent"] is TopIntent.CONVERSATION
    assert final["display_text"] == SURFACE_PROFILES[Surface.MOBILE].greeting
    assert final.get("repository_match") is not True


async def test_item_greeting_stays_conversation_when_llm_misclassifies_it() -> None:
    text = "안녕 돌도끼?"
    final = await make_graph(ForcedRecipeTopProvider()).ainvoke(
        {"turn": make_turn(text, surface=Surface.MOBILE), "text": text}
    )

    assert final["top_intent"] is TopIntent.CONVERSATION
    assert final["display_text"] == SURFACE_PROFILES[Surface.MOBILE].greeting


async def test_attached_bandage_alias_item_question_uses_verified_item_info() -> None:
    text = "엉붕이뭐야"
    provider = NaturalRecipeProvider(
        RecipeSelection(decision="match", candidate_recipe_ids=("recipe-1",), confidence=95)
    )
    final = await make_graph(provider).ainvoke(
        {"turn": make_turn(text, surface=Surface.MOBILE), "text": text}
    )

    assert final["top_intent"] is TopIntent.RECIPE
    assert final["display_text"] == "그건 엉성한 붕대를 뜻해. 확인된 제작 아이템이야."
    assert final.get("repository_match") is True
    assert final.get("action") is None


async def test_natural_language_bandage_alias_uses_validated_recipe_for_craft() -> None:
    provider = NaturalRecipeProvider(
        RecipeSelection(decision="match", candidate_recipe_ids=("recipe-1",), confidence=95)
    )
    text = "엉붕 만들어줘"
    final = await make_graph(provider).ainvoke(
        {
            "turn": make_turn(
                text,
                frozenset({CommandType.CRAFT_ITEM}),
                surface=Surface.MOBILE,
            ),
            "text": text,
        }
    )

    assert provider.recipe_inputs == [text]
    assert final["action"] is not None
    assert final["action"].type is CommandType.CRAFT_ITEM
    assert final["action"].parameters == {"recipe_id": "recipe-1", "quantity": 1}


async def test_natural_language_craft_rejects_an_unverified_recipe_selection() -> None:
    provider = NaturalRecipeProvider(
        RecipeSelection(decision="match", candidate_recipe_ids=("recipe-999",), confidence=100)
    )
    text = "엉붕 만들어줘"
    final = await make_graph(provider).ainvoke(
        {
            "turn": make_turn(
                text,
                frozenset({CommandType.CRAFT_ITEM}),
                surface=Surface.MOBILE,
            ),
            "text": text,
        }
    )

    assert final.get("action") is None


async def test_llm_command_family_is_validated_by_stable_capability() -> None:
    provider = ForcedCommandProvider(
        CommandClassification(
            command=CommandLabel.ATTACK,
            resource=ResourceSlot.UNSPECIFIED,
            quantity=None,
        )
    )
    final = await make_graph(provider).ainvoke(
        {
            "turn": make_turn("나무30개 캐줘", surface=Surface.MOBILE),
            "text": "나무30개 캐줘",
        }
    )

    assert final["action"].type is CommandType.ATTACK


class MemoryAnswerProvider(MockLLMProvider):
    async def classify_top(
        self,
        text: str,
        *,
        clarification_pending: bool,
        history: tuple[ConversationTurn, ...] = (),
    ) -> TopIntent:
        del text, clarification_pending, history
        return TopIntent.CONVERSATION

    async def generate_dialogue(self, spec: DialogueSpec) -> DialogueOutput:
        assert spec.memories == ("[M0] 제이름은 대통령 윤 석열입니다",)
        return DialogueOutput(
            text="네 이름은 대통령 윤 석열이야.",
            purpose="conversation",
            fact_references=(),
            memory_references=(0,),
            situation_references=(),
            accepts_command=False,
        )


class RecordingMemoryProvider(MockLLMProvider):
    def __init__(self) -> None:
        super().__init__()
        self.dialogue_specs: list[DialogueSpec] = []

    async def generate_dialogue(self, spec: DialogueSpec) -> DialogueOutput:
        self.dialogue_specs.append(spec)
        return await super().generate_dialogue(spec)


class RecalledMemoryStoreStub:
    def __init__(self) -> None:
        self.direct_recall_values: list[bool] = []
        self.used_memory_ids: list[str] = []

    async def recall(
        self,
        _scope: object,
        *,
        query: str,
        context_query: str = "",
        direct_recall: bool = False,
        source_mode: str | None,
        query_embedding: object | None = None,
        embedding_model: str | None = None,
        limit: int = 3,
    ) -> tuple[RecalledMemory, ...]:
        del context_query, source_mode, query_embedding, embedding_model, limit
        assert query == "출근시간은?"
        self.direct_recall_values.append(direct_recall)
        return (
            RecalledMemory(
                trace_id="M0",
                memory_id="memory-commute-time",
                text="출근시간은 9시 반이야",
                memory_type="ProfileFact",
                source_modes=("RealWorld",),
                occurred_at=datetime(2026, 8, 24, 2, 21, tzinfo=UTC),
                priority="Normal",
                required=direct_recall,
            ),
        )

    async def record_used(
        self, _scope: object, memory_ids: tuple[str, ...], _now: datetime
    ) -> None:
        self.used_memory_ids.extend(memory_ids)


async def test_specific_memory_question_uses_recalled_memory_as_required() -> None:
    provider = RecordingMemoryProvider()
    source_memory = RecalledMemoryStoreStub()
    brain = CompanionBrain(provider, source_memory=source_memory)  # type: ignore[arg-type]

    reply = await brain.respond(
        CompanionTurn(
            text="출근시간은?",
            conversation_key="memory-question",
            player_key="player-a",
            companion_id="mako",
            surface=Surface.MOBILE,
            allowed_actions=frozenset(CommandType),
            memory_scope=MemoryScope("profile-a", "slot-a", "mako"),
        )
    )

    assert source_memory.direct_recall_values == [True]
    assert provider.dialogue_specs[-1].memories[0].endswith("출근시간은 9시 반이야")
    assert provider.dialogue_specs[-1].memory_use_policy == "Required"
    assert reply.text == "기억하고 있어. 출근시간은 9시 반이야"
    assert reply.provenance is not None
    assert reply.provenance.query_mode == "MemoryRecall"


async def test_llm_can_answer_from_a_declared_long_term_memory_candidate() -> None:
    text = "내 이름은?"
    final = await make_graph(MemoryAnswerProvider()).ainvoke(
        {
            "turn": make_turn(text, surface=Surface.MOBILE),
            "text": text,
            "long_term": ("[M0] 제이름은 대통령 윤 석열입니다",),
        }
    )

    assert final["display_text"] == "네 이름은 대통령 윤 석열이야."
    assert final["top_intent"] is TopIntent.CONVERSATION


async def test_provider_cannot_answer_a_pending_resource_with_unrelated_text() -> None:
    brain = CompanionBrain(ForcedPendingProvider())
    await say(brain, "저것 좀 캐 줘", key="forced-pending")

    reply = await say(brain, "안녕", key="forced-pending")

    assert reply.action is None


@pytest.mark.parametrize(
    "text",
    [
        "나무를 모아 줘",
        "나무 좀 캐 줘",
        "장작 모아줘",
        "나무 캐줄래?",
        "나무 캐 줘?",
        "나무 캐쇼",
    ],
)
async def test_game_gather_emits_canonical_wood_parameters(text: str) -> None:
    final = await make_graph().ainvoke(
        {"turn": make_turn(text, surface=Surface.GAME), "text": text}
    )

    action = final["action"]
    assert action is not None
    assert action.type is CommandType.GATHER_RESOURCE
    assert action.parameters == {"resource": "wood"}


@pytest.mark.parametrize(
    "text",
    [
        "나무 1개 캐 줘",
        "나무 20개 캐 줘",
        "나무 1.5개 캐 줘",
        "나무 -1개 캐 줘",
        "나무 많이 캐 줘",
    ],
)
async def test_game_gather_rejects_any_quantity(text: str) -> None:
    final = await make_graph().ainvoke(
        {"turn": make_turn(text, surface=Surface.GAME), "text": text}
    )

    assert final["display_text"]
    assert final.get("action") is None


@pytest.mark.parametrize(
    "text",
    [
        "돌 캐 줘",
        "나무를 어떻게 캐?",
        "나무 캐는 방법 알려 줘",
        "나무 채집하는 법 알려 줘",
        "돌 캐는 방법 알려 줘",
    ],
)
async def test_game_gather_rejects_stone_and_questions(text: str) -> None:
    final = await make_graph().ainvoke(
        {"turn": make_turn(text, surface=Surface.GAME), "text": text}
    )

    assert final["display_text"]
    assert final.get("action") is None


@pytest.mark.parametrize(
    "text",
    [
        "저것 좀 캐 줘",  # 자원 미지정
        "부싯돌 캐 줘",  # 허용 목록 밖 자원
        "철광석을 캐 줘",  # 허용 목록 밖 자원
        "돌이랑 나무를 모아 줘",  # 자원을 여럿 말해 되물어야 한다
        f"나무 {MAX_GATHER_QUANTITY + 1}개 캐 줘",  # 상한 초과
    ],
)
async def test_gather_withholds_action_outside_supported_range(text: str) -> None:
    final = await make_graph().ainvoke({"turn": make_turn(text), "text": text})

    assert final["display_text"]
    assert final.get("action") is None


async def test_attack_resolves_target_from_the_utterance() -> None:
    text = "참호병 공격해"
    final = await make_graph().ainvoke({"turn": make_turn(text), "text": text})

    action = final["action"]
    assert action is not None
    assert action.type is CommandType.ATTACK
    assert action.parameters == {"target_id": "TrenchCrawler"}


async def test_attack_without_a_named_enemy_leaves_the_target_to_the_game() -> None:
    text = "공격해"
    final = await make_graph().ainvoke({"turn": make_turn(text), "text": text})

    action = final["action"]
    assert action is not None
    assert action.type is CommandType.ATTACK
    assert action.parameters == {}


async def test_return_to_player_emits_switch() -> None:
    text = "내 옆으로 돌아와"
    final = await make_graph().ainvoke({"turn": make_turn(text), "text": text})

    action = final["action"]
    assert action is not None
    assert action.type is CommandType.SWITCH


class GatherClassifyingProvider(MockLLMProvider):
    """허용 목록 게이트만 보기 위해 항상 유효한 채집 슬롯을 내는 공급자."""

    async def classify_command(self, text: str) -> CommandClassification:
        return CommandClassification(
            command=CommandLabel.GATHER_RESOURCE,
            resource=ResourceSlot.WOOD,
            quantity=10,
        )


@pytest.mark.parametrize(
    ("text", "llm"),
    [
        ("따라와", None),
        ("여기서 기다려", None),
        ("그만", None),
        ("참호병 공격해", None),
        ("내 옆으로 돌아와", None),
        ("나무 10개 캐 줘", GatherClassifyingProvider()),
    ],
)
async def test_disallowed_action_never_produces_an_acceptance_line(
    text: str, llm: LLMProvider | None
) -> None:
    """허용되지 않은 명령에 수락 대사를 내면 말만 하고 움직이지 않는 상태가 된다."""

    turn = make_turn(text, allowed_actions=frozenset())

    final = await make_graph(llm).ainvoke({"turn": turn, "text": text})

    assert final.get("action") is None
    assert final["display_text"] == "지금은 그렇게 해 줄 수 없어."


async def test_provider_failure_propagates_out_of_the_brain() -> None:
    """두뇌는 장애를 감추지 않는다. 서버용 오류로 옮기는 것은 어댑터의 몫이다."""

    brain = CompanionBrain(ExplodingLLMProvider())

    with pytest.raises(RuntimeError):
        await brain.respond(make_turn("따라와"))


async def say(
    brain: CompanionBrain,
    text: str,
    *,
    key: str = "conv-1",
    surface: Surface = Surface.GAME,
) -> CompanionReply:
    return await brain.respond(
        CompanionTurn(
            text=text,
            allowed_actions=frozenset(CommandType),
            conversation_key=key,
            surface=surface,
        )
    )


async def test_recipe_follow_up_uses_only_the_immediately_previous_detail() -> None:
    brain = CompanionBrain(MockLLMProvider())

    detail = await say(brain, "돌도끼 레시피 알려줘")
    follow_up = await say(brain, "그거 어떻게 만들어?")
    await say(brain, "안녕, 마코")
    expired = await say(brain, "그거 어떻게 만들어?")

    assert detail.provenance is not None
    assert detail.provenance.query_mode == "detail"
    assert follow_up.provenance is not None
    assert follow_up.provenance.query_mode == "detail"
    assert follow_up.text == detail.text
    assert expired.provenance is not None
    assert expired.provenance.query_mode == "ambiguous"
    assert expired.action is None


@pytest.mark.parametrize(
    "first",
    [
        "레시피 알고 있는 거 있어?",
        "돌도끼와 돌곡괭이 레시피를 비교해 줘",
        "전설검 레시피 알려줘",
    ],
)
async def test_non_detail_recipe_query_does_not_create_a_follow_up_reference(
    first: str,
) -> None:
    brain = CompanionBrain(MockLLMProvider())

    await say(brain, first)
    follow_up = await say(brain, "그거 어떻게 만들어?")

    assert follow_up.provenance is not None
    assert follow_up.provenance.query_mode == "ambiguous"
    assert follow_up.action is None


async def test_recipe_reference_is_separate_from_command_pending_slot() -> None:
    brain = CompanionBrain(MockLLMProvider())

    await say(brain, "돌도끼 레시피 알려줘")
    gather_question = await say(brain, "저것 좀 캐 줘")
    recipe_question = await say(brain, "그거 어떻게 만들어?")

    assert gather_question.action is None
    assert recipe_question.provenance is not None
    assert recipe_question.provenance.query_mode == "ambiguous"
    assert recipe_question.action is None


async def test_ambiguous_gather_answer_is_understood_on_the_next_turn() -> None:
    """되묻고 나서 그 답을 못 알아듣던 것이 이 기능의 출발점이다."""

    brain = CompanionBrain(MockLLMProvider())

    asked = await say(brain, "저것 좀 캐 줘")
    answered = await say(brain, "나무")

    assert asked.action is None
    assert answered.action is not None
    assert answered.action.type is CommandType.GATHER_RESOURCE
    assert answered.action.parameters == {"resource": "wood"}


async def test_quantity_survives_the_ask_back() -> None:
    """되물었다고 플레이어가 이미 말한 수량을 버리면 안 된다."""

    brain = CompanionBrain(MockLLMProvider())

    # 수량은 말했지만 자원은 지목하지 않아 되묻는 발화여야 한다.
    asked = await say(brain, "저것 20개 캐 줘", surface=Surface.MOBILE)
    answered = await say(brain, "나무", surface=Surface.MOBILE)

    assert asked.action is None
    assert answered.action is not None
    assert answered.action.parameters == {"resource": "wood", "quantity": 20}


async def test_changing_the_subject_drops_the_pending_slot() -> None:
    brain = CompanionBrain(MockLLMProvider())

    await say(brain, "저것 좀 캐 줘")
    changed = await say(brain, "안녕, 마코")
    # 슬롯을 버렸으니 다음 "나무" 는 다시 알아듣지 못해야 한다.
    afterwards = await say(brain, "나무")

    assert changed.action is None
    assert changed.text == SURFACE_PROFILES[Surface.GAME].greeting
    assert afterwards.action is None


async def test_a_new_request_does_not_inherit_the_old_slots_quantity() -> None:
    """되묻기를 방치한 채 새 채집을 요청하면 지난 수량이 따라오면 안 된다."""

    brain = CompanionBrain(MockLLMProvider())

    await say(brain, "저것 20개 캐 줘", surface=Surface.MOBILE)  # 되묻고 답하지 않은 채로 둔다
    await say(brain, "저것 좀 캐 줘", surface=Surface.MOBILE)  # 수량 없이 새로 요청한다
    answered = await say(brain, "나무", surface=Surface.MOBILE)

    assert answered.action is not None
    assert answered.action.parameters == {"resource": "wood"}


async def test_pending_does_not_leak_across_conversations() -> None:
    """다른 대화가 남의 되묻기를 이어받으면 안 된다."""

    brain = CompanionBrain(MockLLMProvider())

    await say(brain, "저것 좀 캐 줘", key="conv-1")
    other = await say(brain, "나무", key="conv-2")

    assert other.action is None


async def test_repeated_ambiguity_stops_asking_after_the_cap() -> None:
    """계속 모호하게 답해도 같은 질문을 무한히 되풀이하지 않는다."""

    brain = CompanionBrain(MockLLMProvider())

    replies = [await say(brain, "저것 좀 캐 줘")]
    for _ in range(MAX_ASK_COUNT):
        replies.append(await say(brain, "돌이랑 나무"))

    assert all(reply.action is None for reply in replies)
    # 상한에 도달한 마지막 응답은 되묻지 않고 가능한 범위를 알린다.
    assert replies[-1].text != "무엇을 캐면 될까?"


async def test_pending_is_ignored_once_it_expires() -> None:
    brain = CompanionBrain(
        MockLLMProvider(),
        store=InMemoryConversationStore(
            pending_ttl_seconds=0.0, idle_ttl_seconds=1800.0, max_entries=10
        ),
    )

    await say(brain, "저것 좀 캐 줘")
    answered = await say(brain, "나무")

    assert answered.action is None


async def test_gather_that_was_never_ambiguous_leaves_no_pending() -> None:
    """평범한 채집은 슬롯을 남기지 않는다. 남으면 다음 턴을 오해한다."""

    brain = CompanionBrain(MockLLMProvider())

    await say(brain, "나무 캐 줘")
    afterwards = await say(brain, "돌")

    assert afterwards.action is None


async def test_pending_state_never_reaches_a_disallowed_action() -> None:
    """되묻기로 자원이 확정돼도 허용 목록 밖이면 명령을 내면 안 된다."""

    brain = CompanionBrain(MockLLMProvider())

    await say(brain, "저것 좀 캐 줘")
    answered = await brain.respond(
        CompanionTurn(text="나무", allowed_actions=frozenset(), conversation_key="conv-1")
    )

    assert answered.action is None
    assert answered.text == "지금은 그렇게 해 줄 수 없어."


class RecordingProvider(MockLLMProvider):
    """분류기에 실제로 무엇이 들어갔는지, 대사 프롬프트에 무엇이 실렸는지 포착한다."""

    def __init__(self) -> None:
        self.classify_inputs: list[str] = []
        self.dialogue_specs: list[DialogueSpec] = []

    async def classify_top(
        self,
        text: str,
        *,
        clarification_pending: bool,
        history: tuple[ConversationTurn, ...] = (),
    ) -> TopIntent:
        self.classify_inputs.append(text)
        return await super().classify_top(
            text,
            clarification_pending=clarification_pending,
            history=history,
        )

    async def classify_command(self, text: str) -> CommandClassification:
        self.classify_inputs.append(text)
        return await super().classify_command(text)

    async def resolve_pending(self, text: str, pending: PendingSlot) -> ResourceSlot | None:
        self.classify_inputs.append(text)
        return await super().resolve_pending(text, pending)

    async def generate_dialogue(self, spec: DialogueSpec) -> DialogueOutput:
        self.dialogue_specs.append(spec)
        return await super().generate_dialogue(spec)


class NaturalRecipeProvider(RecordingProvider):
    def __init__(self, selection: RecipeSelection) -> None:
        super().__init__()
        self.selection = selection
        self.recipe_inputs: list[str] = []

    async def classify_top(
        self,
        text: str,
        *,
        clarification_pending: bool,
        history: tuple[ConversationTurn, ...] = (),
    ) -> TopIntent:
        del clarification_pending, history
        return (
            TopIntent.COMMAND
            if RecipeRepository().looks_like_craft_request(text)
            else TopIntent.RECIPE
        )

    async def resolve_recipe(
        self, text: str, options: tuple[RecipeSelectionOption, ...]
    ) -> RecipeSelection:
        assert options
        self.recipe_inputs.append(text)
        return self.selection


async def test_dialogue_candidate_flag_matches_the_emitted_action() -> None:
    accepted_provider = RecordingProvider()
    accepted = await say(CompanionBrain(accepted_provider), "따라와", key="candidate-accepted")

    assert accepted.action is not None
    assert accepted_provider.dialogue_specs[-1].command_candidate_present is True

    declined_provider = RecordingProvider()
    declined = await CompanionBrain(declined_provider).respond(
        make_turn("따라와", allowed_actions=frozenset())
    )

    assert declined.action is None
    assert declined_provider.dialogue_specs[-1].scene == "unsupported"
    assert declined_provider.dialogue_specs[-1].command_candidate_present is False

    information_provider = RecordingProvider()
    information = await say(
        CompanionBrain(information_provider),
        "참호병은 어떻게 잡아?",
        key="candidate-information",
    )

    assert information.action is None
    assert information_provider.dialogue_specs[-1].scene == "enemy"
    assert information_provider.dialogue_specs[-1].command_candidate_present is False


async def test_recipe_list_detail_and_compare_bypass_dialogue_generation() -> None:
    llm = RecordingProvider()
    brain = CompanionBrain(llm)

    listed = await say(brain, "레시피 알고 있는 거 있어?", key="recipe-list")
    detailed = await say(brain, "돌도끼 레시피 알려줘", key="recipe-detail")
    compared = await say(
        brain,
        "돌도끼와 돌곡괭이 레시피를 비교해 줘",
        key="recipe-compare",
    )

    assert llm.dialogue_specs == []
    assert "돌도끼" in listed.text
    copper_ingot = next(item.name_ko for item in ITEMS if item.item_id == "CopperIngot")
    assert copper_ingot in listed.text
    assert detailed.provenance is not None
    assert detailed.provenance.repository_match is True
    assert detailed.provenance.final_response_source == "game_repository"
    assert detailed.provenance.fact_ids == ("recipe:recipe-3",)
    assert compared.provenance is not None
    assert compared.provenance.repository_match is True
    assert compared.provenance.final_response_source == "game_repository"
    assert compared.provenance.fact_ids == ("recipe:recipe-3", "recipe:recipe-4")


async def test_natural_language_recipe_uses_only_the_validated_repository_fact() -> None:
    llm = NaturalRecipeProvider(
        RecipeSelection(decision="match", candidate_recipe_ids=("recipe-1",), confidence=95)
    )
    reply = await say(
        CompanionBrain(llm),
        "상처에 대충 감는 거 어떻게 만들지?",
        key="recipe-natural-language",
    )

    assert llm.recipe_inputs == ["상처에 대충 감는 거 어떻게 만들지?"]
    assert llm.dialogue_specs == []
    assert "엉성한 붕대" in reply.text
    assert reply.provenance is not None
    assert reply.provenance.repository_match is True
    assert reply.provenance.fact_ids == ("recipe:recipe-1",)


async def test_exact_recipe_alias_bypasses_natural_language_resolution() -> None:
    llm = NaturalRecipeProvider(
        RecipeSelection(decision="match", candidate_recipe_ids=("recipe-4",), confidence=100)
    )
    reply = await say(
        CompanionBrain(llm),
        "엉성한붕대 레시피 알려줘",
        key="recipe-exact-bypass",
    )

    assert llm.recipe_inputs == []
    assert "엉성한 붕대" in reply.text
    assert reply.provenance is not None
    assert reply.provenance.fact_ids == ("recipe:recipe-1",)


async def test_natural_language_recipe_rejects_an_invented_recipe_id() -> None:
    llm = NaturalRecipeProvider(
        RecipeSelection(decision="match", candidate_recipe_ids=("recipe-999",), confidence=100)
    )
    reply = await say(
        CompanionBrain(llm),
        "상처에 대충 감는 거 어떻게 만들지?",
        key="recipe-invented-id",
    )

    assert reply.text == ("확인된 제작법에 없는 대상이야. 이름이나 Recipe ID를 다시 확인해 줘.")
    assert reply.action is None
    assert reply.provenance is not None
    assert reply.provenance.repository_match is False


async def test_ambiguous_and_unknown_recipe_use_distinct_fixed_responses() -> None:
    llm = RecordingProvider()
    brain = CompanionBrain(llm)

    ambiguous = await say(brain, "그거 어떻게 만들어?", key="recipe-ambiguous")
    unknown = await say(brain, "전설검 레시피 알려줘", key="recipe-unknown")

    assert ambiguous.text == "어떤 제작법을 말하는지 대상 하나만 알려 줘."
    assert unknown.text == ("확인된 제작법에 없는 대상이야. 이름이나 Recipe ID를 다시 확인해 줘.")
    assert ambiguous.action is None
    assert unknown.action is None
    assert llm.dialogue_specs == []


async def test_game_time_reaches_dialogue_but_not_classifiers() -> None:
    llm = RecordingProvider()
    brain = CompanionBrain(llm)
    game_time = TimeContext(source=TimeSource.GAME_WORLD, day=7, hour=23, period="Night")

    await brain.respond(make_turn("안녕, 마코", game_time=game_time))

    assert llm.classify_inputs == ["안녕, 마코"]
    assert llm.dialogue_specs[-1].situation == ("지금은 게임 세계 기준 7일차 밤, 23시다.",)


async def test_history_accumulates_across_turns() -> None:
    llm = RecordingProvider()
    brain = CompanionBrain(llm)

    await say(brain, "안녕, 마코")
    await say(brain, "따라와")

    # 둘째 턴의 대사 프롬프트는 첫 왕복을 이미 알고 있어야 한다.
    history = llm.dialogue_specs[-1].history
    assert [(t.speaker, t.text) for t in history] == [
        ("player", "안녕, 마코"),
        ("companion", "오, 왔네! 오늘은 어디부터 가볼까?"),
    ]


async def test_history_never_reaches_the_classifiers() -> None:
    """기록이 분류에 끼면 세 턴 전 명령이 지금 다시 나갈 수 있다."""

    llm = RecordingProvider()
    brain = CompanionBrain(llm)

    await say(brain, "따라와")
    llm.classify_inputs.clear()
    await say(brain, "안녕, 마코")

    # 분류기는 현재 발화만 본다. 지난 턴의 문장이 섞이면 안 된다.
    assert llm.classify_inputs == ["안녕, 마코"]


async def test_history_does_not_change_routing_or_emitted_actions() -> None:
    """문맥이 쌓여도 명령 방출은 현재 발화만으로 결정되어야 한다."""

    brain = CompanionBrain(MockLLMProvider())
    for filler in ("안녕, 마코", "고마워", "오늘 비가 올까?"):
        await say(brain, filler)

    with_history = await say(brain, "나무 20개 캐 줘")
    fresh = await say(CompanionBrain(MockLLMProvider()), "나무 20개 캐 줘", key="conv-x")

    assert with_history.action == fresh.action


async def test_history_does_not_cross_conversations() -> None:
    llm = RecordingProvider()
    brain = CompanionBrain(llm)

    await say(brain, "안녕, 마코", key="conv-1")
    await say(brain, "따라와", key="conv-2")

    assert llm.dialogue_specs[-1].history == ()


async def test_history_is_never_written_to_logs() -> None:
    """대화 기록은 프롬프트에만 실리고 로그에는 남지 않아야 한다."""

    import logging

    records: list[logging.LogRecord] = []
    handler = logging.Handler()
    handler.emit = records.append  # type: ignore[method-assign]
    logger = logging.getLogger("aire.backend")
    previous_level = logger.level
    logger.setLevel(logging.INFO)
    logger.addHandler(handler)
    try:
        brain = CompanionBrain(TimingLLMProvider(MockLLMProvider()))
        await say(brain, "안녕, 마코")
        await say(brain, "따라와")
    finally:
        logger.removeHandler(handler)
        logger.setLevel(previous_level)

    dumped = " ".join(f"{record.__dict__}" for record in records)
    assert records, "스텝 로그 자체는 남아야 한다"
    for secret in ("안녕, 마코", "따라와", "오늘은 어디부터"):
        assert secret not in dumped


async def test_concurrent_turns_in_one_conversation_do_not_lose_memory() -> None:
    """load→그래프→save 사이에 다른 요청이 끼면 나중 저장이 앞선 저장을 덮어쓴다.

    HTTP 는 같은 대화로 병렬 요청을 받을 수 있다(WebSocket 만 프레임을 순차 처리한다).
    """

    import asyncio

    brain = CompanionBrain(MockLLMProvider())

    await asyncio.gather(
        say(brain, "안녕, 마코", key="conv-race"),
        say(brain, "고마워", key="conv-race"),
    )

    # 두 턴이 모두 남아야 한다. 유실되면 왕복 하나(2줄)만 남는다.
    assert len(brain._store.load("conv-race").recent_turns) == 4


async def test_concurrent_turns_do_not_leak_locks() -> None:
    import asyncio

    brain = CompanionBrain(MockLLMProvider())

    await asyncio.gather(*(say(brain, "안녕, 마코", key=f"conv-{n}") for n in range(5)))

    # 락은 처리 중인 요청에만 필요하다. 대화마다 남기면 상한이 없다.
    assert brain._locks == {}
    assert brain._lock_users == {}


def test_turn_rejects_an_empty_conversation_key() -> None:
    """키를 빠뜨린 호출자들이 한 항목을 공유하면 서로의 기억을 조용히 읽는다."""

    with pytest.raises(ValueError, match="conversation_key"):
        CompanionTurn(text="따라와", conversation_key="")


async def test_surface_reaches_the_dialogue_layer_but_no_classifier() -> None:
    """창구는 말투 축이다. 분류기가 읽으면 같은 말이 창구에 따라 다른 명령이 된다."""

    llm = RecordingProvider()
    turn = make_turn("안녕, 마코", surface=Surface.MOBILE)

    await make_graph(llm).ainvoke({"turn": turn, "text": turn.text})

    assert llm.dialogue_specs[-1].surface is Surface.MOBILE
    # 분류기는 텍스트만 받는다 — 창구를 넘겨받는 인자가 애초에 없다.
    assert llm.classify_inputs == ["안녕, 마코"]


@pytest.mark.parametrize(
    ("text", "llm"),
    [
        ("따라와", None),
        ("나무 10개 캐 줘", GatherClassifyingProvider()),
    ],
)
async def test_mobile_refusal_does_not_promise_a_game_action(
    text: str, llm: LLMProvider | None
) -> None:
    """게임 문구를 그대로 쓰면 폰에 없는 따라오기·대기를 할 수 있는 일로 안내하게 된다."""

    turn = make_turn(text, allowed_actions=frozenset(), surface=Surface.MOBILE)

    final = await make_graph(llm).ainvoke({"turn": turn, "text": text})

    assert final.get("action") is None
    assert final["display_text"] == SURFACE_PROFILES[Surface.MOBILE].not_allowed.text
    assert final["display_text"] != SURFACE_PROFILES[Surface.GAME].not_allowed.text


async def test_mobile_greeting_differs_from_the_game_greeting() -> None:
    """기본 공급자(mock)는 폴백을 그대로 낸다. 창구별 대사가 실제로 나오는지 확인한다."""

    turn = make_turn("안녕, 마코", surface=Surface.MOBILE)

    final = await make_graph().ainvoke({"turn": turn, "text": turn.text})

    assert final["display_text"] == SURFACE_PROFILES[Surface.MOBILE].greeting
    assert final["display_text"] != SURFACE_PROFILES[Surface.GAME].greeting


async def test_mobile_lore_miss_does_not_talk_about_a_current_location() -> None:
    turn = make_turn("이 마을 유래가 뭐야?", surface=Surface.MOBILE)

    final = await make_graph().ainvoke({"turn": turn, "text": turn.text})

    assert final["display_text"] == SURFACE_PROFILES[Surface.MOBILE].lore_missing.text
