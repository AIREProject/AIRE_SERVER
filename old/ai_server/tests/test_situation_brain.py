"""`CompanionBrain.react` 검증 — 상황 이벤트가 라우팅을 건너뛰고 대사만 만드는지,
`pending` 슬롯과 대화 기억이 규약대로 다뤄지는지 본다.

`test_companion_ai_service.py` 의 `RecordingProvider` 패턴을 그대로 쓴다.
"""

from datetime import UTC, datetime

from app.brain import CompanionBrain, SituationTurn
from app.brain.dialogue import SURFACE_PROFILES, DialogueSpec
from app.brain.llm import MockLLMProvider
from app.brain.store import ConversationMemory, InMemoryConversationStore, PendingSlot
from app.models import Surface, TimeContext, TimeSource


class RecordingProvider(MockLLMProvider):
    def __init__(self) -> None:
        self.dialogue_specs: list[DialogueSpec] = []

    async def generate_dialogue(self, spec: DialogueSpec) -> str:
        self.dialogue_specs.append(spec)
        return await super().generate_dialogue(spec)


def make_turn(
    situation: tuple[str, ...] = ("적이 나타났다",), **overrides: object
) -> SituationTurn:
    defaults: dict[str, object] = {
        "situation": situation,
        "conversation_key": "conv-1",
        "player_key": "",
        "surface": Surface.GAME,
    }
    defaults.update(overrides)
    return SituationTurn(**defaults)  # type: ignore[arg-type]


def make_pending(*, ask_count: int = 1) -> PendingSlot:
    return PendingSlot(
        kind="gather_resource", quantity=20, ask_count=ask_count, asked_at=datetime.now(UTC)
    )


async def test_react_skips_routing_and_uses_the_situation_scene() -> None:
    """`scene == "situation"`, `user_text` 없음, `facts` 없음 — 사실 저장소를 조회하지 않는다."""

    provider = RecordingProvider()
    brain = CompanionBrain(provider)

    text = await brain.react(make_turn(("적이 나타났다", "체력이 낮다")))

    assert text
    spec = provider.dialogue_specs[-1]
    assert spec.scene == "situation"
    assert spec.user_text is None
    assert spec.facts == ()
    assert spec.situation == ("적이 나타났다", "체력이 낮다")


async def test_react_appends_game_time_after_client_situation() -> None:
    provider = RecordingProvider()
    brain = CompanionBrain(provider)
    time_context = TimeContext(source=TimeSource.GAME_WORLD, day=2, hour=6, period="dawn")

    await brain.react(make_turn(("적이 나타났다",), game_time=time_context))

    spec = provider.dialogue_specs[-1]
    assert spec.situation == ("적이 나타났다", "지금은 게임 세계 기준 2일차 새벽, 6시다.")


async def test_react_falls_back_to_the_surface_situation_line_on_llm_failure() -> None:
    """MockLLMProvider 는 `spec.fallback` 을 그대로 돌려준다."""

    brain = CompanionBrain(MockLLMProvider())

    text = await brain.react(make_turn(surface=Surface.MOBILE))

    assert text == SURFACE_PROFILES[Surface.MOBILE].situation


async def test_react_does_not_clear_a_pending_ask_back_slot() -> None:
    """되묻기 도중 상황 이벤트가 끼어들어도 `pending` 이 살아남는다.

    `respond` 는 매 턴 `next_pending` 으로 슬롯을 덮어써 지우지만, 상황 이벤트는 플레이어의
    답이 아니므로 `react` 는 `pending` 을 아예 건드리지 않아야 한다.
    """

    store = InMemoryConversationStore(
        pending_ttl_seconds=120.0, idle_ttl_seconds=1800.0, max_entries=10
    )
    pending = make_pending()
    store.save("conv-1", ConversationMemory(pending=pending))
    brain = CompanionBrain(MockLLMProvider(), store=store)

    await brain.react(make_turn(conversation_key="conv-1"))

    assert store.load("conv-1").pending == pending


async def test_react_records_a_situation_speaker_turn_in_working_memory() -> None:
    """대화 기억에 `situation`/`companion` 두 화자가 쌓인다 — `player` 가 아니다."""

    store = InMemoryConversationStore(
        pending_ttl_seconds=120.0, idle_ttl_seconds=1800.0, max_entries=10
    )
    brain = CompanionBrain(MockLLMProvider(), store=store)

    await brain.react(make_turn(("적이 나타났다",), conversation_key="conv-1"))

    turns = store.load("conv-1").recent_turns
    assert [turn.speaker for turn in turns] == ["situation", "companion"]
    assert turns[0].text == "적이 나타났다"


async def test_react_reads_history_from_the_same_conversation_as_respond() -> None:
    """chat 과 같은 `conversation_key` 를 쓰면 `[최근 대화]` 를 이어받는다."""

    store = InMemoryConversationStore(
        pending_ttl_seconds=120.0, idle_ttl_seconds=1800.0, max_entries=10
    )
    memory = store.load("conv-1").appended("안녕", "안녕! 오늘은 어디부터 둘러볼까?")
    store.save("conv-1", memory)
    provider = RecordingProvider()
    brain = CompanionBrain(provider, store=store)

    await brain.react(make_turn(conversation_key="conv-1"))

    spec = provider.dialogue_specs[-1]
    assert len(spec.history) == 2
    assert spec.history[0].speaker == "player"
