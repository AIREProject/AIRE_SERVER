from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from collections.abc import Awaitable
from contextvars import ContextVar, Token
from dataclasses import dataclass
from time import perf_counter

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from app.models import Surface
from app.settings import Settings

from .command_intent import (
    CONVERSATION_PATTERN,
    ENEMY_PATTERN,
    LORE_PATTERN,
    RECIPE_PATTERN,
    CommandIntentParser,
)
from .contract import FallbackReason, ProviderCallProvenance
from .dialogue import SCENE_GUIDE, SURFACE_PROFILES, DialogueSpec
from .intent import (
    CommandClassification,
    CommandLabel,
    PendingResolution,
    ResourceSlot,
    TopClassification,
    TopIntent,
)
from .memory import (
    EMPTY_CONSOLIDATION,
    EMPTY_EXTRACTION,
    EMPTY_SUMMARY,
    MAX_MEMORY_TEXT,
    Consolidation,
    ConsolidationSpec,
    MemoryExtraction,
    MemoryExtractionSpec,
    SessionSummary,
    SessionSummarySpec,
)
from .resources import ResourceRepository
from .store import PendingSlot

_MAX_PROVIDER_CALLS = 8
_provider_calls_context: ContextVar[list[ProviderCallProvenance] | None] = ContextVar(
    "provider_calls", default=None
)
_provider_observation_suppressed: ContextVar[bool] = ContextVar(
    "provider_observation_suppressed", default=False
)


class _EmptyOutputError(ValueError):
    pass


def begin_provider_trace() -> Token[list[ProviderCallProvenance] | None]:
    return _provider_calls_context.set([])


def finish_provider_trace(
    token: Token[list[ProviderCallProvenance] | None],
) -> tuple[ProviderCallProvenance, ...]:
    calls = tuple(_provider_calls_context.get() or ())
    _provider_calls_context.reset(token)
    return calls


def _fallback_reason(error: Exception) -> FallbackReason:
    if isinstance(error, TimeoutError) or "timeout" in type(error).__name__.casefold():
        return "provider_timeout"
    if isinstance(error, _EmptyOutputError):
        return "empty_output"
    if isinstance(error, ValidationError):
        return "invalid_structured_output"
    return "provider_unavailable"


def _record_provider_call(
    *,
    step: str,
    configured_provider: str,
    effective_provider: str,
    succeeded: bool,
    fallback_used: bool,
    fallback_reason: FallbackReason | None,
    started_at: float,
) -> None:
    calls = _provider_calls_context.get()
    if calls is None or _provider_observation_suppressed.get() or len(calls) >= _MAX_PROVIDER_CALLS:
        return
    calls.append(
        ProviderCallProvenance(
            step=step,
            configured_provider=configured_provider,
            effective_provider=effective_provider,
            succeeded=succeeded,
            fallback_used=fallback_used,
            fallback_reason=fallback_reason,
            duration_ms=round((perf_counter() - started_at) * 1000, 3),
        )
    )


async def _without_provider_observation[T](awaitable: Awaitable[T]) -> T:
    token = _provider_observation_suppressed.set(True)
    try:
        return await awaitable
    finally:
        _provider_observation_suppressed.reset(token)

_TOP_ROUTER_PROMPT = """사용자의 한국어 발화를 다음 의도 중 정확히 하나로 분류한다.
- command: 따라오기, 대기, 작업 중지·취소, 자원 채집 요청, 적 공격, 플레이어 곁으로 복귀
- recipe: 아이템 제작법이나 재료 질문. 적을 상대하는 방법은 enemy다.
- enemy: 적의 약점, 공략법, 상대하는 방법을 **묻는 질문**. 공격하라는 명령 자체는 command다.
- lore: 장소의 역사, 유래, 세계관 질문
- conversation: 인사, 감사 등 가벼운 대화
- unknown: 위 범주에 속하지 않거나 판단할 수 없음
게임 사실이나 답변을 생성하지 말고 의도만 반환한다."""

_COMMAND_ROUTER_PROMPT = """사용자의 한국어 명령형 발화를 다음 명령 중 정확히 하나로 분류한다.
- follow_player: 사용자를 따라오라는 명령
- wait: 현재 위치에서 기다리거나 대기하라는 명령
- stop_current_task: 현재 수행 중인 작업을 멈추거나 직전 요청을 취소하라는 명령
- gather_resource: 자원을 모으거나 캐거나 가져오라는 명령
- attack: 적을 공격하라는 명령. "어떻게 잡아?" 처럼 방법을 묻는 질문은 이 명령이 아니다.
- return_to_player: 플레이어 곁으로 돌아오라는 명령
- unknown: 명령형이지만 위 명령으로 매핑할 수 없음

resource 는 채집 대상 자원을 다음 중 하나로 고른다. gather_resource 가 아니면 unspecified 로 둔다.
- wood: 나무, 목재, 장작, 통나무, 나뭇가지 등 목재 계열
- stone: 돌, 바위, 석재, 자갈 등 석재 계열
- other: 자원을 지목했지만 목재도 석재도 아님(철광석, 부싯돌 등)
- unspecified: 무엇을 캘지 지목하지 않음(저것, 뭔가 등)
서로 다른 자원을 함께 말하면('나무랑 돌') 하나를 고르지 말고 unspecified 로 둔다.

quantity 는 수량이 명시되면 정수로 옮긴다. 수량을 말하지 않았거나 '많이', '가방 찰 때까지'
처럼 정수로 옮길 수 없으면 반드시 null 로 둔다. 임의의 숫자를 지어내지 않는다.

답변을 생성하지 말고 분류 결과만 반환한다."""

_PENDING_RESOLVER_PROMPT = """직전에 동료가 '무엇을 캘지' 되물었고, 사용자가 방금 답했다.
이 발화가 그 질문에 대한 답인지 판정한다.

is_answer 는 발화가 캘 자원을 가리키면 true, 전혀 다른 주제로 넘어갔으면 false 로 둔다.
'나무', '나무로 해', '아까 그 돌' 처럼 자원을 지목하면 답이다.
'안녕', '됐어', '철검 어떻게 만들어?' 처럼 화제가 바뀌었으면 답이 아니다.

resource 는 답일 때만 의미가 있다. is_answer 가 false 면 unspecified 로 둔다.
- wood: 나무, 목재, 장작, 통나무, 나뭇가지 등 목재 계열
- stone: 돌, 바위, 석재, 자갈 등 석재 계열
- other: 자원을 지목했지만 목재도 석재도 아님(철광석, 부싯돌 등)
- unspecified: 답이지만 여전히 무엇인지 특정하지 못함('아무거나', '둘 다')

답변을 생성하지 말고 판정 결과만 반환한다."""

# 창구별로 갈리는 것은 `{tone}` 한 블록뿐이다. 사실 규칙과 기록 취급은 창구와 무관해서
# 여기 그대로 남는다 — 말투를 바꾸려다 사실 가드가 창구마다 달라지는 일이 없어야 한다.
_DIALOGUE_PROMPT_TEMPLATE = """너는 생존 게임의 AI 동료 마코다.
{tone}
[지시]가 요구하는 내용을 전달하되 문장은 매번 새로 만든다. 정해진 문구를 반복하지 않는다.
[확정 사실]에 적힌 내용만 사용하고, 없는 게임 정보·수치·아이템·장소를 절대 지어내지 않는다.
사실이 비어 있으면 사실 언급 없이 상황에만 반응한다.
[상황]은 지금 턴의 게임 배경이다. 물어보지 않으면 굳이 꺼내지 않고, 자연스러울 때만 스친다.
[최근 대화]는 흐름과 어조를 잇기 위한 참고일 뿐이다. 거기 적힌 게임 정보나 수치를 확정 사실로
삼지 않으며, 이미 한 말을 그대로 되풀이하지 않는다. 무엇을 말할지는 [지시]가 정한다.
[기억]은 예전 대화에서 알게 된 것이라 확정 사실이 아니다. 게임 정보나 수치의 근거로 삼지
않으며, 지금 말과 자연스럽게 이어질 때만 스치듯 쓰고 억지로 꺼내지 않는다.
되묻지 않는다(단, 지시가 되물으라고 하면 예외). 이모지와 따옴표를 쓰지 않는다."""

_DIALOGUE_PROMPTS: dict[Surface, str] = {
    surface: _DIALOGUE_PROMPT_TEMPLATE.format(tone=profile.tone)
    for surface, profile in SURFACE_PROFILES.items()
}

_MEMORY_RULES = f"""- 한국어 반말 서술체로 각 항목은 한 문장, {MAX_MEMORY_TEXT}자 이내.
- **숫자를 쓰지 않는다.** 수량이나 시간은 '조금', '여러 번' 처럼 말로 옮긴다.
- 제작법·재료·수량 같은 게임 정보는 기억하지 않는다. 그건 따로 검증된 자료가 있다.
- 확실하지 않으면 비워 둔다. 지어내는 것보다 기억하지 않는 편이 낫다."""

_MEMORY_EXTRACTOR_PROMPT = f"""너는 생존 게임 AI 동료 마코의 기억을 정리한다.
아직 정리하지 않은 대화 구간에서 **다음에 다시 만났을 때 알고 있어야 할 것**만 골라 낸다.

profile: 플레이어라는 사람에 대해 새로 알게 된 사실. 취향, 목표, 습관, 부르는 방식 등.
  최대 두 개. 새로 알게 된 것이 없으면 빈 배열로 둔다.
episode: 이 구간에서 기억에 남을 만한 사건 한 줄. 없으면 null.
episode_importance: episode 의 중요도. 1(사소함)부터 10(오래 기억할 일)까지의 정수.

규칙:
{_MEMORY_RULES}
- [이미 아는 것]에 있는 내용과 겹치면 다시 만들지 말고 비워 둔다."""

_SESSION_SUMMARY_PROMPT = f"""너는 생존 게임 AI 동료 마코의 기억을 정리한다.
방금 끝난 대화 **전체**를 한 줄로 요약한다. 마지막 몇 마디가 아니라 처음부터 끝까지 무슨
일이 있었는지가 남아야 한다.

summary: 대화 전체를 한 문장으로. 요약할 내용이 없으면 null.

규칙:
{_MEMORY_RULES}"""

_CONSOLIDATION_PROMPT = f"""너는 생존 게임 AI 동료 마코의 기억을 정리한다.
기억이 상한에 닿았다. **같은 이야기를 하는 줄들을 하나로 합쳐** 자리를 만든다.

번호가 붙은 기억 목록을 받는다. 합칠 것이 있는 묶음만 결과로 낸다.
- text: 합친 내용을 담은 새 한 줄.
- sources: 합친 원본의 번호들. 반드시 목록에 있는 번호여야 한다.

규칙:
{_MEMORY_RULES}
- 합칠 이유가 없는 줄은 결과에 넣지 않는다. 그대로 남는다.
- **내용을 버리지 않는다.** 합치는 것은 두 줄을 한 줄로 줄이는 일이지 하나를 고르는 일이
  아니다. 합쳐서 뜻이 달라질 것 같으면 그 묶음은 만들지 않는다.
- 합칠 것이 하나도 없으면 빈 배열로 둔다."""


_SPEAKER_LABELS = {"player": "플레이어", "companion": "마코", "situation": "상황"}


def _extraction_user_message(spec: MemoryExtractionSpec) -> str:
    """이미 아는 것을 먼저 보이고 그 다음에 이번 구간을 붙인다."""

    lines: list[str] = []
    if spec.known:
        lines.append("[이미 아는 것]")
        lines.extend(f"- {known}" for known in spec.known)
    lines.append("[대화]")
    lines.extend(f"{_SPEAKER_LABELS[turn.speaker]}: {turn.text}" for turn in spec.recent_turns)
    return "\n".join(lines)


def _summary_user_message(spec: SessionSummarySpec) -> str:
    lines = ["[대화]"]
    lines.extend(f"{_SPEAKER_LABELS[turn.speaker]}: {turn.text}" for turn in spec.turns)
    return "\n".join(lines)


def _consolidation_user_message(spec: ConsolidationSpec) -> str:
    """번호를 붙여 보인다. 그 번호가 그대로 `sources` 의 값이 된다."""

    lines = ["[기억]"]
    lines.extend(f"{index}. {text}" for index, text in enumerate(spec.memories))
    return "\n".join(lines)


def _dialogue_user_message(spec: DialogueSpec) -> str:
    """기억, 최근 대화, 장면 지시, 확정 사실을 조립한다. 이번 대사의 폴백은 넣지 않는다.

    기억과 최근 대화를 맨 앞에 둔다. 지시·사실·현재 발화가 뒤에 와야 무엇을 말할지는 [지시]가
    정하고 기록은 배경으로만 읽힌다. 기억은 그 기록보다도 더 오래된 배경이라 가장 앞이다.
    """

    lines: list[str] = []
    if spec.memories:
        lines.append("[기억]")
        lines.extend(f"- {memory}" for memory in spec.memories)
    if spec.situation:
        lines.append("[상황]")
        lines.extend(f"- {item}" for item in spec.situation)
    if spec.history:
        lines.append("[최근 대화]")
        lines.extend(
            f"{_SPEAKER_LABELS[turn.speaker]}: {turn.text}" for turn in spec.history
        )
    lines.append(f"[지시] {SCENE_GUIDE[spec.scene]}")
    if spec.facts:
        lines.append("[확정 사실]")
        lines.extend(f"- {fact}" for fact in spec.facts)
    else:
        lines.append("[확정 사실] 없음")
    if spec.user_text is not None:
        lines.append(f"[플레이어] {spec.user_text}")
    return "\n".join(lines)


class DialogueOutput(BaseModel):
    """LLM이 반드시 지켜야 하는 구조화된 출력 형식."""

    model_config = ConfigDict(extra="forbid")

    text: str = Field(min_length=1, max_length=200)


class LLMProvider(ABC):
    """의도 분류와 사실 기반 대사 생성을 제공하는 공통 인터페이스."""

    @abstractmethod
    async def classify_top(self, text: str, *, clarification_pending: bool) -> TopIntent:
        """사용자 발화를 최상위 처리 경로 중 하나로 분류한다."""

        raise NotImplementedError

    @abstractmethod
    async def classify_command(self, text: str) -> CommandClassification:
        """명령형 발화를 명령 계열과 채집 슬롯(자원·수량)으로 분류한다."""

        raise NotImplementedError

    @abstractmethod
    async def resolve_pending(self, text: str, pending: PendingSlot) -> ResourceSlot | None:
        """되물은 슬롯의 답이면 자원 슬롯을, 화제가 바뀌었으면 None 을 반환한다."""

        raise NotImplementedError

    @abstractmethod
    async def generate_dialogue(self, spec: DialogueSpec) -> str:
        """장면 지시와 코드가 확정한 사실을 자연스러운 대사로 옮긴다."""

        raise NotImplementedError

    @abstractmethod
    async def extract_memories(self, spec: MemoryExtractionSpec) -> MemoryExtraction:
        """아직 증류하지 않은 대화 구간에서 다음 세션까지 들고 갈 기억 후보를 뽑는다.

        턴 밖 백그라운드에서 호출된다. 실패하면 예외 대신 빈 추출을 돌려준다 — 기억을
        못 만드는 것은 대화의 실패가 아니다. 아래 둘도 같다.
        """

        raise NotImplementedError

    @abstractmethod
    async def summarize_session(self, spec: SessionSummarySpec) -> SessionSummary:
        """끝난 대화 전체를 한 줄로 요약한다. 대화당 한 번만 호출된다."""

        raise NotImplementedError

    @abstractmethod
    async def consolidate_memories(self, spec: ConsolidationSpec) -> Consolidation:
        """상한에 닿은 기억들 중 같은 이야기를 하는 것끼리 합친다."""

        raise NotImplementedError

    async def aclose(self) -> None:
        """공급자가 보유한 자원(HTTP 클라이언트 등)을 정리한다. 기본은 no-op."""

        return None


class MockLLMProvider(LLMProvider):
    """외부 API 없이 테스트와 로컬 개발에 사용하는 대화 공급자."""

    _resources = ResourceRepository()

    def __init__(
        self,
        *,
        configured_provider: str = "mock",
        fallback_reason: FallbackReason | None = None,
    ) -> None:
        self._configured_provider = configured_provider
        self._selection_fallback_reason = fallback_reason

    async def classify_top(self, text: str, *, clarification_pending: bool) -> TopIntent:
        """기존 정규식 우선순위를 재현하는 결정론적 분류를 수행한다."""

        started_at = perf_counter()
        del clarification_pending
        if (
            CommandIntentParser.classify_simple_command(text) is not None
            or CommandIntentParser.is_gather_command(text)
            or CommandIntentParser.is_attack_command(text)
        ):
            result = TopIntent.COMMAND
        elif ENEMY_PATTERN.search(text):
            result = TopIntent.ENEMY
        elif RECIPE_PATTERN.search(text):
            result = TopIntent.RECIPE
        elif LORE_PATTERN.search(text):
            result = TopIntent.LORE
        elif CONVERSATION_PATTERN.search(text):
            result = TopIntent.CONVERSATION
        else:
            result = TopIntent.UNKNOWN
        self._record_mock_call("classify_top", started_at)
        return result

    async def classify_command(self, text: str) -> CommandClassification:
        """기존 정규식 파서의 우선순위를 재현하는 결정론적 분류를 수행한다."""

        started_at = perf_counter()
        label = CommandIntentParser.classify_simple_command(text)
        if label is not None:
            result = CommandClassification(
                command=label, resource=ResourceSlot.UNSPECIFIED, quantity=None
            )
        elif CommandIntentParser.is_gather_command(text):
            resource, quantity = CommandIntentParser.resolve_gather(text)
            result = CommandClassification(
                command=CommandLabel.GATHER_RESOURCE, resource=resource, quantity=quantity
            )
        elif CommandIntentParser.is_attack_command(text):
            result = CommandClassification(
                command=CommandLabel.ATTACK, resource=ResourceSlot.UNSPECIFIED, quantity=None
            )
        else:
            result = CommandClassification(
                command=CommandLabel.UNKNOWN, resource=ResourceSlot.UNSPECIFIED, quantity=None
            )
        self._record_mock_call("classify_command", started_at)
        return result

    async def resolve_pending(self, text: str, pending: PendingSlot) -> ResourceSlot | None:
        """발화에서 지원 자원을 찾아 답 여부를 판정한다.

        실제 공급자보다 거칠다. 아는 자원이 하나도 안 보이면 화제가 바뀐 것으로 보는데,
        그래서 '부싯돌' 같은 미지원 자원 답변은 새 주제로 흘러가 일반 거절 대사를 받는다.
        Mock 은 결정론적 대체물이지 복제본이 아니므로 이 정도로 둔다.
        """

        started_at = perf_counter()
        del pending
        resources = self._resources.find_all(CommandIntentParser.normalize(text))
        if len(resources) == 1:
            result = ResourceSlot(resources[0].value)
        elif len(resources) > 1:
            # 여럿을 말했으면 답이긴 한데 여전히 고르지 못한 것이다.
            result = ResourceSlot.UNSPECIFIED
        else:
            result = None
        self._record_mock_call("resolve_pending", started_at)
        return result

    async def generate_dialogue(self, spec: DialogueSpec) -> str:
        """장면 폴백을 그대로 낸다.

        **폴백 말고 다른 문장을 만들지 않는다.** 규칙으로 흉내 낸 대사는 진짜 공급자가 내는
        것과 다르고, 창구가 늘면 그 흉내도 창구마다 갈라진다. 폴백을 유일한 출처로 두면
        창구별 대사는 `SURFACE_PROFILES` 한 곳에서만 정해진다.
        """

        started_at = perf_counter()
        self._record_mock_call("generate_dialogue", started_at)
        return spec.fallback

    def _record_mock_call(self, step: str, started_at: float) -> None:
        configured_provider = getattr(self, "_configured_provider", "mock")
        fallback_used = configured_provider != "mock"
        _record_provider_call(
            step=step,
            configured_provider=configured_provider,
            effective_provider="mock",
            succeeded=not fallback_used,
            fallback_used=fallback_used,
            fallback_reason=getattr(self, "_selection_fallback_reason", None),
            started_at=started_at,
        )

    async def extract_memories(self, spec: MemoryExtractionSpec) -> MemoryExtraction:
        """아무것도 기억하지 않는다.

        무엇이 기억할 만한지는 정규식으로 흉내 낼 수 있는 판단이 아니다. 억지로 규칙을
        만들면 실제 공급자와 전혀 다른 것을 저장하면서 그 사실이 드러나지 않는다.
        Mock 은 결정론적 대체물이지 복제본이 아니므로 빈 추출로 둔다 — 기본 설정에서는
        장기기억이 만들어지지 않고, 테스트는 명시적인 스텁 공급자를 쓴다.
        아래 요약·통합도 같은 이유로 빈 결과를 낸다.
        """

        del spec
        return EMPTY_EXTRACTION

    async def summarize_session(self, spec: SessionSummarySpec) -> SessionSummary:
        del spec
        return EMPTY_SUMMARY

    async def consolidate_memories(self, spec: ConsolidationSpec) -> Consolidation:
        del spec
        return EMPTY_CONSOLIDATION


class OpenAIProvider(LLMProvider):
    """OpenAI Responses API로 짧은 캐릭터 대화를 생성한다."""

    def __init__(self, config: Settings, fallback: LLMProvider | None = None) -> None:
        from openai import AsyncOpenAI

        if not config.openai_api_key:
            raise ValueError("OPENAI_API_KEY is required when LLM_PROVIDER=openai")
        self._client = AsyncOpenAI(
            api_key=config.openai_api_key, timeout=config.openai_timeout_seconds
        )
        self._model = config.openai_model
        self._classify_temperature = config.classify_temperature
        self._classify_max_tokens = config.classify_max_tokens
        self._dialogue_temperature = config.dialogue_temperature
        self._dialogue_max_tokens = config.dialogue_max_tokens
        self._memory_max_tokens = config.memory_extract_max_tokens
        self._summary_max_tokens = config.memory_summary_max_tokens
        self._consolidate_max_tokens = config.memory_consolidate_max_tokens
        self._fallback = fallback or MockLLMProvider()

    async def classify_top(self, text: str, *, clarification_pending: bool) -> TopIntent:
        """Responses API 구조화 출력으로 최상위 의도를 분류한다."""

        started_at = perf_counter()
        pending = "있음" if clarification_pending else "없음"
        prompt = f"{_TOP_ROUTER_PROMPT}\n미완성 선택: {pending}\n사용자: {text}"
        try:
            response = await self._client.responses.create(
                model=self._model,
                input=prompt,
                text={
                    "format": {
                        "type": "json_schema",
                        "name": "top_classification",
                        "strict": True,
                        "schema": TopClassification.model_json_schema(),
                    }
                },
                temperature=self._classify_temperature,
                max_output_tokens=self._classify_max_tokens,
                reasoning={"effort": "minimal"},
            )
            if not isinstance(response.output_text, str) or not response.output_text.strip():
                raise _EmptyOutputError
            result = TopClassification.model_validate_json(response.output_text).intent
            _record_provider_call(
                step="classify_top", configured_provider="openai", effective_provider="openai",
                succeeded=True, fallback_used=False, fallback_reason=None, started_at=started_at,
            )
            return result
        except Exception as error:
            result = await _without_provider_observation(
                self._fallback.classify_top(text, clarification_pending=clarification_pending)
            )
            _record_provider_call(
                step="classify_top", configured_provider="openai", effective_provider="mock",
                succeeded=False, fallback_used=True, fallback_reason=_fallback_reason(error),
                started_at=started_at,
            )
            return result

    async def classify_command(self, text: str) -> CommandClassification:
        """Responses API 구조화 출력으로 명령 계열과 채집 슬롯을 분류한다."""

        started_at = perf_counter()
        prompt = f"{_COMMAND_ROUTER_PROMPT}\n사용자: {text}"
        try:
            response = await self._client.responses.create(
                model=self._model,
                input=prompt,
                text={
                    "format": {
                        "type": "json_schema",
                        "name": "command_classification",
                        "strict": True,
                        "schema": CommandClassification.model_json_schema(),
                    }
                },
                temperature=self._classify_temperature,
                max_output_tokens=self._classify_max_tokens,
                reasoning={"effort": "minimal"},
            )
            if not isinstance(response.output_text, str) or not response.output_text.strip():
                raise _EmptyOutputError
            result = CommandClassification.model_validate_json(response.output_text)
            _record_provider_call(
                step="classify_command", configured_provider="openai", effective_provider="openai",
                succeeded=True, fallback_used=False, fallback_reason=None, started_at=started_at,
            )
            return result
        except Exception as error:
            result = await _without_provider_observation(self._fallback.classify_command(text))
            _record_provider_call(
                step="classify_command", configured_provider="openai", effective_provider="mock",
                succeeded=False, fallback_used=True, fallback_reason=_fallback_reason(error),
                started_at=started_at,
            )
            return result

    async def resolve_pending(self, text: str, pending: PendingSlot) -> ResourceSlot | None:
        """Responses API 구조화 출력으로 되묻기 답 여부와 자원을 판정한다."""

        started_at = perf_counter()
        prompt = f"{_PENDING_RESOLVER_PROMPT}\n사용자: {text}"
        try:
            response = await self._client.responses.create(
                model=self._model,
                input=prompt,
                text={
                    "format": {
                        "type": "json_schema",
                        "name": "pending_resolution",
                        "strict": True,
                        "schema": PendingResolution.model_json_schema(),
                    }
                },
                temperature=self._classify_temperature,
                max_output_tokens=self._classify_max_tokens,
                reasoning={"effort": "minimal"},
            )
            if not isinstance(response.output_text, str) or not response.output_text.strip():
                raise _EmptyOutputError
            resolution = PendingResolution.model_validate_json(response.output_text)
            result = resolution.resource if resolution.is_answer else None
            _record_provider_call(
                step="resolve_pending", configured_provider="openai", effective_provider="openai",
                succeeded=True, fallback_used=False, fallback_reason=None, started_at=started_at,
            )
            return result
        except Exception as error:
            result = await _without_provider_observation(
                self._fallback.resolve_pending(text, pending)
            )
            _record_provider_call(
                step="resolve_pending", configured_provider="openai", effective_provider="mock",
                succeeded=False, fallback_used=True, fallback_reason=_fallback_reason(error),
                started_at=started_at,
            )
            return result

    async def generate_dialogue(self, spec: DialogueSpec) -> str:
        """구조화된 사실 기반 대사를 생성하고 실패하면 장면 폴백으로 복구한다."""

        started_at = perf_counter()
        try:
            response = await self._client.responses.create(
                model=self._model,
                input=[
                    {"role": "system", "content": _DIALOGUE_PROMPTS[spec.surface]},
                    {"role": "user", "content": _dialogue_user_message(spec)},
                ],
                text={
                    "format": {
                        "type": "json_schema",
                        "name": "dialogue_output",
                        "strict": True,
                        "schema": DialogueOutput.model_json_schema(),
                    }
                },
                temperature=self._dialogue_temperature,
                max_output_tokens=self._dialogue_max_tokens,
                reasoning={"effort": "minimal"},
            )
            if not isinstance(response.output_text, str) or not response.output_text.strip():
                raise _EmptyOutputError
            result = DialogueOutput.model_validate_json(response.output_text).text
            _record_provider_call(
                step="generate_dialogue", configured_provider="openai", effective_provider="openai",
                succeeded=True, fallback_used=False, fallback_reason=None, started_at=started_at,
            )
            return result
        except Exception as error:
            # 네트워크, 인증, 응답 검증 오류가 사용자 요청 전체를 실패시키지 않게 한다.
            result = await _without_provider_observation(self._fallback.generate_dialogue(spec))
            _record_provider_call(
                step="generate_dialogue", configured_provider="openai", effective_provider="mock",
                succeeded=False, fallback_used=True, fallback_reason=_fallback_reason(error),
                started_at=started_at,
            )
            return result

    async def extract_memories(self, spec: MemoryExtractionSpec) -> MemoryExtraction:
        """Responses API 구조화 출력으로 기억 후보를 뽑는다."""

        try:
            response = await self._client.responses.create(
                model=self._model,
                input=[
                    {"role": "system", "content": _MEMORY_EXTRACTOR_PROMPT},
                    {"role": "user", "content": _extraction_user_message(spec)},
                ],
                text={
                    "format": {
                        "type": "json_schema",
                        "name": "memory_extraction",
                        "strict": True,
                        "schema": MemoryExtraction.model_json_schema(),
                    }
                },
                temperature=self._classify_temperature,
                max_output_tokens=self._memory_max_tokens,
                reasoning={"effort": "minimal"},
            )
            return MemoryExtraction.model_validate_json(response.output_text)
        except Exception:
            return await self._fallback.extract_memories(spec)

    async def summarize_session(self, spec: SessionSummarySpec) -> SessionSummary:
        """Responses API 구조화 출력으로 대화 전체를 한 줄로 줄인다."""

        try:
            response = await self._client.responses.create(
                model=self._model,
                input=[
                    {"role": "system", "content": _SESSION_SUMMARY_PROMPT},
                    {"role": "user", "content": _summary_user_message(spec)},
                ],
                text={
                    "format": {
                        "type": "json_schema",
                        "name": "session_summary",
                        "strict": True,
                        "schema": SessionSummary.model_json_schema(),
                    }
                },
                temperature=self._classify_temperature,
                max_output_tokens=self._summary_max_tokens,
                reasoning={"effort": "minimal"},
            )
            return SessionSummary.model_validate_json(response.output_text)
        except Exception:
            return await self._fallback.summarize_session(spec)

    async def consolidate_memories(self, spec: ConsolidationSpec) -> Consolidation:
        """Responses API 구조화 출력으로 겹치는 기억을 합친다."""

        try:
            response = await self._client.responses.create(
                model=self._model,
                input=[
                    {"role": "system", "content": _CONSOLIDATION_PROMPT},
                    {"role": "user", "content": _consolidation_user_message(spec)},
                ],
                text={
                    "format": {
                        "type": "json_schema",
                        "name": "consolidation",
                        "strict": True,
                        "schema": Consolidation.model_json_schema(),
                    }
                },
                temperature=self._classify_temperature,
                max_output_tokens=self._consolidate_max_tokens,
                reasoning={"effort": "minimal"},
            )
            return Consolidation.model_validate_json(response.output_text)
        except Exception:
            return await self._fallback.consolidate_memories(spec)

    async def aclose(self) -> None:
        await self._client.close()


class LocalLLMProvider(LLMProvider):
    """OpenAI 호환 Chat Completions API를 제공하는 로컬 LLM을 호출한다."""

    def __init__(self, config: Settings, fallback: LLMProvider | None = None) -> None:
        from openai import AsyncOpenAI

        if not config.local_llm_api_key:
            raise ValueError("LOCAL_LLM_API_KEY is required when LLM_PROVIDER=local")
        self._client = AsyncOpenAI(
            base_url=config.local_llm_base_url,
            api_key=config.local_llm_api_key,
            timeout=config.local_llm_timeout_seconds,
        )
        self._model = config.local_llm_model
        self._classify_temperature = config.classify_temperature
        self._classify_max_tokens = config.classify_max_tokens
        self._dialogue_temperature = config.dialogue_temperature
        self._dialogue_max_tokens = config.dialogue_max_tokens
        self._memory_max_tokens = config.memory_extract_max_tokens
        self._summary_max_tokens = config.memory_summary_max_tokens
        self._consolidate_max_tokens = config.memory_consolidate_max_tokens
        self._fallback = fallback or MockLLMProvider()

    async def classify_top(self, text: str, *, clarification_pending: bool) -> TopIntent:
        """로컬 모델의 JSON Schema 출력으로 최상위 의도를 분류한다."""

        started_at = perf_counter()
        pending = "있음" if clarification_pending else "없음"
        try:
            response = await self._client.chat.completions.create(
                model=self._model,
                messages=[
                    {"role": "system", "content": _TOP_ROUTER_PROMPT},
                    {"role": "user", "content": f"미완성 선택: {pending}\n사용자: {text}"},
                ],
                response_format={
                    "type": "json_schema",
                    "json_schema": {
                        "name": "top_classification",
                        "strict": True,
                        "schema": TopClassification.model_json_schema(),
                    },
                },
                temperature=self._classify_temperature,
                max_tokens=self._classify_max_tokens,
                extra_body={"chat_template_kwargs": {"enable_thinking": False}},
            )
            content = response.choices[0].message.content
            if content is None or not content.strip():
                raise _EmptyOutputError
            result = TopClassification.model_validate_json(content).intent
            _record_provider_call(
                step="classify_top", configured_provider="local", effective_provider="local",
                succeeded=True, fallback_used=False, fallback_reason=None, started_at=started_at,
            )
            return result
        except Exception as error:
            result = await _without_provider_observation(
                self._fallback.classify_top(text, clarification_pending=clarification_pending)
            )
            _record_provider_call(
                step="classify_top", configured_provider="local", effective_provider="mock",
                succeeded=False, fallback_used=True, fallback_reason=_fallback_reason(error),
                started_at=started_at,
            )
            return result

    async def classify_command(self, text: str) -> CommandClassification:
        """로컬 모델의 JSON Schema 출력으로 명령 계열과 채집 슬롯을 분류한다."""

        started_at = perf_counter()
        try:
            response = await self._client.chat.completions.create(
                model=self._model,
                messages=[
                    {"role": "system", "content": _COMMAND_ROUTER_PROMPT},
                    {"role": "user", "content": text},
                ],
                response_format={
                    "type": "json_schema",
                    "json_schema": {
                        "name": "command_classification",
                        "strict": True,
                        "schema": CommandClassification.model_json_schema(),
                    },
                },
                temperature=self._classify_temperature,
                max_tokens=self._classify_max_tokens,
                extra_body={"chat_template_kwargs": {"enable_thinking": False}},
            )
            content = response.choices[0].message.content
            if content is None or not content.strip():
                raise _EmptyOutputError
            result = CommandClassification.model_validate_json(content)
            _record_provider_call(
                step="classify_command", configured_provider="local", effective_provider="local",
                succeeded=True, fallback_used=False, fallback_reason=None, started_at=started_at,
            )
            return result
        except Exception as error:
            result = await _without_provider_observation(self._fallback.classify_command(text))
            _record_provider_call(
                step="classify_command", configured_provider="local", effective_provider="mock",
                succeeded=False, fallback_used=True, fallback_reason=_fallback_reason(error),
                started_at=started_at,
            )
            return result

    async def resolve_pending(self, text: str, pending: PendingSlot) -> ResourceSlot | None:
        """로컬 모델의 JSON Schema 출력으로 되묻기 답 여부와 자원을 판정한다."""

        started_at = perf_counter()
        try:
            response = await self._client.chat.completions.create(
                model=self._model,
                messages=[
                    {"role": "system", "content": _PENDING_RESOLVER_PROMPT},
                    {"role": "user", "content": text},
                ],
                response_format={
                    "type": "json_schema",
                    "json_schema": {
                        "name": "pending_resolution",
                        "strict": True,
                        "schema": PendingResolution.model_json_schema(),
                    },
                },
                temperature=self._classify_temperature,
                max_tokens=self._classify_max_tokens,
                extra_body={"chat_template_kwargs": {"enable_thinking": False}},
            )
            content = response.choices[0].message.content
            if content is None or not content.strip():
                raise _EmptyOutputError
            resolution = PendingResolution.model_validate_json(content)
            result = resolution.resource if resolution.is_answer else None
            _record_provider_call(
                step="resolve_pending", configured_provider="local", effective_provider="local",
                succeeded=True, fallback_used=False, fallback_reason=None, started_at=started_at,
            )
            return result
        except Exception as error:
            result = await _without_provider_observation(
                self._fallback.resolve_pending(text, pending)
            )
            _record_provider_call(
                step="resolve_pending", configured_provider="local", effective_provider="mock",
                succeeded=False, fallback_used=True, fallback_reason=_fallback_reason(error),
                started_at=started_at,
            )
            return result

    async def generate_dialogue(self, spec: DialogueSpec) -> str:
        """로컬 모델의 사실 기반 대사를 반환하고 실패하면 장면 폴백으로 복구한다."""

        started_at = perf_counter()
        try:
            response = await self._client.chat.completions.create(
                model=self._model,
                messages=[
                    {"role": "system", "content": _DIALOGUE_PROMPTS[spec.surface]},
                    {"role": "user", "content": _dialogue_user_message(spec)},
                ],
                temperature=self._dialogue_temperature,
                max_tokens=self._dialogue_max_tokens,
                extra_body={"chat_template_kwargs": {"enable_thinking": False}},
            )
            content = response.choices[0].message.content
            if content is None or not content.strip():
                raise _EmptyOutputError
            result = DialogueOutput(text=content.strip()).text
            _record_provider_call(
                step="generate_dialogue", configured_provider="local", effective_provider="local",
                succeeded=True, fallback_used=False, fallback_reason=None, started_at=started_at,
            )
            return result
        except Exception as error:
            # 로컬 서버 중단이나 호환되지 않는 응답이 API 요청 전체를 실패시키지 않게 한다.
            result = await _without_provider_observation(self._fallback.generate_dialogue(spec))
            _record_provider_call(
                step="generate_dialogue", configured_provider="local", effective_provider="mock",
                succeeded=False, fallback_used=True, fallback_reason=_fallback_reason(error),
                started_at=started_at,
            )
            return result

    async def extract_memories(self, spec: MemoryExtractionSpec) -> MemoryExtraction:
        """로컬 모델의 JSON Schema 출력으로 기억 후보를 뽑는다."""

        try:
            response = await self._client.chat.completions.create(
                model=self._model,
                messages=[
                    {"role": "system", "content": _MEMORY_EXTRACTOR_PROMPT},
                    {"role": "user", "content": _extraction_user_message(spec)},
                ],
                response_format={
                    "type": "json_schema",
                    "json_schema": {
                        "name": "memory_extraction",
                        "strict": True,
                        "schema": MemoryExtraction.model_json_schema(),
                    },
                },
                temperature=self._classify_temperature,
                max_tokens=self._memory_max_tokens,
                extra_body={"chat_template_kwargs": {"enable_thinking": False}},
            )
            content = response.choices[0].message.content
            if content is None:
                raise ValueError("Local LLM returned no memory extraction content")
            return MemoryExtraction.model_validate_json(content)
        except Exception:
            return await self._fallback.extract_memories(spec)

    async def summarize_session(self, spec: SessionSummarySpec) -> SessionSummary:
        """로컬 모델의 JSON Schema 출력으로 대화 전체를 한 줄로 줄인다."""

        try:
            response = await self._client.chat.completions.create(
                model=self._model,
                messages=[
                    {"role": "system", "content": _SESSION_SUMMARY_PROMPT},
                    {"role": "user", "content": _summary_user_message(spec)},
                ],
                response_format={
                    "type": "json_schema",
                    "json_schema": {
                        "name": "session_summary",
                        "strict": True,
                        "schema": SessionSummary.model_json_schema(),
                    },
                },
                temperature=self._classify_temperature,
                max_tokens=self._summary_max_tokens,
                extra_body={"chat_template_kwargs": {"enable_thinking": False}},
            )
            content = response.choices[0].message.content
            if content is None:
                raise ValueError("Local LLM returned no session summary content")
            return SessionSummary.model_validate_json(content)
        except Exception:
            return await self._fallback.summarize_session(spec)

    async def consolidate_memories(self, spec: ConsolidationSpec) -> Consolidation:
        """로컬 모델의 JSON Schema 출력으로 겹치는 기억을 합친다."""

        try:
            response = await self._client.chat.completions.create(
                model=self._model,
                messages=[
                    {"role": "system", "content": _CONSOLIDATION_PROMPT},
                    {"role": "user", "content": _consolidation_user_message(spec)},
                ],
                response_format={
                    "type": "json_schema",
                    "json_schema": {
                        "name": "consolidation",
                        "strict": True,
                        "schema": Consolidation.model_json_schema(),
                    },
                },
                temperature=self._classify_temperature,
                max_tokens=self._consolidate_max_tokens,
                extra_body={"chat_template_kwargs": {"enable_thinking": False}},
            )
            content = response.choices[0].message.content
            if content is None:
                raise ValueError("Local LLM returned no consolidation content")
            return Consolidation.model_validate_json(content)
        except Exception:
            return await self._fallback.consolidate_memories(spec)

    async def aclose(self) -> None:
        await self._client.close()


_logger = logging.getLogger("aire.backend")


class TimingLLMProvider(LLMProvider):
    """감싼 공급자의 LLM 스텝별 지연 시간을 구조화 로그(`duration_ms`)로 남긴다.

    스텝1·2 라우터, 되묻기 해소, 대사 생성이 각각 `LLMProvider` 메서드 하나에 대응하므로,
    노드마다 흩어 재는 대신 이 경계 한 곳에서 mock/openai/local 모두를 계측한다.
    한 턴에 모든 스텝이 도는 것은 아니다. 되묻기 답으로 풀린 턴은 `resolve_pending` 이
    스텝1·2를 대신하므로 로그에 라우터가 찍히지 않는다. `extract_memories`,
    `summarize_session`, `consolidate_memories` 는 턴이 아니라 백그라운드 루프에서 도므로
    그 시간은 어떤 요청의 지연에도 포함되지 않는다.
    실제 공급자 내부 폴백(예: OpenAI 실패 → mock)이 발생하면 그 시간까지 포함된다.
    """

    def __init__(self, inner: LLMProvider) -> None:
        self._inner = inner

    async def classify_top(self, text: str, *, clarification_pending: bool) -> TopIntent:
        started_at = perf_counter()
        try:
            return await self._inner.classify_top(
                text, clarification_pending=clarification_pending
            )
        finally:
            _log_step("classify_top", started_at)

    async def classify_command(self, text: str) -> CommandClassification:
        started_at = perf_counter()
        try:
            return await self._inner.classify_command(text)
        finally:
            _log_step("classify_command", started_at)

    async def resolve_pending(self, text: str, pending: PendingSlot) -> ResourceSlot | None:
        started_at = perf_counter()
        try:
            return await self._inner.resolve_pending(text, pending)
        finally:
            _log_step("resolve_pending", started_at)

    async def generate_dialogue(self, spec: DialogueSpec) -> str:
        started_at = perf_counter()
        try:
            return await self._inner.generate_dialogue(spec)
        finally:
            _log_step("generate_dialogue", started_at)

    async def extract_memories(self, spec: MemoryExtractionSpec) -> MemoryExtraction:
        started_at = perf_counter()
        try:
            return await self._inner.extract_memories(spec)
        finally:
            _log_step("extract_memories", started_at)

    async def summarize_session(self, spec: SessionSummarySpec) -> SessionSummary:
        started_at = perf_counter()
        try:
            return await self._inner.summarize_session(spec)
        finally:
            _log_step("summarize_session", started_at)

    async def consolidate_memories(self, spec: ConsolidationSpec) -> Consolidation:
        started_at = perf_counter()
        try:
            return await self._inner.consolidate_memories(spec)
        finally:
            _log_step("consolidate_memories", started_at)

    async def aclose(self) -> None:
        await self._inner.aclose()


def _log_step(step: str, started_at: float) -> None:
    """스텝 이름과 경과 시간을 request_context 미들웨어와 같은 규약으로 기록한다."""

    _logger.info(
        "llm_step",
        extra={
            "event": "llm_step",
            "step": step,
            "duration_ms": round((perf_counter() - started_at) * 1000, 3),
        },
    )


@dataclass(frozen=True, slots=True)
class SelectedProvider:
    """선택된 공급자와, 그 선택이 무엇이었는지 보고할 이름·모델.

    이름과 모델을 함께 돌려주는 이유: 호출자가 메타데이터를 채우려고 선택 규칙을 다시
    구현하면 두 곳이 어긋날 때 메타데이터가 조용히 거짓말한다. 규칙은 한 곳에만 둔다.
    """

    provider: LLMProvider
    configured_name: str
    name: str
    model_version: str


def build_llm_provider(config: Settings) -> SelectedProvider:
    """설정과 API 키 유무에 따라 실제 또는 mock 공급자를 선택한다."""

    provider = config.llm_provider.casefold()
    if provider == "local" and config.local_llm_api_key:
        inner: LLMProvider = LocalLLMProvider(config)
        name, model_version = "local", config.local_llm_model
    elif provider == "openai" and config.openai_api_key:
        inner = OpenAIProvider(config)
        name, model_version = "openai", config.openai_model
    else:
        selection_reason: FallbackReason | None = (
            "provider_unavailable" if provider != "mock" else None
        )
        inner = MockLLMProvider(
            configured_provider=provider,
            fallback_reason=selection_reason,
        )
        name, model_version = "mock", "mock-v1"
    return SelectedProvider(
        provider=TimingLLMProvider(inner) if config.llm_step_timing else inner,
        configured_name=provider,
        name=name,
        model_version=model_version,
    )
