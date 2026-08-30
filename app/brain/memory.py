"""마코가 대화를 넘어 들고 가는 장기기억과 그 저장소.

[store.py](store.py) 의 작업 기억과 **섞지 않는다.** 그쪽은 한 대화가 다음 턴으로 넘기는
것이라 `conversation_key` 로 색인되고 유휴 TTL 로 통째로 사라지지만, 여기 있는 것은 세션과
프로세스를 넘어 남는 것이라 `player_key` 로 색인되고 개수 상한으로만 줄어든다. 수명이 다른
두 기억을 한 레코드에 두면 어느 쪽 규칙도 지킬 수 없다.

키는 어댑터가 넘겨주는 불투명한 값이다. 이 모듈은 그 값이 무엇에서 파생됐는지 알지 못하며,
알 필요도 없다. 현재 런타임 저장소는 이 키를 SQLite 스코프로 사용하고, 아래 파일 읽기
호환 계층은 `0005` 마이그레이션의 기존 JSON을 옮길 때만 사용한다.
"""

from __future__ import annotations

import asyncio
import math
import re
from collections.abc import AsyncIterator, Sequence
from contextlib import asynccontextmanager
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal, Protocol

from pydantic import BaseModel, ConfigDict, Field

from .store import ConversationTurn

# 한 플레이어가 들고 갈 수 있는 기억의 상한. 설정이 아니라 상수로 둔다 — `store.py` 와 같은
# 이유다. 환경변수로 올릴 수 있는 값은 상한 구실을 못 한다.
MAX_MEMORIES_PER_PLAYER = 32
MAX_MEMORY_TEXT = 120
MAX_KEYWORDS = 6
# 한 글자 토큰("좀", "안")은 아무 발화에나 걸려 점수를 무의미하게 만든다.
MIN_KEYWORD_LENGTH = 2

# 요약 프롬프트에 실을 전사 항목 수. 500턴짜리 대화를 통째로 넣을 수는 없다.
MAX_SUMMARY_TURNS = 60

# 감쇠 반감기(일). 한 번도 불려 나오지 않은 기억은 이 주기로 힘이 절반이 된다.
HALF_LIFE_DAYS = 30.0
# 회수 횟수가 힘에 보태는 양의 상한. 없으면 한 번 자주 불린 기억이 영원히 못 밀려난다.
MAX_RECALL_BONUS = 5
_RECALL_BONUS_WEIGHT = 0.5

MemoryKind = Literal["profile", "episode", "session_summary"]


class MemoryClassification(BaseModel):
    """LLM output limited to disposition and ranking metadata.

    Canonical source text is intentionally absent: the worker copies it from the
    authenticated Message row after this result has passed strict validation.
    """

    model_config = ConfigDict(extra="forbid")

    decision: Literal["Reject", "ProfileFact", "Preference", "Promise", "Episode"]
    importance: int = Field(ge=1, le=10)
    confidence: float = Field(ge=0.0, le=1.0)


REJECT_MEMORY = MemoryClassification(decision="Reject", importance=1, confidence=1.0)

_EXPLICIT_MEMORY_REQUEST = re.compile(r"(?:기억해\s*줘|기억해\s*둬|잊지\s*마)")
_FIRST_PERSON = re.compile(r"(?:^|\s)(?:나는|난|내가|저는|전|제가)(?:\s|$)")
_PREFERENCE = re.compile(r"(?:좋아해|좋아한다|좋아해요|선호해|싫어해|싫어한다|싫어해요)")


def classify_explicit_memory_request(text: str) -> MemoryClassification | None:
    """Recognize only an explicit, first-person preference memory request.

    This is a booth-safe fallback for an unavailable classifier. It returns metadata
    only; the authenticated Message row remains the sole source of stored text.
    """

    normalized = " ".join(text.strip().split())
    if not normalized or len(normalized) > MAX_MEMORY_TEXT or _DIGIT_PATTERN.search(normalized):
        return None
    if (
        _EXPLICIT_MEMORY_REQUEST.search(normalized) is None
        or _FIRST_PERSON.search(normalized) is None
        or _PREFERENCE.search(normalized) is None
    ):
        return None
    return MemoryClassification(decision="Preference", importance=8, confidence=1.0)

# 회수 동점을 가를 때의 종류 우선순위. 플레이어 자체에 대한 사실이 지난 세션 요약보다 먼저다.
_KIND_ORDER: dict[MemoryKind, int] = {"profile": 0, "episode": 1, "session_summary": 2}

# 종류별 기본 중요도. 프로필 사실은 오래 남아야 하고 세션 요약은 가장 먼저 밀려나야 한다.
# 기존 1~3 척도를 ERD의 1~10 척도로 옮기되, 종류 사이의 상대 순서는 유지한다.
_DEFAULT_IMPORTANCE: dict[MemoryKind, int] = {"profile": 6, "episode": 4, "session_summary": 2}

MIN_IMPORTANCE = 1
MAX_IMPORTANCE = 10
# 중요도 범위를 넓혀도 키워드 겹침과 힘의 균형은 유지한다. 최대 힘은 예전처럼 5.5다.
_IMPORTANCE_WEIGHT = 3.0 / 10.0
_SEMANTIC_WEIGHT = 2.0
_SEMANTIC_FLOOR = 0.5

_DIGIT_PATTERN = re.compile(r"\d")
_WHITESPACE_PATTERN = re.compile(r"\s+")
_PUNCTUATION_PATTERN = re.compile(r"[^\w\s]", flags=re.UNICODE)

# 한국어는 교착어라 "나무를" 처럼 조사가 붙는다. 형태소 분석기를 들이는 대신 흔한 조사만
# 어미에서 떼어 내는 근사다. 질의 쪽은 부분 문자열로 맞추므로 키워드만 다듬으면 된다.
_PARTICLES = (
    "이랑|하고|에게|에서|처럼|보다|부터|까지|마다|라도|으로|한테|"
    "을|를|은|는|이|가|도|만|와|과|랑|로|의|에"
)
_PARTICLE_SUFFIX = re.compile(rf"(?:{_PARTICLES})$")

# 어느 기억에나 들어가 변별력이 없는 말. 키워드에서 뺀다.
_STOPWORDS = frozenset(
    {"플레이어", "마코", "그리고", "하지만", "때문", "우리", "자기", "이것", "그것", "저것"}
)


@dataclass(frozen=True, slots=True)
class LongTermMemory:
    """세션과 재시작을 넘어 남는 한 줄."""

    kind: MemoryKind
    # 숫자를 담지 않는다. `build_memory` 가 강제한다 — 이유는 그 함수의 주석에 있다.
    text: str
    # 회수용 색인. 본문에서 결정론적으로 파생하므로 저장하지 않고 읽을 때 다시 만든다.
    keywords: tuple[str, ...]
    importance: int
    # 세션 요약이 어느 대화에서 나왔는지. 같은 대화의 요약은 새 것이 옛 것을 갈아치운다.
    source_key: str | None
    created_at: datetime
    # 마지막으로 회수된 시각과 회수 횟수. 감쇠와 축출이 이 둘을 본다 — 매번 불려 나오는
    # 기억과 한 번도 안 쓰인 기억을 같게 취급하면 상한이 쓸모없는 줄로 찬다.
    recalled_at: datetime | None = None
    recall_count: int = 0
    # 임베딩은 선택 사항이다. 공급자가 없거나 실패한 기억은 키워드 검색으로 회수한다.
    embedding: tuple[float, ...] | None = None
    embedding_model: str | None = None


def _normalize(text: str) -> str:
    """구두점을 지우고 공백을 하나로 모은다. 키워드와 질의에 같은 규칙을 쓴다."""

    return " ".join(_PUNCTUATION_PATTERN.sub(" ", text.casefold()).split())


def _keywords(text: str) -> tuple[str, ...]:
    """본문에서 회수 색인으로 쓸 어절을 뽑는다. 같은 본문은 항상 같은 결과를 낸다."""

    picked: list[str] = []
    for token in _normalize(text).split():
        stem = _PARTICLE_SUFFIX.sub("", token)
        if len(stem) < MIN_KEYWORD_LENGTH or stem in _STOPWORDS or stem in picked:
            continue
        picked.append(stem)
        if len(picked) == MAX_KEYWORDS:
            break
    return tuple(picked)


def normalize_embedding(values: Sequence[float] | None) -> tuple[float, ...] | None:
    """유효한 임베딩을 단위 벡터로 바꾼다. 잘못된 벡터는 키워드 검색으로 폴백한다."""

    if values is None:
        return None
    vector = tuple(float(value) for value in values)
    if not vector or not all(math.isfinite(value) for value in vector):
        return None
    length = math.sqrt(sum(value * value for value in vector))
    if length <= 0.0:
        return None
    return tuple(value / length for value in vector)


def build_memory(
    kind: MemoryKind,
    text: str,
    *,
    importance: int | None = None,
    source_key: str | None = None,
    created_at: datetime | None = None,
    recalled_at: datetime | None = None,
    recall_count: int = 0,
    embedding: Sequence[float] | None = None,
    embedding_model: str | None = None,
) -> LongTermMemory | None:
    """후보 한 줄을 기억으로 만든다. 규칙을 어기면 `None` — 조용히 버린다.

    **숫자가 든 후보는 저장하지 않는다.** 기억은 검증된 사실이 아니라 지난 대화에서 알게 된
    것이라 `DialogueSpec.facts` 에 실을 수 없고, `facts` 밖의 숫자를 모델이 따라 말하면
    `dialogue.sanitize` 가 대사를 통째로 버려 폴백으로 떨어진다. 입구에서 숫자를 막으면
    `sanitize` 를 한 줄도 고치지 않고 두 규칙이 모두 성립한다.
    """

    collapsed = _WHITESPACE_PATTERN.sub(" ", text).strip()
    if not collapsed or len(collapsed) > MAX_MEMORY_TEXT:
        return None
    if _DIGIT_PATTERN.search(collapsed):
        return None

    resolved = _DEFAULT_IMPORTANCE[kind] if importance is None else importance
    stamped = created_at or datetime.now(UTC)
    return LongTermMemory(
        kind=kind,
        text=collapsed,
        keywords=_keywords(collapsed),
        importance=min(max(resolved, MIN_IMPORTANCE), MAX_IMPORTANCE),
        source_key=source_key,
        # 시간대가 없는 값이 섞이면 축출 정렬에서 비교가 터진다. 입구에서 UTC 로 맞춘다.
        created_at=_utc(stamped),
        recalled_at=None if recalled_at is None else _utc(recalled_at),
        recall_count=max(recall_count, 0),
        embedding=normalize_embedding(embedding),
        embedding_model=embedding_model,
    )


def _utc(moment: datetime) -> datetime:
    return moment if moment.tzinfo is not None else moment.replace(tzinfo=UTC)


def strength(memory: LongTermMemory, *, now: datetime) -> float:
    """기억이 지금 얼마나 살아 있는지. 축출과 회수 순위가 함께 보는 값이다.

    나이는 `created_at` 이 아니라 **마지막으로 불려 나온 시각**부터 센다. 자주 쓰이는
    기억은 나이를 먹지 않고, 만들어만 놓고 한 번도 안 쓰인 기억은 반감기마다 절반씩
    작아져 결국 새 기억에 자리를 내준다.
    """

    reference = memory.recalled_at or memory.created_at
    age_days = max((now - reference).total_seconds(), 0.0) / 86_400.0
    decayed = memory.importance * _IMPORTANCE_WEIGHT * float(0.5 ** (age_days / HALF_LIFE_DAYS))
    return decayed + min(memory.recall_count, MAX_RECALL_BONUS) * _RECALL_BONUS_WEIGHT


@dataclass(frozen=True, slots=True)
class MemoryExtractionSpec:
    """무엇을 기억으로 남길지 판단하는 데 필요한 전부.

    `recent_turns` 는 **아직 증류하지 않은 구간**이다. 작업 기억의 최근 몇 마디가 아니라
    전사에서 커서 뒤를 읽어 온 것이라, 추출 주기를 늘려도 앞쪽 턴이 사각지대에 빠지지 않는다.
    """

    recent_turns: tuple[ConversationTurn, ...]
    # 이미 아는 것. 넘기지 않으면 같은 사실을 매 번 새로 만들어 상한을 잡아먹는다.
    known: tuple[str, ...] = ()


class MemoryExtraction(BaseModel):
    """LLM이 반드시 지켜야 하는 기억 추출 결과.

    세션 요약은 여기 없다. 증분 추출은 몇 턴마다 도는 것이라 요약을 시키면 "대화 전체"가
    아니라 "마지막 몇 왕복"의 요약이 된다. 요약은 `SessionSummary` 가 대화당 한 번 맡는다.
    """

    model_config = ConfigDict(extra="forbid")

    # 기본값을 주지 않는다 — OpenAI strict 모드는 모든 프로퍼티가 required 여야 한다.
    profile: list[str] = Field(max_length=2)
    episode: str | None
    episode_importance: int = Field(ge=MIN_IMPORTANCE, le=MAX_IMPORTANCE)


EMPTY_EXTRACTION = MemoryExtraction(profile=[], episode=None, episode_importance=MIN_IMPORTANCE)


@dataclass(frozen=True, slots=True)
class SessionSummarySpec:
    """대화 하나를 한 줄로 줄이는 데 필요한 전부. `turns` 는 전사에서 온다."""

    turns: tuple[ConversationTurn, ...]


class SessionSummary(BaseModel):
    """LLM이 반드시 지켜야 하는 세션 요약 결과."""

    model_config = ConfigDict(extra="forbid")

    summary: str | None


EMPTY_SUMMARY = SessionSummary(summary=None)


@dataclass(frozen=True, slots=True)
class ConsolidationSpec:
    """상한에 닿은 기억들을 합치는 데 필요한 전부.

    `memories` 의 **인덱스가 곧 `ConsolidatedMemory.sources` 의 값**이다. 그래야 합쳐진
    줄이 어느 원본에서 왔는지 알 수 있고, 원본의 이력을 물려받을 수 있다.
    """

    memories: tuple[str, ...]


class ConsolidatedMemory(BaseModel):
    model_config = ConfigDict(extra="forbid")

    text: str
    sources: list[int]


class Consolidation(BaseModel):
    """LLM이 반드시 지켜야 하는 기억 통합 결과."""

    model_config = ConfigDict(extra="forbid")

    memories: list[ConsolidatedMemory]


EMPTY_CONSOLIDATION = Consolidation(memories=[])


def memories_from(
    extraction: MemoryExtraction,
    *,
    created_at: datetime | None = None,
) -> tuple[LongTermMemory, ...]:
    """추출 결과를 저장 가능한 기억들로 옮긴다. 규칙을 어긴 후보는 여기서 사라진다."""

    now = created_at or datetime.now(UTC)
    candidates = [build_memory("profile", text, created_at=now) for text in extraction.profile]
    if extraction.episode is not None:
        candidates.append(
            build_memory(
                "episode",
                extraction.episode,
                importance=extraction.episode_importance,
                created_at=now,
            )
        )
    return tuple(memory for memory in candidates if memory is not None)


def summary_memory(
    summary: SessionSummary,
    *,
    source_key: str,
    created_at: datetime | None = None,
) -> LongTermMemory | None:
    """세션 요약을 저장 가능한 기억으로 옮긴다. 규칙을 어기면 `None`."""

    if summary.summary is None:
        return None
    return build_memory(
        "session_summary",
        summary.summary,
        source_key=source_key,
        created_at=created_at or datetime.now(UTC),
    )


def consolidated(
    current: Sequence[LongTermMemory], result: Consolidation
) -> tuple[LongTermMemory, ...] | None:
    """통합 결과를 기억들로 옮긴다. **기억을 잃을 수 있으면 `None`** — 원본을 그대로 둔다.

    통합은 상한에 닿은 줄들을 합쳐 자리를 만드는 일이지 지우는 일이 아니다. LLM이 빈
    결과를 내거나, 범위 밖 인덱스를 가리키거나, 통과한 줄이 하나도 없거나, 결과가 원본보다
    줄지 않으면 **아무것도 하지 않는다.** 여기서 관대하면 한 번의 잘못된 응답이 그 사람의
    기억을 통째로 날린다.

    합쳐진 줄은 출처의 이력을 물려받는다 — 만들어진 시각은 가장 오래된 것, 회수 횟수는
    합, 마지막 회수는 가장 최근, 중요도는 가장 높은 것. 그러지 않으면 통합할 때마다 모든
    기억이 방금 만들어진 것처럼 보여 감쇠가 무의미해진다.
    """

    if not result.memories:
        return None

    rebuilt: list[LongTermMemory] = []
    used: set[int] = set()
    for item in result.memories:
        if not item.sources or any(index not in range(len(current)) for index in item.sources):
            return None
        sources = [current[index] for index in item.sources]
        # 여러 출처를 합친다면서 원문 하나만 그대로 돌려주면 다른 출처를 지우게 된다.
        # 의미 보존 전체를 자동 판정할 수는 없으므로, 최소한 이 명백한 축약은 거부한다.
        if len(sources) > 1 and item.text in {source.text for source in sources}:
            return None
        source_keys = {source.source_key for source in sources}
        recalls = [source.recalled_at for source in sources if source.recalled_at is not None]
        memory = build_memory(
            # 가장 오래 남아야 하는 종류를 따른다(profile → episode → session_summary).
            min(sources, key=lambda source: _KIND_ORDER[source.kind]).kind,
            item.text,
            importance=max(source.importance for source in sources),
            source_key=source_keys.pop() if len(source_keys) == 1 else None,
            created_at=min(source.created_at for source in sources),
            recalled_at=max(recalls) if recalls else None,
            recall_count=sum(source.recall_count for source in sources),
        )
        if memory is None:
            # 규칙을 어긴 줄만 버린다. 그 출처들은 통합되지 않은 채 원본으로 남는다.
            continue
        rebuilt.append(memory)
        used.update(item.sources)

    if not rebuilt:
        return None
    kept = [memory for index, memory in enumerate(current) if index not in used]
    merged = tuple(kept) + tuple(rebuilt)
    if len({memory.text for memory in merged}) != len(merged):
        return None
    return merged if len(merged) < len(current) else None


class LongTermStore(Protocol):
    """플레이어별 장기기억 보관소.

    `ConversationStore` 와 달리 비동기다 — 구현이 파일이나 DB를 만지기 때문이고, 이벤트
    루프를 막지 않으려면 그 사실이 인터페이스에 드러나야 한다.
    """

    async def recall(
        self,
        player_key: str,
        *,
        query: str,
        limit: int,
        query_embedding: Sequence[float] | None = None,
        embedding_model: str | None = None,
    ) -> tuple[LongTermMemory, ...]:
        """이번 발화와 관련이 깊은 순으로 최대 `limit` 개를 돌려준다.

        **질의가 비어 있으면 회수 통계를 남기지 않는다.** 빈 질의는 "무엇을 아는지 훑는다"
        (추출의 [이미 아는 것], 통합 입력)이지 "이 기억이 쓸모 있었다" 가 아니다. 그것까지
        세면 모든 기억이 똑같이 자주 불린 것이 되어 감쇠가 무의미해진다.
        """
        ...

    async def remember(self, player_key: str, memories: Sequence[LongTermMemory]) -> None:
        """기억을 병합해 보관한다. 상한을 넘으면 힘이 약한 것부터 버린다."""
        ...

    async def replace_all(self, player_key: str, memories: Sequence[LongTermMemory]) -> None:
        """보관 중인 기억을 통째로 갈아 끼운다. 통합 결과를 반영하는 유일한 경로다.

        `remember` 는 병합이라 "줄어든 목록" 을 표현할 수 없다. 지우는 힘을 가진 메서드라
        호출자가 안전을 책임진다 — `consolidated()` 의 방어가 그것이다.
        """
        ...


def _semantic_bonus(
    memory: LongTermMemory,
    query_embedding: tuple[float, ...] | None,
    *,
    embedding_model: str | None,
) -> float:
    """같은 임베딩 모델의 벡터만 의미 점수에 참여시킨다."""

    if query_embedding is None:
        return 0.0
    if (
        memory.embedding is None
        or (embedding_model is not None and memory.embedding_model != embedding_model)
        or len(memory.embedding) != len(query_embedding)
    ):
        # 아직 임베딩이 없거나 모델이 바뀐 기억도 검색에서 사라지면 안 된다.
        return _SEMANTIC_FLOOR
    similarity = sum(
        left * right for left, right in zip(memory.embedding, query_embedding, strict=True)
    )
    return similarity * _SEMANTIC_WEIGHT


def _score(
    memory: LongTermMemory,
    normalized_query: str,
    *,
    now: datetime,
    query_embedding: tuple[float, ...] | None = None,
    embedding_model: str | None = None,
) -> float:
    """키워드·의미 겹침에 가중치를 주고 지금의 힘을 더한다. 점수는 항상 0보다 크다.

    질의 벡터가 없으면 오늘의 키워드 순위와 정확히 같다. 벡터가 있어도 벡터가 없는
    기억에는 작은 바닥값을 주어 임베딩 이전의 기억이 통째로 밀려나지 않게 한다.
    """

    hits = sum(1 for keyword in memory.keywords if keyword in normalized_query)
    return (
        hits * 2
        + strength(memory, now=now)
        + _semantic_bonus(memory, query_embedding, embedding_model=embedding_model)
    )


def rank(
    memories: Sequence[LongTermMemory],
    *,
    query: str,
    limit: int,
    now: datetime | None = None,
    query_embedding: Sequence[float] | None = None,
    embedding_model: str | None = None,
) -> tuple[LongTermMemory, ...]:
    """회수 순위를 매긴다. 같은 입력은 항상 같은 순서를 낸다 — 동점을 끝까지 가른다."""

    if limit <= 0:
        return ()
    moment = now or datetime.now(UTC)
    normalized = _normalize(query)
    normalized_embedding = normalize_embedding(query_embedding)
    ordered = sorted(
        memories,
        key=lambda memory: (
            -_score(
                memory,
                normalized,
                now=moment,
                query_embedding=normalized_embedding,
                embedding_model=embedding_model,
            ),
            -memory.created_at.timestamp(),
            _KIND_ORDER[memory.kind],
            memory.text,
        ),
    )
    return tuple(ordered[:limit])


def merge(
    current: Sequence[LongTermMemory],
    incoming: Sequence[LongTermMemory],
    *,
    now: datetime | None = None,
) -> tuple[LongTermMemory, ...]:
    """새 기억을 병합하고 상한을 지킨다.

    같은 본문은 새 것이 옛 것을 갈아치우되 **회수 이력은 물려받는다** — 같은 사실이 다시
    추출됐다고 그동안 몇 번 쓰였는지를 잊으면 감쇠가 매번 처음부터 시작한다. 세션 요약은
    `source_key` 하나당 한 줄만 남는다 — 한 대화가 길어질수록 요약이 쌓이면 상한이
    요약으로만 차 버린다.
    """

    moment = now or datetime.now(UTC)
    merged = list(current)
    for memory in incoming:
        inherited = next((kept for kept in merged if kept.text == memory.text), None)
        merged = [
            kept
            for kept in merged
            if kept.text != memory.text
            and not (
                memory.kind == "session_summary"
                and kept.kind == "session_summary"
                and kept.source_key == memory.source_key
            )
        ]
        if inherited is not None:
            memory = replace(
                memory,
                recall_count=max(memory.recall_count, inherited.recall_count),
                recalled_at=inherited.recalled_at or memory.recalled_at,
                embedding=memory.embedding or inherited.embedding,
                embedding_model=memory.embedding_model or inherited.embedding_model,
            )
        merged.append(memory)

    if len(merged) <= MAX_MEMORIES_PER_PLAYER:
        return tuple(merged)
    # 힘이 약한 것부터 버리되, 남는 것들의 시간 순서는 흐트러뜨리지 않는다.
    survivors = set(
        sorted(merged, key=lambda m: (strength(m, now=moment), m.created_at), reverse=True)[
            :MAX_MEMORIES_PER_PLAYER
        ]
    )
    return tuple(memory for memory in merged if memory in survivors)


_FILE_VERSION = 2
# v1 은 회수 이력이 없던 포맷이다. 읽을 수 있게 남겨 둔다 — 포맷이 올랐다고 이미 쌓인
# 기억을 버리면, 기억을 버리지 않으려고 만든 층이 스스로 기억을 버린다.
_READABLE_VERSIONS = frozenset({1, _FILE_VERSION})


class _MemoryRecord(BaseModel):
    """파일에 적히는 한 줄. `keywords` 는 본문에서 파생되므로 저장하지 않는다."""

    model_config = ConfigDict(extra="ignore")

    kind: MemoryKind
    text: str
    importance: int
    source_key: str | None = None
    created_at: datetime
    # v1 파일에는 없다. 기본값이 곧 "한 번도 불려 나온 적 없음" 이다.
    recalled_at: datetime | None = None
    recall_count: int = 0
    embedding: list[float] | None = None
    embedding_model: str | None = None


class _MemoryFile(BaseModel):
    model_config = ConfigDict(extra="ignore")

    version: int
    memories: list[_MemoryRecord] = []


def read_memory_file(path: Path) -> tuple[LongTermMemory, ...]:
    """파일을 읽어 기억으로 옮긴다. 읽을 수 없으면 빈 기억이다.

    기억 파일 하나가 깨졌다고 대화가 실패해서는 안 된다. 손상·구버전·권한 문제 모두
    "아직 아무것도 기억하지 못한다" 로 취급한다.
    """

    try:
        raw = path.read_text(encoding="utf-8")
    except OSError:
        return ()
    try:
        parsed = _MemoryFile.model_validate_json(raw)
    except ValueError:
        return ()
    if parsed.version not in _READABLE_VERSIONS:
        return ()

    # 저장할 때의 규칙(길이·숫자 금지)을 읽을 때 다시 적용한다. 손으로 고친 파일이나
    # 규칙이 바뀌기 전에 쓰인 파일이 규칙을 우회하지 못한다.
    rebuilt = (
        build_memory(
            record.kind,
            record.text,
            importance=record.importance,
            source_key=record.source_key,
            created_at=record.created_at,
            recalled_at=record.recalled_at,
            recall_count=record.recall_count,
            embedding=record.embedding,
            embedding_model=record.embedding_model,
        )
        for record in parsed.memories
    )
    return tuple(memory for memory in rebuilt if memory is not None)


def _write_file(path: Path, memories: Sequence[LongTermMemory]) -> None:
    """임시 파일에 쓰고 원자적으로 바꿔 끼운다. 중간에 죽어도 반쪽 파일이 남지 않는다."""

    path.parent.mkdir(parents=True, exist_ok=True)
    payload = _MemoryFile(
        version=_FILE_VERSION,
        memories=[
            _MemoryRecord(
                kind=memory.kind,
                text=memory.text,
                importance=memory.importance,
                source_key=memory.source_key,
                created_at=memory.created_at,
                recalled_at=memory.recalled_at,
                recall_count=memory.recall_count,
                embedding=None if memory.embedding is None else list(memory.embedding),
                embedding_model=memory.embedding_model,
            )
            for memory in memories
        ],
    )
    temporary = path.with_name(f"{path.name}.tmp")
    temporary.write_text(payload.model_dump_json(indent=2), encoding="utf-8")
    temporary.replace(path)


class FileLongTermStore:
    """플레이어마다 JSON 파일 하나를 두는 기본 구현.

    플레이어당 최대 `MAX_MEMORIES_PER_PLAYER` 줄, 한 줄 `MAX_MEMORY_TEXT` 자라 파일이 몇 KB
    수준이다. 그래서 덧붙이지 않고 매번 통째로 다시 쓴다 — JSONL 에 컴팩션을 붙이는 것보다
    훨씬 단순하고, 갱신이 곧 원자적 교체 한 번이 된다.

    읽기는 상한 있는 캐시를 거치므로 같은 플레이어의 두 번째 턴부터는 디스크를 만지지 않는다.
    쓰기는 백그라운드 추출에서만 일어나 대사 지연에 들어가지 않는다.
    """

    def __init__(self, *, directory: Path, max_players: int) -> None:
        self._directory = directory
        self._max_players = max_players
        # 삽입 순서를 유지하는 dict 라, 가장 오래 전에 쓰인 항목이 앞에 온다.
        self._cache: dict[str, tuple[LongTermMemory, ...]] = {}
        # 플레이어별 직렬화 장치. 한 파일의 read-modify-write 가 겹치면 갱신이 사라진다.
        self._locks: dict[str, asyncio.Lock] = {}
        self._lock_users: dict[str, int] = {}

    async def recall(
        self,
        player_key: str,
        *,
        query: str,
        limit: int,
        query_embedding: Sequence[float] | None = None,
        embedding_model: str | None = None,
    ) -> tuple[LongTermMemory, ...]:
        if not player_key or limit <= 0:
            return ()
        now = datetime.now(UTC)
        memories = await self._memories(player_key)
        picked = rank(
            memories,
            query=query,
            limit=limit,
            now=now,
            query_embedding=query_embedding,
            embedding_model=embedding_model,
        )
        if not query.strip() or not picked:
            return picked
        return self._record_recall(player_key, memories, picked, now=now)

    async def remember(self, player_key: str, memories: Sequence[LongTermMemory]) -> None:
        if not player_key or not memories:
            return
        async with self._player_lock(player_key):
            current = self._cache.get(player_key)
            if current is None:
                current = await asyncio.to_thread(read_memory_file, self._path(player_key))
            merged = merge(current, memories)
            if merged == current:
                return
            self._cache_put(player_key, merged)
            await asyncio.to_thread(_write_file, self._path(player_key), merged)

    async def replace_all(self, player_key: str, memories: Sequence[LongTermMemory]) -> None:
        if not player_key or not memories:
            return
        async with self._player_lock(player_key):
            replacement = tuple(memories)[:MAX_MEMORIES_PER_PLAYER]
            self._cache_put(player_key, replacement)
            await asyncio.to_thread(_write_file, self._path(player_key), replacement)

    def _record_recall(
        self,
        player_key: str,
        memories: tuple[LongTermMemory, ...],
        picked: tuple[LongTermMemory, ...],
        *,
        now: datetime,
    ) -> tuple[LongTermMemory, ...]:
        """불려 나온 기억의 회수 이력을 **캐시에만** 새긴다.

        여기서 파일을 쓰면 매 턴 디스크가 돈다. 다음 `remember` 나 `replace_all` 이 쓸 때
        같이 나가고, 그때까지 프로세스가 죽거나 캐시가 밀려나면 이 통계는 사라진다.
        **best-effort 로 둔다** — 회수 이력은 순위를 다듬는 휴리스틱이지 기억 자체가 아니다.
        """

        updated = {
            memory.text: replace(memory, recalled_at=now, recall_count=memory.recall_count + 1)
            for memory in picked
        }
        self._cache_put(player_key, tuple(updated.get(m.text, m) for m in memories))
        return tuple(updated[memory.text] for memory in picked)

    def _path(self, player_key: str) -> Path:
        return self._directory / f"{player_key}.json"

    async def _memories(self, player_key: str) -> tuple[LongTermMemory, ...]:
        cached = self._cache.get(player_key)
        if cached is not None:
            self._cache_put(player_key, cached)
            return cached
        async with self._player_lock(player_key):
            # 락을 기다리는 사이 다른 태스크가 이미 읽어 왔을 수 있다.
            cached = self._cache.get(player_key)
            if cached is not None:
                return cached
            loaded = await asyncio.to_thread(read_memory_file, self._path(player_key))
            self._cache_put(player_key, loaded)
            return loaded

    def _cache_put(self, player_key: str, memories: tuple[LongTermMemory, ...]) -> None:
        """캐시를 갱신하고 상한을 넘으면 가장 오래 안 쓰인 플레이어를 떨군다.

        캐시일 뿐이라 떨궈도 기억은 파일에 남는다. 다음 회수에서 다시 읽어 온다.
        """

        self._cache.pop(player_key, None)
        self._cache[player_key] = memories
        while len(self._cache) > self._max_players:
            self._cache.pop(next(iter(self._cache)))

    @asynccontextmanager
    async def _player_lock(self, player_key: str) -> AsyncIterator[None]:
        """한 플레이어 파일의 읽기·병합·쓰기를 직렬화한다.

        락은 쓰는 사람이 없어지면 즉시 버린다. 플레이어마다 남겨 두면 캐시와 달리 상한이 없다.
        """

        lock = self._locks.get(player_key)
        if lock is None:
            lock = asyncio.Lock()
            self._locks[player_key] = lock
        self._lock_users[player_key] = self._lock_users.get(player_key, 0) + 1
        try:
            async with lock:
                yield
        finally:
            self._lock_users[player_key] -= 1
            if self._lock_users[player_key] == 0:
                del self._lock_users[player_key]
                del self._locks[player_key]
