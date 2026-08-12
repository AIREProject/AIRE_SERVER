"""마코가 턴 사이에 들고 가는 기억과 그 저장소.

이 패키지가 소유한다. 서버의 `messages` 테이블은 감사 기록이고 여기 있는 것은 작업 기억이라,
수명도 목적도 다르다. 한 행에 수명이 다른 두 writer 를 두지 않기 위해 섞지 않는다.

키는 어댑터가 넘겨주는 불투명한 값이다. 이 모듈은 그 값이 무엇에서 파생됐는지 알지 못하며,
알 필요도 없다.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta
from typing import Literal, Protocol

# 되묻기를 몇 번까지 반복할지. 상한이 없으면 플레이어가 계속 모호하게 답할 때
# 마코가 같은 질문만 되풀이한다.
MAX_ASK_COUNT = 2

# 프롬프트에 실을 수 있는 대화의 상한. 설정이 아니라 상수로 둔다 — 환경변수로 올릴 수 있는
# 값은 상한 구실을 못 한다. 3왕복이면 연속성에는 충분하고 토큰은 얼마 들지 않는다.
MAX_HISTORY_TURNS = 6
MAX_HISTORY_TEXT = 200


@dataclass(frozen=True, slots=True)
class PendingSlot:
    """되물어 놓고 아직 답을 받지 못한 슬롯."""

    kind: Literal["gather_resource"]
    # 플레이어가 이미 말한 수량. 되물었다고 버리면 "20개 캐 줘" → "뭘?" → "나무" 에서
    # 20이 사라진다.
    quantity: int | None
    ask_count: int
    asked_at: datetime

    def is_expired(self, *, now: datetime, ttl_seconds: float) -> bool:
        return now - self.asked_at > timedelta(seconds=ttl_seconds)

    def asked_again(self, *, now: datetime) -> PendingSlot:
        return replace(self, ask_count=self.ask_count + 1, asked_at=now)

    @property
    def may_ask_again(self) -> bool:
        return self.ask_count < MAX_ASK_COUNT


@dataclass(frozen=True, slots=True)
class ConversationTurn:
    """실제로 오간 말 한 마디. 프롬프트의 흐름 참고용으로만 쓴다.

    `speaker` 가 `"situation"` 이면 플레이어가 한 말이 아니라 클라이언트가 알려 온 상황이다
    — 마코의 대사가 왜 나왔는지 맥락 없이 `[최근 대화]` 에 남지 않도록 구분한다.
    """

    speaker: Literal["player", "companion", "situation"]
    text: str


def _clip(text: str) -> str:
    """한 턴이 프롬프트를 통째로 차지하지 못하게 자른다."""

    collapsed = " ".join(text.split())
    return collapsed[:MAX_HISTORY_TEXT]


@dataclass(frozen=True, slots=True)
class ConversationMemory:
    """한 대화가 다음 턴으로 넘기는 전부."""

    pending: PendingSlot | None = None
    recent_turns: tuple[ConversationTurn, ...] = ()

    @property
    def is_empty(self) -> bool:
        return self.pending is None and not self.recent_turns

    def appended(self, player_text: str, companion_text: str) -> ConversationMemory:
        """이번 턴의 두 마디를 덧붙이고 상한을 넘긴 앞쪽을 버린다.

        **누적 턴 수는 세지 않는다.** 예전에는 이 카운터로 "N턴마다 추출" 을 판정했지만,
        증류 주기는 이제 전사에 대한 커서가 소유한다(`companion.py`). 작업 기억은 프롬프트에
        실을 최근 몇 마디만 알면 된다.
        """

        turns = (
            *self.recent_turns,
            ConversationTurn(speaker="player", text=_clip(player_text)),
            ConversationTurn(speaker="companion", text=_clip(companion_text)),
        )
        return replace(self, recent_turns=turns[-MAX_HISTORY_TURNS:])

    def reacted(self, situation: Sequence[str], companion_text: str) -> ConversationMemory:
        """상황 한 줄들과 마코의 대사를 덧붙인다.

        `appended` 와 달리 `pending` 을 건드리지 않는다 — 상황 이벤트는 플레이어의 답이
        아니므로, 되묻기 도중에 상황이 끼어들어도 되묻던 슬롯이 조용히 사라지면 안 된다.
        """

        turns = (
            *self.recent_turns,
            ConversationTurn(speaker="situation", text=_clip(" ".join(situation))),
            ConversationTurn(speaker="companion", text=_clip(companion_text)),
        )
        return replace(self, recent_turns=turns[-MAX_HISTORY_TURNS:])


EMPTY_MEMORY = ConversationMemory()


class ConversationStore(Protocol):
    """대화 키별 기억 보관소. 구현은 자기 수명과 한계를 스스로 정한다."""

    def load(self, key: str) -> ConversationMemory:
        """없거나 만료됐으면 빈 기억을 돌려준다."""
        ...

    def save(self, key: str, memory: ConversationMemory) -> None:
        """기억을 갱신한다. 빈 기억이면 항목을 지운다."""
        ...


class InMemoryConversationStore:
    """프로세스 메모리에 두는 기본 구현.

    단일 워커 MVP 전제라 재시작 시 소실과 워커 간 비공유를 감수한다. 무한히 자라지 않도록
    TTL 과 최대 항목 수로 경계 짓는다. 교체가 필요해지면 `ConversationStore` 를 구현하면 된다.

    수명은 두 층이다. **대화 항목**은 마지막으로 쓰인 뒤 `idle_ttl_seconds` 가 지나면
    통째로 사라지고, 그 안에서 **되묻기 슬롯**은 더 짧은 `pending_ttl_seconds` 로 따로
    만료된다. 슬롯은 답을 기다리는 질문이라 금방 낡지만 대화는 그보다 오래 이어지기 때문이며,
    하나로 합치면 기록만 남은 대화가 영영 만료되지 않거나 대화가 너무 빨리 끊긴다.
    """

    def __init__(
        self,
        *,
        pending_ttl_seconds: float,
        idle_ttl_seconds: float,
        max_entries: int,
    ) -> None:
        self._pending_ttl_seconds = pending_ttl_seconds
        self._idle_ttl_seconds = idle_ttl_seconds
        self._max_entries = max_entries
        # 삽입 순서를 유지하는 dict 라, 가장 오래 전에 쓰인 항목이 앞에 온다.
        self._entries: dict[str, tuple[ConversationMemory, datetime]] = {}

    def load(self, key: str) -> ConversationMemory:
        entry = self._entries.get(key)
        if entry is None:
            return EMPTY_MEMORY

        memory, saved_at = entry
        now = datetime.now(UTC)
        # 대화 자체가 낡았으면 기록까지 통째로 버린다. 기억이 대화보다 오래 살면 안 된다.
        if now - saved_at > timedelta(seconds=self._idle_ttl_seconds):
            del self._entries[key]
            return EMPTY_MEMORY

        # 대화는 살아 있지만 되묻기만 낡았을 수 있다. 그때는 슬롯만 떨군다.
        if memory.pending is not None and memory.pending.is_expired(
            now=now, ttl_seconds=self._pending_ttl_seconds
        ):
            memory = replace(memory, pending=None)
            self._entries[key] = (memory, saved_at)
        return memory

    def save(self, key: str, memory: ConversationMemory) -> None:
        # 빈 기억을 남겨 두면 아무것도 기억하지 않는 대화가 자리만 차지한다.
        if memory.is_empty:
            self._entries.pop(key, None)
            return

        # 다시 쓴 항목은 뒤로 보내, 축출이 "가장 오래 안 쓰인 것" 을 고르게 한다.
        self._entries.pop(key, None)
        self._entries[key] = (memory, datetime.now(UTC))
        while len(self._entries) > self._max_entries:
            self._entries.pop(next(iter(self._entries)))
