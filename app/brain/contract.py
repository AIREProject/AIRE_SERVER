"""마코가 한 번 판단하는 데 필요한 값과 그 결과.

한 번의 판단에 필요한 것(`CompanionTurn`)과 그 결과(`CompanionReply`)만 정의하고,
요청 식별자·만료 시각 같은 전선 장부는 다루지 않는다. 장부는 `app/service.py` 가 채운다.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

from pydantic import JsonValue

from app.models import CommandType, Surface, TimeContext


@dataclass(frozen=True, slots=True)
class MemoryScope:
    """Authenticated canonical scope for source-backed memory retrieval."""

    profile_id: str
    save_slot_row_id: str
    companion_id: str


FallbackReason = Literal[
    "provider_timeout",
    "provider_unavailable",
    "invalid_structured_output",
    "empty_output",
    "sanitizer_rejection",
]
FinalResponseSource = Literal[
    "game_repository",
    "local_llm",
    "openai",
    "mock_fallback",
    "fixed_fallback",
    "validation_rejection",
]


@dataclass(frozen=True, slots=True)
class ProviderCallProvenance:
    step: str
    configured_provider: str
    effective_provider: str
    succeeded: bool
    fallback_used: bool
    fallback_reason: FallbackReason | None
    duration_ms: float


@dataclass(frozen=True, slots=True)
class BrainProvenance:
    top_intent: str | None
    query_mode: str | None
    selected_route: str
    repository_match: bool
    fact_ids: tuple[str, ...]
    provider_calls: tuple[ProviderCallProvenance, ...]
    effective_provider: str | None
    final_response_source: FinalResponseSource
    sanitizer_succeeded: bool | None
    final_fallback_reason: FallbackReason | None


@dataclass(frozen=True, slots=True)
class ResponseProvenance:
    request_id: str
    surface: str
    top_intent: str | None
    query_mode: str | None
    selected_route: str
    repository_match: bool
    fact_ids: tuple[str, ...]
    configured_provider: str
    effective_provider: str | None
    provider_call_succeeded: bool | None
    provider_fallback_used: bool
    final_fallback_reason: FallbackReason | None
    final_response_source: FinalResponseSource
    model_version: str
    prompt_version: str
    sanitizer_succeeded: bool | None
    duration_ms: float
    provider_calls: tuple[ProviderCallProvenance, ...]


@dataclass(frozen=True, slots=True)
class ThreatFacts:
    present: bool
    count: int
    nearest_kind: str | None


@dataclass(frozen=True, slots=True)
class ResourceFacts:
    kind: str
    count: int


@dataclass(frozen=True, slots=True)
class WorkFacts:
    type: str
    state: str


@dataclass(frozen=True, slots=True)
class InventoryItemFacts:
    item_id: str
    count: int


@dataclass(frozen=True, slots=True)
class InventoryFacts:
    container_id: str
    free_slots: int
    item_totals: tuple[InventoryItemFacts, ...]
    truncated: bool


@dataclass(frozen=True, slots=True)
class WorldContextFacts:
    is_available: bool = False
    location_id: str | None = None
    threat: ThreatFacts | None = None
    nearby_resources: tuple[ResourceFacts, ...] = ()
    available_workstations: tuple[str, ...] = ()
    current_work: WorkFacts | None = None
    inventories: tuple[InventoryFacts, ...] = ()


@dataclass(frozen=True, slots=True)
class CompanionTurn:
    """마코가 한 번 판단하는 데 필요한 전부.

    지금 라우팅이 실제로 쓰는 것만 담는다. 이 패키지가 소유한 타입이라 필요해지면 필드를
    더하는 데 바깥 계약을 고칠 필요가 없다.
    """

    text: str
    # 이 발화가 속한 대화. 어댑터가 주는 불투명한 값이며, 무엇에서 파생됐는지는 알지 못한다.
    # 턴 사이 기억을 찾는 데만 쓴다. **기본값을 주지 않는다** — 빠뜨린 호출자들이 빈 문자열
    # 항목 하나를 공유하면 서로의 대화 기억을 읽게 되고, 그것도 조용히 그렇게 된다.
    conversation_key: str
    # 이 발화를 한 사람. 대화가 아니라 **사람**을 가리키는 불투명한 값이라 세션이 바뀌어도
    # 같다. 장기기억(`memory.py`)을 찾는 데만 쓴다. `conversation_key` 와 달리 기본값을
    # 주되, **빈 문자열은 "이 호출자에게는 장기기억이 없다"** 는 뜻이다 — 빠뜨린 호출자가
    # 빈 문자열 항목 하나를 공유해 서로의 기억을 읽는 일이 원천적으로 일어나지 않는다.
    player_key: str = ""
    # 어느 컴패니언이 답하는지. 지금은 `app/brain/companions.py` 의 레지스트리에 "mako"
    # 하나만 있고, `service.py` 가 요청을 여기 넘기기 전에 멤버십을 검증한다 — 이 필드는
    # 배관일 뿐 아직 아무 노드도 값을 보고 갈라지지 않는다.
    companion_id: str = ""
    # 이 말이 어느 창구에서 왔는지. **말투만 고른다** — 무엇을 할 수 있는지는 아래
    # `allowed_actions` 가 정한다. 위의 두 키와 달리 기본값을 주는 이유: 빠뜨린 호출자가
    # 잃는 것은 어조뿐이라 기억이 새지 않는다. 조용히 게임 말투가 나올 뿐이다.
    surface: Surface = Surface.GAME
    # 게임이 지금 받을 수 있는 명령. 마코가 만들지 않는 것(교전, 지점 이동 등)이 섞여 있어도
    # 무해하다 — 라우팅은 이 집합을 순회하지 않고 `in` 의 우변으로만 쓴다.
    allowed_actions: frozenset[CommandType] = frozenset()
    world_context: WorldContextFacts = WorldContextFacts()
    game_time: TimeContext | None = None
    memory_scope: MemoryScope | None = None
    # Backend rules가 계산한 read-only presentation state다. 대사 어조에만 쓰며, 그래프와
    # Command 후보는 이 값으로 분기하지 않는다.
    relationship_state: Literal["Low", "Growing", "High"] = "Low"

    def __post_init__(self) -> None:
        if not self.conversation_key:
            raise ValueError("conversation_key must not be empty.")


@dataclass(frozen=True, slots=True)
class SituationTurn:
    """마코가 플레이어 발화 없이 먼저 말을 걸 때 필요한 전부.

    `CompanionTurn` 과 달리 `text`/`allowed_actions` 가 없다 — 무슨 상황인지는 클라이언트가
    코드로 이미 판단해 보냈으므로 다시 분류하지 않고, 명령도 내지 않는다(대사만).
    """

    # 클라이언트가 관찰한 상황을 자유 문장으로. 최소 한 줄.
    situation: tuple[str, ...]
    # `CompanionTurn.conversation_key` 와 같은 이유로 기본값을 주지 않는다.
    conversation_key: str
    player_key: str = ""
    companion_id: str = ""
    surface: Surface = Surface.GAME
    game_time: TimeContext | None = None
    memory_scope: MemoryScope | None = None
    relationship_state: Literal["Low", "Growing", "High"] = "Low"

    def __post_init__(self) -> None:
        if not self.conversation_key:
            raise ValueError("conversation_key must not be empty.")
        if not self.situation:
            raise ValueError("situation must not be empty.")


@dataclass(frozen=True, slots=True)
class CompanionAction:
    """마코가 하기로 정한 행동. 언제 만료되는지 같은 판단은 담기지 않는다."""

    type: CommandType
    parameters: dict[str, JsonValue] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class CompanionReply:
    """한 번의 판단 결과. 대사는 항상 있고, 행동은 낼 때만 있다."""

    text: str
    action: CompanionAction | None = None
    provenance: BrainProvenance | None = None
