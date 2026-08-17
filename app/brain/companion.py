"""마코 두뇌의 진입점.

한국어 발화 라우팅은 [graph.py](graph.py)의 LangGraph `StateGraph` 가 담당하고,
이 모듈은 그래프를 한 번 컴파일해 두고 `CompanionTurn` → `CompanionReply` 로 옮기는 일만 맡는다.

**기억 파이프라인도 여기서 돈다.** 턴마다 오간 말을 전사에 덧붙이고, 백그라운드 루프 하나가
아직 증류하지 않은 구간을 읽어 장기기억으로 옮긴다. 추출은 턴의 부수효과가 아니라 전사에
대한 커서 작업이다 — 그래서 추출 시점이 턴 주기에서 자유롭고, 대화가 몇 턴에서 끝나든
꼬리가 유실되지 않는다.

실패를 감추지 않는다. 예외를 서버용 오류로 옮기는 것은 `app/service.py` 의 몫이며,
여기서 미리 삼키면 장애와 정상 응답을 구분할 수 없게 된다. **예외는 기억 파이프라인뿐이고**
(`_record`, `_drain`), 그 이유는 각 메서드의 docstring 에 적혀 있다 — 그쪽에는 실패를
전달할 호출자가 없고, 기억을 만들지 못한 것은 대화의 실패가 아니다.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Sequence
from contextlib import asynccontextmanager, suppress
from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta
from typing import cast

from sqlalchemy.exc import SQLAlchemyError

from app.db.source_repository import SourceScope
from app.models import TimeContext
from app.relationship_service import RelationshipPresentationStore, RelationshipState
from app.source_memory_store import SourceBackedMemoryStore

from .contract import (
    BrainProvenance,
    CompanionReply,
    CompanionTurn,
    FallbackReason,
    FinalResponseSource,
    MemoryScope,
    ProviderCallProvenance,
    SituationTurn,
)
from .dialogue import begin_sanitizer_trace, finish_sanitizer_trace, render
from .embedding import EmbeddingProvider
from .enemies import EnemyRepository
from .graph import CompanionState, build_companion_graph, selected_route
from .llm import LLMProvider, begin_provider_trace, finish_provider_trace
from .lore import LoreRepository
from .memory import (
    MAX_MEMORIES_PER_PLAYER,
    MAX_SUMMARY_TURNS,
    ConsolidationSpec,
    LongTermMemory,
    LongTermStore,
    MemoryExtractionSpec,
    SessionSummarySpec,
    consolidated,
    memories_from,
    summary_memory,
)
from .recipes import RecipeRepository
from .resources import ResourceRepository
from .situation import build_spec as build_situation_spec
from .store import (
    ConversationMemory,
    ConversationStore,
    ConversationTurn,
    InMemoryConversationStore,
)
from .transcript import TranscriptEntry, TranscriptStore

# `store` 를 넘기지 않았을 때 쓰는 값. **운영 기본값이 아니다** — 서버는 항상
# `Settings` 로 만든 저장소를 넘긴다(`app/service.py`). 여기 있는 것은 두뇌를 단독으로
# 띄우거나 테스트할 때 저장소를 매번 조립하지 않기 위한 편의값이다.
_DEFAULT_PENDING_TTL_SECONDS = 120.0
_DEFAULT_IDLE_TTL_SECONDS = 1800.0
_DEFAULT_MAX_ENTRIES = 1000
_DEFAULT_RECALL_LIMIT = 3
_DEFAULT_EXTRACT_EVERY_N_TURNS = 3
_DEFAULT_QUIET_SECONDS = 90.0
_DEFAULT_SESSION_END_SECONDS = 600.0
_DEFAULT_TICK_SECONDS = 15.0
_DEFAULT_RETENTION_DAYS = 30

# 추출할 때 "이미 아는 것" 으로 보여 줄 기억 수. 회수 한도보다 넉넉해야 방금 회수되지 않은
# 기억까지 보고 중복을 피한다. 프롬프트에 실리는 양이라 상한은 상수로 둔다.
_KNOWN_MEMORY_LIMIT = 12

# 한 번의 증분 추출이 읽을 전사 항목 수의 상한. 더 밀렸으면 다음 차례에 이어서 읽는다 —
# 커서가 실제로 읽은 만큼만 전진하므로 건너뛰는 구간은 생기지 않는다.
MAX_EXTRACT_ENTRIES = 24

# 대기열에 둘 대화 수의 상한. 넘치면 가장 오래된 것부터 버린다. 커서를 잃을 뿐 전사는
# 남으므로 원문이 사라지는 것은 아니다.
MAX_PENDING_CONVERSATIONS = 500
_EMBEDDING_QUERY_CACHE_SIZE = 64

# 전사 정리 주기. 보존 기간이 날 단위라 이보다 자주 돌 이유가 없다.
_SWEEP_INTERVAL_SECONDS = 3600.0


def _build_provenance(
    final: CompanionState | None,
    *,
    provider_calls: tuple[ProviderCallProvenance, ...],
    sanitizer_results: tuple[bool, ...],
    route: str,
) -> BrainProvenance:
    dialogue_call = next(
        (call for call in reversed(provider_calls) if call.step == "generate_dialogue"), None
    )
    sanitizer_succeeded = sanitizer_results[-1] if sanitizer_results else None
    repository_match = bool(final and final.get("repository_match"))
    if sanitizer_succeeded is False:
        source: FinalResponseSource = "validation_rejection"
    elif dialogue_call is not None and dialogue_call.effective_provider == "local":
        source = "local_llm"
    elif dialogue_call is not None and dialogue_call.effective_provider == "openai":
        source = "openai"
    elif dialogue_call is not None and dialogue_call.effective_provider == "mock":
        source = "mock_fallback"
    elif repository_match:
        source = "game_repository"
    else:
        source = "fixed_fallback"

    if sanitizer_succeeded is False:
        final_reason: FallbackReason | None = "sanitizer_rejection"
    elif dialogue_call is not None and dialogue_call.fallback_reason is not None:
        final_reason = dialogue_call.fallback_reason
    else:
        final_reason = next(
            (call.fallback_reason for call in provider_calls if call.fallback_reason is not None),
            None,
        )

    intent = final.get("top_intent") if final is not None else None
    query_mode = final.get("query_mode") if final is not None else None
    return BrainProvenance(
        top_intent=intent.value if intent is not None else None,
        query_mode=query_mode.value if query_mode is not None else None,
        selected_route=route,
        repository_match=repository_match,
        fact_ids=tuple(final.get("fact_ids", ()))[:8] if final is not None else (),
        provider_calls=provider_calls[:8],
        effective_provider=(
            dialogue_call.effective_provider if dialogue_call is not None else None
        ),
        final_response_source=source,
        sanitizer_succeeded=sanitizer_succeeded,
        final_fallback_reason=final_reason,
    )


@dataclass(slots=True)
class _Pending:
    """아직 증류가 끝나지 않은 대화 하나.

    **인메모리다.** 재시작하면 커서가 사라져 미추출 꼬리는 자동 파이프라인에서 빠진다.
    다만 전사는 온전하므로 나중에 일괄 재추출이 가능하다 — 원문 자체가 없던 예전과 다르다.
    """

    player_key: str
    # 어디까지 증류했는지. 전사의 `seq` 를 가리킨다.
    extracted_upto: int
    # 전사에 쓰인 마지막 `seq`.
    appended_upto: int
    last_turn_at: datetime
    summarized: bool


@dataclass(frozen=True, slots=True)
class PreparedCompanionReply:
    conversation_key: str
    player_key: str
    saved_memory: ConversationMemory
    turns: tuple[ConversationTurn, ...]
    reply: CompanionReply


class CompanionBrain:
    """발화 한 번을 받아 마코의 대사와 행동을 정한다."""

    def __init__(
        self,
        llm: LLMProvider,
        *,
        recipes: RecipeRepository | None = None,
        lore: LoreRepository | None = None,
        resources: ResourceRepository | None = None,
        enemies: EnemyRepository | None = None,
        store: ConversationStore | None = None,
        long_term: LongTermStore | None = None,
        source_memory: SourceBackedMemoryStore | None = None,
        relationship_presentation: RelationshipPresentationStore | None = None,
        transcript: TranscriptStore | None = None,
        embedder: EmbeddingProvider | None = None,
        embedding_model: str | None = None,
        embedding_timeout_seconds: float = 3.0,
        recall_limit: int = _DEFAULT_RECALL_LIMIT,
        extract_every_n_turns: int = _DEFAULT_EXTRACT_EVERY_N_TURNS,
        quiet_seconds: float = _DEFAULT_QUIET_SECONDS,
        session_end_seconds: float = _DEFAULT_SESSION_END_SECONDS,
        tick_seconds: float = _DEFAULT_TICK_SECONDS,
        transcript_retention_days: int = _DEFAULT_RETENTION_DAYS,
    ) -> None:
        # 앱 수명 동안 공유되는 단일 인스턴스라, 종료 시 정리할 수 있게 공급자를 참조로 보관한다.
        self._llm = llm
        # 턴 사이 기억은 이 패키지가 소유한다. 서버 DB 는 쓰지 않는다.
        self._store = store or InMemoryConversationStore(
            pending_ttl_seconds=_DEFAULT_PENDING_TTL_SECONDS,
            idle_ttl_seconds=_DEFAULT_IDLE_TTL_SECONDS,
            max_entries=_DEFAULT_MAX_ENTRIES,
        )
        # 세션을 넘는 기억. **없어도 두뇌는 온전히 동작한다** — 넘기지 않으면 회수도 추출도
        # 일어나지 않고, 지금까지와 똑같이 한 대화 안에서만 기억한다.
        self._long_term = long_term
        self._source_memory = source_memory
        self._relationship_presentation = relationship_presentation
        # 증류의 원본. **추출은 이것 없이는 돌지 않는다** — 커서가 가리킬 로그가 없다.
        # 전사만 끄면 회수는 계속 되지만(이미 있는 기억은 쓰인다) 새 기억은 생기지 않는다.
        self._transcript = transcript
        self._embedder = embedder
        self._embedding_model = embedding_model
        self._embedding_timeout_seconds = embedding_timeout_seconds
        self._query_embedding_cache: dict[str, tuple[float, ...] | None] = {}
        self._recall_limit = recall_limit
        self._extract_every_n_turns = extract_every_n_turns
        self._quiet_seconds = quiet_seconds
        self._session_end_seconds = session_end_seconds
        self._tick_seconds = tick_seconds
        self._transcript_retention_days = transcript_retention_days
        # 증류를 기다리는 대화들. 삽입 순서를 유지하는 dict 라 앞이 가장 오래된 것이다.
        self._pending: dict[str, _Pending] = {}
        self._loop_task: asyncio.Task[None] | None = None
        self._last_sweep_at: datetime | None = None
        # 컴파일된 그래프는 불변이라 동시 요청에 안전하게 공유된다. 한 번만 만든다.
        self._graph = build_companion_graph(
            llm,
            recipes or RecipeRepository(),
            lore or LoreRepository(),
            resources or ResourceRepository(),
            enemies or EnemyRepository(),
        )
        # 대화별 직렬화 장치. 대화 수가 아니라 처리 중인 요청 수만큼만 존재한다.
        self._locks: dict[str, asyncio.Lock] = {}
        self._lock_users: dict[str, int] = {}

    @asynccontextmanager
    async def _conversation_lock(self, key: str) -> AsyncIterator[None]:
        """한 대화의 `load → 그래프 → save` 를 직렬화한다.

        이 구간에는 await 가 있어 같은 대화의 요청 둘이 끼어들 수 있고, 그러면 나중 저장이
        앞선 저장을 덮어써 되묻기 슬롯이나 대화 기록이 통째로 사라진다. 한 대화의 턴은 원래
        순차적이므로 직렬화가 곧 올바른 의미다.

        락은 쓰는 사람이 없어지면 즉시 버린다. 대화마다 남겨 두면 저장소와 달리 상한이 없다.
        """

        lock = self._locks.get(key)
        if lock is None:
            lock = asyncio.Lock()
            self._locks[key] = lock
        self._lock_users[key] = self._lock_users.get(key, 0) + 1
        try:
            async with lock:
                yield
        finally:
            self._lock_users[key] -= 1
            if self._lock_users[key] == 0:
                del self._lock_users[key]
                del self._locks[key]

    async def respond(self, turn: CompanionTurn) -> CompanionReply:
        turn = replace(turn, relationship_state=await self._relationship_state(turn.memory_scope))
        recalled = await self._recall(turn.memory_scope, turn.player_key, turn.text, turn.game_time)
        async with self._conversation_lock(turn.conversation_key):
            prepared = await self._prepare_response_locked(
                turn,
                history=None,
                recalled=recalled,
            )
            self._store.save(prepared.conversation_key, prepared.saved_memory)
        await self._record(
            prepared.conversation_key,
            prepared.player_key,
            prepared.turns,
        )
        return prepared.reply

    async def prepare_response(
        self,
        turn: CompanionTurn,
        *,
        history: Sequence[ConversationTurn] | None = None,
    ) -> PreparedCompanionReply:
        """Generate a reply without mutating process memory or debug transcript."""

        turn = replace(turn, relationship_state=await self._relationship_state(turn.memory_scope))
        # 회수는 락 밖에서 한다. 이 발화만 보고 고르므로 대화 기억과 경합할 것이 없고,
        # 파일을 읽는 동안 같은 대화의 다음 턴을 막을 이유도 없다.
        recalled = await self._recall(turn.memory_scope, turn.player_key, turn.text, turn.game_time)
        async with self._conversation_lock(turn.conversation_key):
            return await self._prepare_response_locked(
                turn,
                history=history,
                recalled=recalled,
            )

    async def _prepare_response_locked(
        self,
        turn: CompanionTurn,
        *,
        history: Sequence[ConversationTurn] | None,
        recalled: tuple[str, ...],
    ) -> PreparedCompanionReply:
        memory = self._store.load(turn.conversation_key)
        prompt_memory = (
            replace(memory, recent_turns=tuple(history)) if history is not None else memory
        )
        provider_token = begin_provider_trace()
        sanitizer_token = begin_sanitizer_trace()
        try:
            final = cast(
                CompanionState,
                await self._graph.ainvoke(
                    {
                        "turn": turn,
                        "text": turn.text,
                        "pending": prompt_memory.pending,
                        "recipe_reference": prompt_memory.recipe_reference,
                        "history": prompt_memory.recent_turns,
                        "long_term": recalled,
                    }
                ),
            )
        finally:
            provider_calls = finish_provider_trace(provider_token)
            sanitizer_results = finish_sanitizer_trace(sanitizer_token)
        display_text: str = final["display_text"]
        saved = replace(
            prompt_memory.appended(turn.text, display_text),
            pending=final.get("next_pending"),
            recipe_reference=final.get("next_recipe_reference"),
        )

        turns = (
            ConversationTurn(speaker="player", text=turn.text),
            ConversationTurn(speaker="companion", text=display_text),
        )
        return PreparedCompanionReply(
            conversation_key=turn.conversation_key,
            player_key=turn.player_key,
            saved_memory=saved,
            turns=turns,
            reply=CompanionReply(
                text=display_text,
                action=final.get("action"),
                provenance=_build_provenance(
                    final,
                    provider_calls=provider_calls,
                    sanitizer_results=sanitizer_results,
                    route=selected_route(final),
                ),
            ),
        )

    async def commit_response(self, prepared: PreparedCompanionReply) -> None:
        """Publish a prepared reply after canonical persistence succeeds."""

        async with self._conversation_lock(prepared.conversation_key):
            self._store.save(prepared.conversation_key, prepared.saved_memory)
        await self._record(
            prepared.conversation_key,
            prepared.player_key,
            prepared.turns,
            enqueue_memory=False,
        )

    async def react(self, turn: SituationTurn) -> str:
        """기존 내부 호출자를 위해 대사 문자열만 반환한다."""

        return (await self.react_with_provenance(turn)).text

    async def react_with_provenance(self, turn: SituationTurn) -> CompanionReply:
        """플레이어 발화 없이, 클라이언트가 알려 온 상황에 먼저 한마디 건넨다.

        라우팅(Stage 1/2)을 거치지 않는다 — 무슨 상황인지는 클라이언트가 코드로 이미
        판단해 보냈으므로 서버가 다시 분류할 이유가 없다. `pending` 슬롯은 건드리지 않는다
        — 상황 이벤트는 플레이어의 답이 아니므로 되묻기 도중에 끼어들어도 슬롯이 살아남는다.
        """

        turn = replace(turn, relationship_state=await self._relationship_state(turn.memory_scope))
        query = " ".join(turn.situation)
        recalled = await self._recall(turn.memory_scope, turn.player_key, query, turn.game_time)
        async with self._conversation_lock(turn.conversation_key):
            memory = self._store.load(turn.conversation_key)
            spec = build_situation_spec(turn, history=memory.recent_turns, memories=recalled)
            provider_token = begin_provider_trace()
            sanitizer_token = begin_sanitizer_trace()
            try:
                display_text = await render(self._llm, spec)
            finally:
                provider_calls = finish_provider_trace(provider_token)
                sanitizer_results = finish_sanitizer_trace(sanitizer_token)
            self._store.save(turn.conversation_key, memory.reacted(turn.situation, display_text))

        await self._record(
            turn.conversation_key,
            turn.player_key,
            (
                ConversationTurn(speaker="situation", text=query),
                ConversationTurn(speaker="companion", text=display_text),
            ),
        )
        self._ensure_loop()
        return CompanionReply(
            text=display_text,
            provenance=_build_provenance(
                None,
                provider_calls=provider_calls,
                sanitizer_results=sanitizer_results,
                route="situation",
            ),
        )

    async def aclose(self) -> None:
        """종료 시 남은 증류를 끝내고 공급자가 보유한 HTTP 클라이언트 등을 정리한다.

        루프를 멈춘 뒤 마지막으로 한 번 더 비운다(`final=True`). 정상 종료에서는 아직
        증류하지 않은 꼬리와 세션 요약이 사라지지 않는다.
        """

        if self._loop_task is not None:
            self._loop_task.cancel()
            with suppress(asyncio.CancelledError):
                await self._loop_task
            self._loop_task = None
        # 공급자를 먼저 닫으면 마지막 증류가 이미 닫힌 클라이언트를 쓴다.
        await self._drain(now=datetime.now(UTC), final=True)
        await self._llm.aclose()
        if self._embedder is not None:
            await self._embedder.aclose()

    async def _recall(
        self,
        memory_scope: MemoryScope | None,
        player_key: str,
        query: str,
        game_time: TimeContext | None,
    ) -> tuple[str, ...]:
        """이번 발화(또는 상황)와 관련 있는 장기기억을 문장으로만 꺼낸다.

        그래프에는 저장소가 아니라 이미 고른 문장만 들어간다. 노드가 저장소를 쥐고 있으면
        분류 노드도 기억을 볼 수 있게 되고, 그 순간 세 세션 전 "따라와" 가 지금 명령이 된다.
        """

        if self._recall_limit <= 0:
            return ()
        if self._source_memory is not None:
            if memory_scope is None:
                return ()
            scope = SourceScope(
                memory_scope.profile_id,
                memory_scope.save_slot_row_id,
                memory_scope.companion_id,
            )
            source_mode = None if game_time is None else game_time.source.value
            source_recalled = await self._source_memory.recall(
                scope,
                query=query,
                source_mode=source_mode,
                query_embedding=await self._embed_text(query),
                embedding_model=self._embedding_model,
                limit=self._recall_limit,
            )
            return tuple(f"[{memory.trace_id}] {memory.text}" for memory in source_recalled)
        if self._long_term is None or not player_key:
            return ()
        query_embedding = await self._embed_text(query)
        if self._embedder is None:
            recalled = await self._long_term.recall(
                player_key, query=query, limit=self._recall_limit
            )
        else:
            recalled = await self._long_term.recall(
                player_key,
                query=query,
                limit=self._recall_limit,
                query_embedding=query_embedding,
                embedding_model=self._embedding_model,
            )
        return tuple(memory.text for memory in recalled)

    async def _relationship_state(self, memory_scope: MemoryScope | None) -> RelationshipState:
        if self._relationship_presentation is None or memory_scope is None:
            return "Low"
        scope = SourceScope(
            memory_scope.profile_id,
            memory_scope.save_slot_row_id,
            memory_scope.companion_id,
        )
        try:
            state: RelationshipState = await self._relationship_presentation.read(scope)
        except SQLAlchemyError:
            # Presentation state must never turn a working local dialogue into a database outage.
            state = "Low"
        return state

    async def _embed_text(self, text: str) -> tuple[float, ...] | None:
        """요청 경로의 질의 임베딩. 실패·시간 초과는 키워드 검색으로 폴백한다."""

        if self._embedder is None or not text.strip():
            return None
        if text in self._query_embedding_cache:
            return self._query_embedding_cache[text]
        try:
            async with asyncio.timeout(self._embedding_timeout_seconds):
                vectors = await self._embedder.embed((text,))
            vector = vectors[0] if vectors else None
        except Exception:
            vector = None
        self._query_embedding_cache[text] = vector
        while len(self._query_embedding_cache) > _EMBEDDING_QUERY_CACHE_SIZE:
            self._query_embedding_cache.pop(next(iter(self._query_embedding_cache)))
        return vector

    async def _with_embeddings(
        self, memories: Sequence[LongTermMemory]
    ) -> tuple[LongTermMemory, ...]:
        """새 기억의 본문을 배치 임베딩하고 실패한 항목은 벡터 없이 남긴다."""

        if self._embedder is None or not memories:
            return tuple(memories)
        texts = tuple(memory.text for memory in memories)
        try:
            async with asyncio.timeout(self._embedding_timeout_seconds):
                vectors = await self._embedder.embed(texts)
        except Exception:
            vectors = tuple(None for _ in texts)
        return tuple(
            memory
            if vector is None and memory.embedding is not None
            else replace(
                memory,
                embedding=vector,
                embedding_model=self._embedding_model if vector is not None else None,
            )
            for memory, vector in zip(memories, vectors, strict=True)
        )

    async def _remember(self, player_key: str, memories: Sequence[LongTermMemory]) -> None:
        if self._long_term is None or not player_key or not memories:
            return
        embedded = await self._with_embeddings(memories)
        await self._long_term.remember(player_key, embedded)

    async def _record(
        self,
        conversation_key: str,
        player_key: str,
        turns: Sequence[ConversationTurn],
        *,
        enqueue_memory: bool = True,
    ) -> None:
        """이번 턴(또는 상황 반응)의 원문을 전사에 덧붙이고 증류 대기열을 갱신한다.

        **여기서 예외를 삼킨다.** 전사를 남기지 못한 것은 대화의 실패가 아니다 — 플레이어는
        이미 대답을 들었고, 로그를 못 썼다고 그 턴을 오류로 만들면 기억 때문에 대화가 끊긴다.

        전사는 증류의 원본이므로 작업 기억의 프롬프트용 잘림 규칙을 재사용하지 않는다.
        작업 기억은 최근 대화가 프롬프트를 독점하지 않게 자르고, 전사는 클라이언트가 보낸
        원문과 마코가 실제로 말한 대사를 보존한다.
        """

        if self._transcript is None:
            return
        try:
            appended_upto = await self._transcript.append(conversation_key, turns)
        except Exception:
            return
        if not enqueue_memory or self._long_term is None or not player_key or appended_upto <= 0:
            return
        self._enqueue(conversation_key, player_key, appended_upto, len(turns))

    def _enqueue(
        self, conversation_key: str, player_key: str, appended_upto: int, written: int
    ) -> None:
        """대기열 항목을 만들거나 갱신한다."""

        now = datetime.now(UTC)
        pending = self._pending.pop(conversation_key, None)
        if pending is None:
            # 처음 보는 대화다. 재시작 직후라면 파일에 앞선 턴이 있을 수 있는데, 그것까지
            # 다시 증류하지는 않는다 — 이미 증류됐을 수도 있고, 인메모리 대기열의 값이다.
            pending = _Pending(
                player_key=player_key,
                extracted_upto=appended_upto - written,
                appended_upto=appended_upto,
                last_turn_at=now,
                summarized=False,
            )
        else:
            pending.appended_upto = appended_upto
            pending.last_turn_at = now
            # 요약한 대화에 말이 다시 오갔으면 끝난 것이 아니다. 새 요약이 옛 것을 갈아치운다.
            pending.summarized = False
        self._pending[conversation_key] = pending
        while len(self._pending) > MAX_PENDING_CONVERSATIONS:
            self._pending.pop(next(iter(self._pending)))

    def _ensure_loop(self) -> None:
        """증류 루프를 처음 필요할 때 띄운다.

        시작 훅이 없어(lifespan 은 teardown 만 부른다) 생성자에서는 이벤트 루프가 없을 수
        있다. 죽은 태스크는 다음 턴에 다시 살아난다.
        """

        if self._long_term is None or self._transcript is None:
            return
        if self._loop_task is None or self._loop_task.done():
            self._loop_task = asyncio.create_task(self._run())

    async def _run(self) -> None:
        """틱마다 대기열을 비운다. 판정은 하나도 하지 않는다 — 전부 `_drain` 안에 있다."""

        while True:
            await asyncio.sleep(self._tick_seconds)
            await self._drain(now=datetime.now(UTC))

    async def _drain(self, *, now: datetime, final: bool = False) -> None:
        """대기열을 훑어 증류할 때가 된 것을 증류한다.

        **시점 판정이 전부 여기 있다.** 루프는 잠들었다 이 메서드를 부를 뿐이라, 테스트는
        시계를 넘겨 이 메서드를 직접 부르고 실제로 기다리지 않는다.

        트리거는 셋이다. 미증류 구간이 임계값을 넘으면 증분 추출, 마지막 턴 이후 조용하면
        (`quiet_seconds`) 남은 구간을 증분 추출 — **꼬리 유실이 여기서 사라진다**, 대화가
        끝난 것으로 볼 만큼 지나면(`session_end_seconds`) 요약 한 번 뒤 대기열에서 뺀다.

        `_record` 와 같은 이유로 예외를 삼킨다. 한 대화의 증류 실패가 다른 대화의 증류를
        막아서도 안 되므로 항목 단위로 막는다.
        """

        for key, pending in tuple(self._pending.items()):
            try:
                await self._drain_one(key, pending, now=now, final=final)
            except Exception:
                # 한 대화의 증류 실패가 나머지 대화의 증류를 막지 않는다.
                continue
        await self._sweep(now=now, final=final)

    async def _drain_one(self, key: str, pending: _Pending, *, now: datetime, final: bool) -> None:
        idle = (now - pending.last_turn_at).total_seconds()
        quiet = final or idle >= self._quiet_seconds
        ended = final or idle >= self._session_end_seconds
        unextracted = pending.appended_upto - pending.extracted_upto
        # 설정값은 왕복 수고 전사의 항목은 한 마디씩이라, 임계값은 그 두 배다.
        threshold = self._extract_every_n_turns * 2

        if unextracted > 0 and (quiet or unextracted >= threshold):
            pending.extracted_upto = await self._extract(key, pending)
        if not ended:
            return
        if not pending.summarized:
            await self._summarize(key, pending)
        self._pending.pop(key, None)

    async def _extract(self, key: str, pending: _Pending) -> int:
        """아직 증류하지 않은 구간을 읽어 기억으로 옮기고 새 커서를 돌려준다."""

        if self._transcript is None or self._long_term is None:
            return pending.extracted_upto
        entries = await self._transcript.read(
            key, since=pending.extracted_upto, limit=MAX_EXTRACT_ENTRIES
        )
        if not entries:
            return pending.extracted_upto
        known = await self._long_term.recall(
            # 질의를 비우면 힘과 최근성만으로 뽑히고, 회수 통계도 남지 않는다. 지금 발화와
            # 겹치지 않는 기억까지 보여 줘야 같은 사실을 다시 만들지 않는다.
            pending.player_key,
            query="",
            limit=_KNOWN_MEMORY_LIMIT,
        )
        extraction = await self._llm.extract_memories(
            MemoryExtractionSpec(
                recent_turns=_as_turns(entries),
                known=tuple(item.text for item in known),
            )
        )
        await self._remember(pending.player_key, memories_from(extraction))
        await self._consolidate(pending.player_key)
        return entries[-1].seq

    async def _summarize(self, key: str, pending: _Pending) -> None:
        """끝난 대화 전체를 한 줄로 줄여 남긴다. 대화당 한 번이다."""

        if self._transcript is None or self._long_term is None:
            return
        pending.summarized = True
        entries = await self._transcript.tail(key, limit=MAX_SUMMARY_TURNS)
        if not entries:
            return
        summary = await self._llm.summarize_session(SessionSummarySpec(turns=_as_turns(entries)))
        memory = summary_memory(summary, source_key=key)
        if memory is not None:
            await self._remember(pending.player_key, (memory,))

    async def _consolidate(self, player_key: str) -> None:
        """상한에 닿았을 때만 겹치는 기억을 합쳐 자리를 만든다.

        **저장소는 LLM을 부르지 않는다** — 지능은 두뇌에, 보관은 저장소에. 결과가 기억을
        잃게 할 수 있으면 `consolidated()` 가 `None` 을 내고 여기서 아무것도 하지 않는다.
        """

        if self._long_term is None:
            return
        current = await self._long_term.recall(player_key, query="", limit=MAX_MEMORIES_PER_PLAYER)
        if len(current) < MAX_MEMORIES_PER_PLAYER:
            return
        result = await self._llm.consolidate_memories(
            ConsolidationSpec(memories=tuple(memory.text for memory in current))
        )
        merged = consolidated(current, result)
        if merged is None:
            return
        embedded = await self._with_embeddings(merged)
        await self._long_term.replace_all(player_key, embedded)

    async def _sweep(self, *, now: datetime, final: bool) -> None:
        """보존 기간이 지난 전사를 지운다. 유일하게 무한히 자라는 층이라 설계의 일부다."""

        if self._transcript is None or final:
            return
        if self._last_sweep_at is not None:
            if (now - self._last_sweep_at).total_seconds() < _SWEEP_INTERVAL_SECONDS:
                return
        self._last_sweep_at = now
        with suppress(Exception):
            await self._transcript.sweep(
                older_than=now - timedelta(days=self._transcript_retention_days)
            )


def _as_turns(entries: Sequence[TranscriptEntry]) -> tuple[ConversationTurn, ...]:
    """전사 항목을 프롬프트가 아는 모양으로 옮긴다.

    `memory.py` 가 `transcript.py` 를 몰라도 되게 하려고 여기서 바꾼다 — 추출 명세는 오간
    말만 알면 되고, `seq` 나 시각은 커서의 것이지 프롬프트의 것이 아니다.
    """

    return tuple(ConversationTurn(speaker=entry.speaker, text=entry.text) for entry in entries)
