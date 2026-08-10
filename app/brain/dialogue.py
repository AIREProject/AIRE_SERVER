from __future__ import annotations

import re
from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal, NamedTuple

from app.models import Surface

from .store import ConversationTurn

if TYPE_CHECKING:
    from .llm import LLMProvider

DialogueScene = Literal[
    "follow_player",
    "wait",
    "stop_current_task",
    "cancel",
    "gather_wood",
    "gather_stone",
    "gather_ambiguous",
    "attack",
    "return_to_player",
    "recipe",
    "enemy",
    "lore",
    "conversation",
    "unsupported",
    "event_completed",
    "event_failed",
    "situation",
]


@dataclass(frozen=True, slots=True)
class DialogueSpec:
    """LLM에 전달할 장면과 코드가 확정한 사실, 실패 시 복구 대사를 묶는다."""

    scene: DialogueScene
    fallback: str
    # 이 대사가 나갈 창구. **말투만 고른다** — 무엇을 말할지는 `scene` 이, 무엇이 사실인지는
    # `facts` 가 정한다. `sanitize` 는 이 값을 보지 않는다(가드는 창구와 무관한 안전망이다).
    surface: Surface = Surface.GAME
    user_text: str | None = None
    facts: tuple[str, ...] = ()
    # 최근에 오간 말. 어조와 흐름 참고용이며 **확정 사실이 아니다** — `sanitize` 의 숫자
    # 검사는 `facts` 만 보므로, 여기 있는 수치가 게임 사실로 승격되지 않는다.
    history: tuple[ConversationTurn, ...] = ()
    # 지난 세션들에서 알게 된 것(`memory.py`). `history` 와 같은 취급이다 — 확정 사실이
    # 아니다. 숫자를 담지 않으므로(`build_memory`) `sanitize` 의 숫자 검사와 충돌하지 않는다.
    memories: tuple[str, ...] = ()
    # 게임이 현재 턴에 알려 준 상황. 게임 시계는 서버가 신뢰하는 값이지만 장면의 확정
    # 사실과는 다른 블록으로 보여 준다.
    situation: tuple[str, ...] = ()


SCENE_GUIDE: dict[DialogueScene, str] = {
    "follow_player": "플레이어를 따라가기 시작한다는 것을 알린다.",
    "wait": "지금 이 자리에서 기다리겠다는 것을 알린다.",
    "stop_current_task": "하던 작업을 지금 중단한다는 것을 알린다.",
    "cancel": "직전 요청을 없던 일로 하겠다는 것을 알린다.",
    "gather_wood": "근처에서 나무를 찾아 채집하러 간다는 것을 알린다.",
    "gather_stone": "근처에서 돌을 찾아 채집하러 간다는 것을 알린다.",
    "gather_ambiguous": "무엇을 캘지 되묻는다. 나무와 돌 중에서만 고르게 한다.",
    "attack": "지금 적을 공격하러 나선다는 것을 알린다.",
    "return_to_player": "플레이어 곁으로 돌아간다는 것을 알린다.",
    "recipe": "확정 사실의 제작법을 전한다. 재료·수량·제작 장소를 빠뜨리지 않는다.",
    "enemy": "확정 사실의 적 약점과 공략을 전한다. 약점 부위와 속성을 빠뜨리지 않는다.",
    "lore": "확정 사실의 지역 이야기를 전한다.",
    "conversation": "플레이어의 말에 가볍게 반응한다.",
    "unsupported": (
        "확정 사실에 적힌 이유로 그 요청은 도울 수 없다고 알리고, "
        "할 수 있는 일을 짧게 안내한다."
    ),
    "event_completed": "채집 결과를 확정 사실 그대로 보고한다.",
    "event_failed": "채집 결과를 확정 사실 그대로 보고한다.",
    "situation": "[상황]에 적힌 일이 방금 일어났다. 플레이어에게 먼저 한마디 건넨다.",
}

class FallbackLine(NamedTuple):
    """LLM이 실패했을 때 그대로 나갈 대사와, 같은 상황에서 프롬프트에 넣을 확정 사실.

    둘을 한 쌍으로 묶는 이유: 창구가 달라지면 반드시 함께 달라진다. 대사만 고치고 사실을
    두면 게임 동작 이름이 적힌 사실이 모바일 프롬프트로 흘러간다.
    """

    text: str
    fact: str


@dataclass(frozen=True, slots=True)
class SurfaceProfile:
    """한 창구에서 마코가 어떤 말투로 말하고, 못 하는 일을 어떻게 알리는지.

    **창구별로 달라지는 것은 여기가 전부다.** `SCENE_GUIDE` 는 나누지 않는다 — 장면 지시는
    *무엇을 말할지*(내용)이고 그건 창구와 무관하며, 달라지는 것은 *어떻게 말할지*뿐이다.
    """

    # 대사 시스템 프롬프트에 끼워 넣을 어조 블록(`llm.py`).
    tone: str
    # 이 창구에서 아예 할 수 없는 요청.
    unsupported: FallbackLine
    # 명령은 알아들었지만 지금 낼 수 없는 경우.
    not_allowed: FallbackLine
    # 어느 지역 이야기인지 확인되지 않은 경우.
    lore_missing: FallbackLine
    greeting: str
    thanks: str
    # 상황 이벤트(`situation.py`)의 LLM 실패 시 폴백. 어떤 상황이 왔는지 모르는 채로 안전해야
    # 하므로 구체적인 내용을 담지 않는다 — `FallbackLine` 이 아닌 이유: 상황 자체가 이미
    # `[상황]` 사실 블록이라 별도의 `fact` 짝이 필요 없다.
    situation: str


# 두 행을 파생 없이 다 적는다. 창구를 늘릴 때 빠뜨린 항목을 mypy 가 잡아 준다.
SURFACE_PROFILES: dict[Surface, SurfaceProfile] = {
    Surface.GAME: SurfaceProfile(
        tone=(
            "한국어 반말로 따뜻하고 짧게 한두 문장만 말한다. 플레이어 바로 옆에 서 있고,"
            " 눈앞의 상황에 그 자리에서 반응한다.\n"
            "말투 예시(내용은 무시하고 어조만 참고): 발 맞춰 갈 테니까 앞장서. /"
            " 그건 내 손을 좀 벗어나네."
        ),
        unsupported=FallbackLine(
            "아직 그 요청은 도와줄 수 없어. 따라오기, 대기, 중지를 말해 줘.",
            "가능한 일은 따라오기, 대기, 작업 중지뿐이다",
        ),
        not_allowed=FallbackLine(
            "지금은 그렇게 해 줄 수 없어.",
            "지금 상황에서는 그 동작을 실행할 수 없다",
        ),
        lore_missing=FallbackLine(
            "지금 위치에 대해 확인된 이야기는 아직 없어.",
            "이 위치에 대해 확인된 이야기가 없다",
        ),
        greeting="안녕! 오늘은 어디부터 둘러볼까?",
        thanks="별말을 다 해. 필요하면 언제든 불러 줘.",
        situation="방금 그거, 봤어?",
    ),
    Surface.MOBILE: SurfaceProfile(
        tone=(
            "한국어 반말로 짧게 한두 문장만 말한다. 지금은 플레이어와 떨어져 휴대폰 채팅으로"
            " 말하고 있다. 곁에 있는 것처럼 눈앞의 상황을 말하지 않고, 메시지를 주고받듯"
            " 답한다.\n"
            "말투 예시(내용은 무시하고 어조만 참고): 그거 나도 좀 궁금했어. /"
            " 음, 그건 지금 내가 못 해 줘."
        ),
        # 게임 동작 이름을 적으면 안 된다. 폰에는 따라오기도 대기도 없다.
        unsupported=FallbackLine(
            "그건 여기서는 못 도와줘. 제작법이나 적 공략, 지역 이야기라면 물어봐.",
            "가능한 일은 제작법과 적 공략과 지역 이야기를 알려 주는 것뿐이다",
        ),
        not_allowed=FallbackLine(
            "채팅으로는 아직 그걸 시킬 수 없어.",
            "채팅으로는 마코를 움직이게 할 수 없다",
        ),
        lore_missing=FallbackLine(
            "어느 지역 얘기인지 모르겠어. 게임에서 물어봐 줄래?",
            "어느 위치를 말하는지 알 수 없어 확인된 이야기를 찾지 못했다",
        ),
        greeting="안녕! 무슨 일이야?",
        thanks="별말을. 또 필요하면 말해.",
        situation="방금 거기 무슨 일 있었어?",
    ),
}


_NUMBER_PATTERN = re.compile(r"\d+")
_WHITESPACE_PATTERN = re.compile(r"\s+")


def sanitize(text: str, spec: DialogueSpec) -> str | None:
    """대사 형식과 확정 사실의 숫자를 검증하고 안전한 한 줄로 정리한다."""

    normalized = _WHITESPACE_PATTERN.sub(" ", text).strip()
    if not normalized or len(normalized) > 200:
        return None

    if spec.scene != "conversation":
        allowed_numbers = set(_NUMBER_PATTERN.findall(" ".join(spec.facts)))
        allowed_numbers.update(_NUMBER_PATTERN.findall(" ".join(spec.situation)))
        output_numbers = set(_NUMBER_PATTERN.findall(normalized))
        if not output_numbers.issubset(allowed_numbers):
            return None
    return normalized


async def render(llm: LLMProvider, spec: DialogueSpec) -> str:
    """공급자 출력을 검증하고 어떤 실패에도 코드의 기존 대사로 복구한다."""

    try:
        generated = await llm.generate_dialogue(spec)
    except Exception:
        return spec.fallback
    return sanitize(generated, spec) or spec.fallback
