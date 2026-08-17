"""상황 이벤트를 대사 프롬프트 스펙으로 조립한다.

`gametime.py` 와 같은 급의 순수 모듈이다. 여기 지식이 `companion.py` 로 새면 두뇌 진입점이
프롬프트 조립까지 알아야 해서 커진다.
"""

from __future__ import annotations

from .contract import SituationTurn
from .dialogue import SURFACE_PROFILES, DialogueSpec
from .gametime import describe
from .store import ConversationTurn


def build_spec(
    turn: SituationTurn,
    *,
    history: tuple[ConversationTurn, ...],
    memories: tuple[str, ...],
) -> DialogueSpec:
    """클라이언트가 보낸 상황과 게임 시계를 하나의 `[상황]` 블록으로 합친다."""

    return DialogueSpec(
        scene="situation",
        fallback=SURFACE_PROFILES[turn.surface].situation,
        surface=turn.surface,
        user_text=None,
        facts=(),
        history=history,
        memories=memories,
        situation=(*turn.situation, *describe(turn.game_time)),
        relationship_state=turn.relationship_state,
    )
