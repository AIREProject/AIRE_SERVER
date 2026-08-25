"""세션과 재시작을 넘는 장기기억 검증.

작업 기억과 달리 이 저장소는 디스크에 남으므로, 무엇을 저장하지 **않는지**가
무엇을 저장하는지만큼 중요하다.
"""

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from app.brain import CompanionBrain, CompanionTurn
from app.brain.dialogue import DialogueSpec, prompt_memory_claim, sanitize
from app.brain.llm import MockLLMProvider
from app.brain.memory import (
    EMPTY_CONSOLIDATION,
    EMPTY_EXTRACTION,
    EMPTY_SUMMARY,
    HALF_LIFE_DAYS,
    MAX_MEMORIES_PER_PLAYER,
    MAX_MEMORY_TEXT,
    ConsolidatedMemory,
    Consolidation,
    ConsolidationSpec,
    FileLongTermStore,
    LongTermMemory,
    MemoryExtraction,
    MemoryExtractionSpec,
    SessionSummary,
    SessionSummarySpec,
    build_memory,
    consolidated,
    memories_from,
    merge,
    rank,
    strength,
    summary_memory,
)
from app.brain.transcript import FileTranscriptStore
from app.models import CommandType

_NOW = datetime(2026, 7, 30, 12, 0, tzinfo=UTC)

# 한글 음절은 연속하지 않아 `chr(ord('가') + n)` 이 '나' 를 주지 않는다. 눈으로 확인하는
# 단언에는 이 이름들을 쓴다.
_LABELS = ("가", "나", "다", "라", "마", "바", "사", "아")


def later(seconds: float) -> datetime:
    """증류 루프에 넘길 시계.

    **테스트는 잠들지 않는다.** 판정이 전부 `CompanionBrain._drain(now=...)` 안에 있으므로
    시계를 넘겨 직접 부른다. 기준은 고정 시각이 아니라 실제 지금이다 — 대기열의
    `last_turn_at` 은 턴이 실제로 오간 시각이라 둘을 섞으면 부호가 뒤집힌다.
    """

    return datetime.now(UTC) + timedelta(seconds=seconds)


def make_memory(
    text: str,
    *,
    kind: str = "profile",
    importance: int = 2,
    source_key: str | None = None,
    minutes_ago: float = 0,
    days_ago: float = 0,
    recall_count: int = 0,
    recalled_days_ago: float | None = None,
) -> LongTermMemory:
    age = timedelta(minutes=minutes_ago, days=days_ago)
    memory = build_memory(
        kind,  # type: ignore[arg-type]
        text,
        importance=importance,
        source_key=source_key,
        created_at=_NOW - age,
        recalled_at=(
            None if recalled_days_ago is None else _NOW - timedelta(days=recalled_days_ago)
        ),
        recall_count=recall_count,
    )
    assert memory is not None, f"기억으로 만들 수 없는 본문이다: {text!r}"
    return memory


def make_store(directory: Path, *, max_players: int = 10) -> FileLongTermStore:
    return FileLongTermStore(directory=directory, max_players=max_players)


def file_names(directory: Path) -> list[str]:
    """디렉터리에 실제로 남은 파일 이름. 동기 함수로 두는 이유는 ruff `ASYNC240` 이다."""

    return sorted(path.name for path in directory.iterdir())


# --- 무엇을 기억으로 받아들이는가 -------------------------------------------------


def test_candidate_with_a_number_is_rejected() -> None:
    """숫자가 든 기억은 저장하지 않는다.

    기억은 확정 사실이 아니라 `facts` 에 실을 수 없고, `facts` 밖의 숫자를 마코가 따라
    말하면 `sanitize` 가 대사를 통째로 버린다. 입구에서 막아야 두 규칙이 함께 성립한다.
    """

    assert build_memory("profile", "플레이어는 나무를 20개씩 모은다") is None
    assert build_memory("profile", "플레이어는 나무를 넉넉히 모아 둔다") is not None


def test_sanitize_still_rejects_numbers_that_are_not_in_the_facts() -> None:
    """숫자 금지 규칙이 있어도 `sanitize` 의 숫자 검사는 그대로 남아 있어야 한다."""

    spec = DialogueSpec(scene="recipe", fallback="확인된 제작법이 없어.", facts=())

    assert sanitize("철괴 3개가 필요해.", spec) is None


def test_overlong_candidate_is_rejected() -> None:
    assert build_memory("profile", "가" * (MAX_MEMORY_TEXT + 1)) is None
    assert build_memory("profile", "가" * MAX_MEMORY_TEXT) is not None


def test_blank_candidate_is_rejected() -> None:
    assert build_memory("episode", "   ") is None


def test_importance_is_clamped_into_range() -> None:
    """ERD의 1~10 범위를 벗어난 중요도도 순위 계산을 흔들지 않아야 한다."""

    assert make_memory("아무 말", importance=99).importance == 10
    assert make_memory("아무 말", importance=-4).importance == 1


def test_naive_timestamp_becomes_utc() -> None:
    """시간대 없는 값이 섞이면 축출 정렬에서 비교가 터진다."""

    memory = build_memory("profile", "무언가", created_at=datetime(2026, 7, 30, 12, 0))

    assert memory is not None
    assert memory.created_at.tzinfo is not None


def test_extraction_becomes_memories_of_each_kind() -> None:
    extraction = MemoryExtraction(
        profile=["플레이어는 돌보다 나무를 좋아한다", "플레이어는 밤에 움직이기 싫어한다"],
        episode="같이 폐광 마을을 둘러봤다",
        episode_importance=3,
    )

    memories = memories_from(extraction, created_at=_NOW)

    assert [memory.kind for memory in memories] == ["profile", "profile", "episode"]
    assert memories[2].importance == 3
    # 증분 추출은 어느 대화에서 왔는지 남기지 않는다. 그건 요약만의 것이다.
    assert memories[0].source_key is None


def test_the_summary_knows_which_conversation_it_came_from() -> None:
    """그래야 같은 대화의 옛 요약을 새 요약이 갈아치울 수 있다."""

    memory = summary_memory(
        SessionSummary(summary="채집 이야기를 나누다 마을을 돌아봤다"),
        source_key="conv-1",
        created_at=_NOW,
    )

    assert memory is not None
    assert memory.kind == "session_summary"
    assert memory.source_key == "conv-1"


def test_an_empty_summary_produces_nothing() -> None:
    assert summary_memory(EMPTY_SUMMARY, source_key="conv-1") is None
    assert summary_memory(SessionSummary(summary="철괴 3개"), source_key="conv-1") is None


def test_empty_extraction_produces_nothing() -> None:
    assert memories_from(EMPTY_EXTRACTION) == ()


def test_extraction_drops_candidates_that_break_the_rules() -> None:
    """규칙을 어긴 후보 하나가 나머지까지 버리게 하지 않는다."""

    extraction = MemoryExtraction(
        profile=["플레이어는 나무를 5개 모았다", "플레이어는 서두르는 편이다"],
        episode=None,
        episode_importance=1,
    )

    memories = memories_from(extraction, created_at=_NOW)

    assert [memory.text for memory in memories] == ["플레이어는 서두르는 편이다"]


# --- 무엇을 먼저 꺼내는가 ---------------------------------------------------------


def test_recall_prefers_memories_that_share_words_with_the_utterance() -> None:
    memories = [
        make_memory("플레이어는 밤을 무서워한다", minutes_ago=1),
        make_memory("플레이어는 나무를 모으는 걸 좋아한다", minutes_ago=10),
    ]

    ranked = rank(memories, query="나무 좀 캐 줄래", limit=1, now=_NOW)

    assert [memory.text for memory in ranked] == ["플레이어는 나무를 모으는 걸 좋아한다"]


def test_recall_matches_across_korean_particles() -> None:
    """'나무를' 로 저장된 기억이 '나무는' 이라는 발화에 걸려야 한다."""

    memories = [make_memory("플레이어는 나무를 좋아한다")]

    assert rank(memories, query="나무는 어디 있어", limit=1, now=_NOW) == tuple(memories)


def test_recall_fills_the_limit_even_without_a_keyword_hit() -> None:
    """겹치는 말이 없어도 자리는 채운다. 프로필 사실은 대개 지금 말과 겹치지 않는다."""

    memories = [
        make_memory("플레이어는 서두르는 편이다", importance=3),
        make_memory("플레이어는 조용한 곳을 좋아한다", importance=1),
    ]

    ranked = rank(memories, query="전혀 관계없는 말", limit=2, now=_NOW)

    assert [memory.text for memory in ranked] == [
        "플레이어는 서두르는 편이다",
        "플레이어는 조용한 곳을 좋아한다",
    ]


def test_recall_breaks_ties_by_recency_then_kind() -> None:
    """같은 입력은 항상 같은 순서를 내야 한다 — 회귀를 눈으로 볼 수 있어야 한다."""

    memories = [
        make_memory("지난 대화 요약", kind="session_summary", importance=2, minutes_ago=0),
        make_memory("어떤 사건", kind="episode", importance=2, minutes_ago=0),
        make_memory("오래된 사실", importance=2, minutes_ago=60),
    ]

    ranked = rank(memories, query="", limit=3, now=_NOW)

    assert [memory.kind for memory in ranked] == ["episode", "session_summary", "profile"]


def test_recall_limit_of_zero_returns_nothing() -> None:
    assert rank([make_memory("무언가")], query="무언가", limit=0, now=_NOW) == ()


def test_a_keyword_hit_outranks_an_equally_strong_memory() -> None:
    """감쇠를 얹어도 힘이 같다면 '지금 말과 맞는가' 가 순위를 가른다."""

    memories = [
        make_memory("플레이어는 서두르는 편이다", importance=2),
        make_memory("플레이어는 나무를 좋아한다", importance=2),
    ]

    ranked = rank(memories, query="나무 좀 캐 줘", limit=1, now=_NOW)

    assert [memory.text for memory in ranked] == ["플레이어는 나무를 좋아한다"]


# --- 시간이 지나면 어떻게 되는가 ---------------------------------------------------


def test_a_memory_halves_in_strength_after_one_half_life() -> None:
    fresh = make_memory("어제 알게 된 사실", importance=2)
    old = make_memory("오래된 사실", importance=2, days_ago=HALF_LIFE_DAYS)

    assert strength(old, now=_NOW) == pytest.approx(strength(fresh, now=_NOW) / 2)


def test_being_recalled_makes_a_memory_young_again() -> None:
    """자주 불려 나오는 기억은 나이를 먹지 않는다."""

    forgotten = make_memory("아무도 안 찾은 사실", days_ago=HALF_LIFE_DAYS * 2)
    used = make_memory(
        "자주 불린 사실", days_ago=HALF_LIFE_DAYS * 2, recall_count=3, recalled_days_ago=0
    )

    assert strength(used, now=_NOW) > strength(forgotten, now=_NOW)


# --- 어떻게 쌓이고 줄어드는가 -----------------------------------------------------


def test_merge_replaces_a_memory_with_the_same_text() -> None:
    old = make_memory("플레이어는 나무를 좋아한다", importance=1, minutes_ago=60)
    new = make_memory("플레이어는 나무를 좋아한다", importance=3, minutes_ago=0)

    merged = merge([old], [new], now=_NOW)

    assert merged == (new,)


def test_merge_keeps_the_recall_history_of_the_memory_it_replaces() -> None:
    """같은 사실이 다시 추출됐다고 그동안 몇 번 쓰였는지를 잊으면 감쇠가 처음부터 시작한다."""

    old = make_memory(
        "플레이어는 나무를 좋아한다", minutes_ago=60, recall_count=4, recalled_days_ago=1
    )
    new = make_memory("플레이어는 나무를 좋아한다", minutes_ago=0)

    merged = merge([old], [new], now=_NOW)

    assert merged[0].recall_count == 4
    assert merged[0].recalled_at == old.recalled_at


def test_merge_keeps_one_summary_per_conversation() -> None:
    """한 대화가 길어질수록 요약이 쌓이면 상한이 요약으로만 차 버린다."""

    old = make_memory("초반 이야기", kind="session_summary", source_key="conv-1", minutes_ago=60)
    new = make_memory("전체 이야기", kind="session_summary", source_key="conv-1", minutes_ago=0)
    other = make_memory("다른 세션", kind="session_summary", source_key="conv-2", minutes_ago=30)

    merged = merge([old, other], [new], now=_NOW)

    assert set(merged) == {other, new}


def test_merge_evicts_the_weakest_first() -> None:
    filler = [
        make_memory(f"덜 중요한 사실 {chr(ord('가') + index)}", importance=1, minutes_ago=index)
        for index in range(MAX_MEMORIES_PER_PLAYER)
    ]
    important = make_memory("오래 기억할 사실", importance=3)

    merged = merge(filler, [important], now=_NOW)

    assert len(merged) == MAX_MEMORIES_PER_PLAYER
    assert important in merged
    # 같은 중요도끼리는 가장 오래된 것이 밀려난다.
    assert filler[-1] not in merged


def test_a_recalled_memory_survives_an_older_never_recalled_one() -> None:
    """회수 결과가 축출에 영향을 주지 않으면 매번 불려 나오는 기억이 그냥 밀려난다."""

    forgotten = make_memory("아무도 안 찾은 사실", importance=2, days_ago=HALF_LIFE_DAYS * 3)
    used = make_memory(
        "자주 불린 사실",
        importance=2,
        days_ago=HALF_LIFE_DAYS * 3,
        recall_count=5,
        recalled_days_ago=0,
    )
    filler = [
        make_memory(f"채우는 사실 {chr(ord('가') + index)}", importance=3)
        for index in range(MAX_MEMORIES_PER_PLAYER - 1)
    ]

    merged = merge([forgotten, used, *filler], [], now=_NOW)

    assert used in merged
    assert forgotten not in merged


# --- 상한에 닿았을 때 합치는가 -----------------------------------------------------


def test_consolidation_inherits_the_history_of_its_sources() -> None:
    """물려받지 않으면 통합할 때마다 모든 기억이 방금 만들어진 것처럼 보인다."""

    current = [
        make_memory("나무를 좋아한다", importance=1, days_ago=10, recall_count=2),
        make_memory(
            "돌보다 나무를 자주 캔다", importance=3, days_ago=2, recall_count=1,
            recalled_days_ago=1,
        ),
    ]
    result = Consolidation(
        memories=[ConsolidatedMemory(text="나무를 좋아해서 돌보다 자주 캔다", sources=[0, 1])]
    )

    merged = consolidated(current, result)

    assert merged is not None
    assert [memory.text for memory in merged] == ["나무를 좋아해서 돌보다 자주 캔다"]
    assert merged[0].created_at == current[0].created_at
    assert merged[0].recall_count == 3
    assert merged[0].recalled_at == current[1].recalled_at
    assert merged[0].importance == 3


def test_consolidation_leaves_untouched_memories_alone() -> None:
    current = [make_memory(f"사실 {label}") for label in _LABELS[:3]]
    result = Consolidation(memories=[ConsolidatedMemory(text="합친 사실", sources=[0, 2])])

    merged = consolidated(current, result)

    assert merged is not None
    assert [memory.text for memory in merged] == ["사실 나", "합친 사실"]


@pytest.mark.parametrize(
    "result",
    [
        pytest.param(EMPTY_CONSOLIDATION, id="빈 결과"),
        pytest.param(
            Consolidation(memories=[ConsolidatedMemory(text="합친 사실", sources=[0, 9])]),
            id="범위 밖 인덱스",
        ),
        pytest.param(
            Consolidation(memories=[ConsolidatedMemory(text="합친 사실", sources=[])]),
            id="출처 없음",
        ),
        pytest.param(
            Consolidation(memories=[ConsolidatedMemory(text="철괴 3개", sources=[0, 1])]),
            id="규칙 위반",
        ),
        pytest.param(
            Consolidation(memories=[ConsolidatedMemory(text="합친 사실", sources=[0])]),
            id="줄지 않음",
        ),
    ],
)
def test_consolidation_can_never_delete_a_memory(result: Consolidation) -> None:
    """통합은 자리를 만드는 일이지 지우는 일이 아니다. 의심스러우면 원본을 그대로 둔다."""

    current = [make_memory("사실 가"), make_memory("사실 나")]

    assert consolidated(current, result) is None


# --- 파일과 격리 -----------------------------------------------------------------


async def test_memories_survive_a_new_store_over_the_same_directory(tmp_path: Path) -> None:
    """재시작을 대신하는 검증 — 저장소를 새로 만들어도 기억이 남아야 한다."""

    await make_store(tmp_path).remember("player-a", [make_memory("플레이어는 밤을 싫어한다")])

    recalled = await make_store(tmp_path).recall("player-a", query="밤", limit=3)

    assert [memory.text for memory in recalled] == ["플레이어는 밤을 싫어한다"]


async def test_players_do_not_share_memories(tmp_path: Path) -> None:
    store = make_store(tmp_path)

    await store.remember("player-a", [make_memory("A 의 기억")])
    await store.remember("player-b", [make_memory("B 의 기억")])

    assert [memory.text for memory in await store.recall("player-a", query="", limit=5)] == [
        "A 의 기억"
    ]


async def test_empty_player_key_touches_nothing(tmp_path: Path) -> None:
    """빈 키는 '이 호출자에게는 장기기억이 없다' 는 뜻이다. 공유 항목을 만들지 않는다."""

    store = make_store(tmp_path)

    await store.remember("", [make_memory("아무 기억")])

    assert await store.recall("", query="아무", limit=3) == ()
    assert file_names(tmp_path) == []


async def test_nothing_is_written_until_there_is_something_to_remember(tmp_path: Path) -> None:
    """켜 두기만 해서는 디렉터리가 생기지 않는다 — 기본 공급자는 아무것도 추출하지 않는다."""

    directory = tmp_path / "memories"

    assert await make_store(directory).recall("player-a", query="무엇", limit=3) == ()
    assert not directory.exists()


async def test_a_corrupt_file_reads_as_no_memories(tmp_path: Path) -> None:
    """기억 파일 하나가 깨졌다고 대화가 실패해서는 안 된다."""

    (tmp_path / "player-a.json").write_text("{ 이건 JSON 이 아니다", encoding="utf-8")

    assert await make_store(tmp_path).recall("player-a", query="무엇", limit=3) == ()


async def test_a_hand_edited_file_cannot_smuggle_in_a_number(tmp_path: Path) -> None:
    """저장할 때의 규칙을 읽을 때 다시 적용한다."""

    (tmp_path / "player-a.json").write_text(
        '{"version": 1, "memories": ['
        '{"kind": "profile", "text": "철괴 3개가 필요하다", "importance": 3,'
        ' "created_at": "2026-07-30T12:00:00Z"},'
        '{"kind": "profile", "text": "플레이어는 조심스럽다", "importance": 3,'
        ' "created_at": "2026-07-30T12:00:00Z"}]}',
        encoding="utf-8",
    )

    recalled = await make_store(tmp_path).recall("player-a", query="", limit=5)

    assert [memory.text for memory in recalled] == ["플레이어는 조심스럽다"]


async def test_a_file_from_another_version_reads_as_no_memories(tmp_path: Path) -> None:
    (tmp_path / "player-a.json").write_text(
        '{"version": 99, "memories": [{"kind": "profile", "text": "무언가",'
        ' "importance": 3, "created_at": "2026-07-30T12:00:00Z"}]}',
        encoding="utf-8",
    )

    assert await make_store(tmp_path).recall("player-a", query="", limit=5) == ()


async def test_a_v1_file_still_reads(tmp_path: Path) -> None:
    """포맷이 올랐다고 이미 쌓인 기억을 버리면, 기억을 지키려고 만든 층이 기억을 버린다."""

    (tmp_path / "player-a.json").write_text(
        '{"version": 1, "memories": [{"kind": "profile", "text": "플레이어는 조심스럽다",'
        ' "importance": 3, "created_at": "2026-07-30T12:00:00Z"}]}',
        encoding="utf-8",
    )

    recalled = await make_store(tmp_path).recall("player-a", query="", limit=5)

    assert [memory.text for memory in recalled] == ["플레이어는 조심스럽다"]
    # v1 에는 회수 이력이 없다. 기본값이 곧 "한 번도 불려 나온 적 없음" 이다.
    assert recalled[0].recall_count == 0
    assert recalled[0].recalled_at is None


async def test_recalling_a_memory_records_that_it_was_used(tmp_path: Path) -> None:
    store = make_store(tmp_path)
    await store.remember("player-a", [make_memory("플레이어는 밤을 싫어한다")])

    first = await store.recall("player-a", query="밤이 무서워", limit=1)
    second = await store.recall("player-a", query="밤이 무서워", limit=1)

    assert first[0].recall_count == 1
    assert second[0].recall_count == 2
    assert second[0].recalled_at is not None


async def test_an_empty_query_does_not_count_as_a_recall(tmp_path: Path) -> None:
    """빈 질의는 '무엇을 아는지 훑는다' 이지 '이 기억이 쓸모 있었다' 가 아니다."""

    store = make_store(tmp_path)
    await store.remember("player-a", [make_memory("플레이어는 밤을 싫어한다")])

    await store.recall("player-a", query="", limit=5)
    recalled = await store.recall("player-a", query="", limit=5)

    assert recalled[0].recall_count == 0


async def test_replace_all_swaps_the_whole_file(tmp_path: Path) -> None:
    store = make_store(tmp_path)
    await store.remember("player-a", [make_memory("사실 가"), make_memory("사실 나")])

    await store.replace_all("player-a", [make_memory("합친 사실")])

    recalled = await make_store(tmp_path).recall("player-a", query="", limit=5)
    assert [memory.text for memory in recalled] == ["합친 사실"]


async def test_writing_leaves_no_temporary_file_behind(tmp_path: Path) -> None:
    await make_store(tmp_path).remember("player-a", [make_memory("무언가")])

    assert file_names(tmp_path) == ["player-a.json"]


async def test_evicting_the_cache_does_not_lose_memories(tmp_path: Path) -> None:
    """캐시는 캐시일 뿐이다. 밀려나도 파일에서 다시 읽어 온다."""

    store = make_store(tmp_path, max_players=1)

    await store.remember("player-a", [make_memory("A 의 기억")])
    await store.remember("player-b", [make_memory("B 의 기억")])

    assert [memory.text for memory in await store.recall("player-a", query="", limit=3)] == [
        "A 의 기억"
    ]


@pytest.mark.parametrize("player_key", ["player-a", "0" * 64])
async def test_the_file_is_named_after_the_player_key(tmp_path: Path, player_key: str) -> None:
    await make_store(tmp_path).remember(player_key, [make_memory("무언가")])

    assert (tmp_path / f"{player_key}.json").exists()


# --- 두뇌가 언제 꺼내고 언제 쌓는가 -----------------------------------------------


class RecordingLLMProvider(MockLLMProvider):
    """Mock 위에 기억 관련 세 메서드만 얹은 스텁. 무엇이 프롬프트로 갔는지 받아 적는다.

    Mock 은 아무것도 추출하지 않으므로(의도된 동작), 두뇌의 배선을 시험하려면
    무엇을 낼지 시험이 정해 주는 공급자가 필요하다.
    """

    def __init__(
        self,
        extraction: MemoryExtraction | None = None,
        *,
        summary: SessionSummary | None = None,
        consolidation: Consolidation | None = None,
    ) -> None:
        self.extraction = extraction or EMPTY_EXTRACTION
        self.summary = summary or EMPTY_SUMMARY
        self.consolidation = consolidation or EMPTY_CONSOLIDATION
        self.dialogue_specs: list[DialogueSpec] = []
        self.extraction_specs: list[MemoryExtractionSpec] = []
        self.summary_specs: list[SessionSummarySpec] = []
        self.consolidation_specs: list[ConsolidationSpec] = []

    async def generate_dialogue(self, spec: DialogueSpec) -> str:
        self.dialogue_specs.append(spec)
        return await super().generate_dialogue(spec)

    async def extract_memories(self, spec: MemoryExtractionSpec) -> MemoryExtraction:
        self.extraction_specs.append(spec)
        return self.extraction

    async def summarize_session(self, spec: SessionSummarySpec) -> SessionSummary:
        self.summary_specs.append(spec)
        return self.summary

    async def consolidate_memories(self, spec: ConsolidationSpec) -> Consolidation:
        self.consolidation_specs.append(spec)
        return self.consolidation


def make_turn(text: str = "안녕", *, player_key: str = "player-a") -> CompanionTurn:
    return CompanionTurn(
        text=text,
        conversation_key="conv-1",
        player_key=player_key,
        allowed_actions=frozenset(CommandType),
    )


def make_brain(
    llm: MockLLMProvider,
    tmp_path: Path,
    *,
    long_term: FileLongTermStore | None = None,
    extract_every_n_turns: int = 3,
) -> CompanionBrain:
    """전사와 장기기억을 모두 붙인 두뇌. 증류 파이프라인은 이 둘이 다 있어야 돈다."""

    return CompanionBrain(
        llm,
        long_term=long_term or make_store(tmp_path / "memories"),
        transcript=FileTranscriptStore(
            directory=tmp_path / "transcripts", max_conversations=10
        ),
        extract_every_n_turns=extract_every_n_turns,
        quiet_seconds=90.0,
        session_end_seconds=600.0,
    )


async def test_recalled_memories_reach_the_dialogue_prompt(tmp_path: Path) -> None:
    store = make_store(tmp_path)
    await store.remember("player-a", [make_memory("플레이어는 밤을 싫어한다")])
    llm = RecordingLLMProvider()
    brain = CompanionBrain(llm, long_term=store)

    await brain.respond(make_turn())

    assert tuple(
        prompt_memory_claim(memory) for memory in llm.dialogue_specs[-1].memories
    ) == ("플레이어는 밤을 싫어한다",)


async def test_memories_never_reach_the_classifiers(tmp_path: Path) -> None:
    """기억에 '따라와' 가 들어 있어도 지금 명령이 나가서는 안 된다.

    과거 기억은 현재 명령을 만들기 위한 입력이 아니다. 세 턴 전이 아니라 세 **세션** 전
    말이 명령이 되면 더 나쁘다.
    """

    store = make_store(tmp_path)
    await store.remember("player-a", [make_memory("플레이어는 따라와 라고 자주 말한다")])
    brain = CompanionBrain(RecordingLLMProvider(), long_term=store)

    reply = await brain.respond(make_turn("안녕"))

    assert reply.action is None


async def test_a_turn_without_a_player_key_neither_recalls_nor_extracts(tmp_path: Path) -> None:
    llm = RecordingLLMProvider()
    brain = make_brain(llm, tmp_path, extract_every_n_turns=1)

    await brain.respond(make_turn(player_key=""))
    await brain.aclose()

    assert llm.dialogue_specs[-1].memories == ()
    assert llm.extraction_specs == []


# --- 언제 증류하는가 --------------------------------------------------------------


async def test_extraction_waits_until_enough_turns_have_piled_up(tmp_path: Path) -> None:
    """매 턴 추출하면 같은 대화로 LLM 을 계속 부른다."""

    llm = RecordingLLMProvider()
    brain = make_brain(llm, tmp_path, extract_every_n_turns=2)

    await brain.respond(make_turn())
    await brain._drain(now=later(0))
    assert llm.extraction_specs == []

    await brain.respond(make_turn())
    await brain._drain(now=later(0))

    assert len(llm.extraction_specs) == 1


async def test_a_quiet_conversation_is_extracted_even_after_two_turns(tmp_path: Path) -> None:
    """꼬리 유실 회귀 — 3의 배수로 끝나지 않은 대화도 기억을 남겨야 한다.

    예전에는 `turn_count % N` 에서만 추출해, 2턴이나 5턴에서 끝난 대화가 통째로 사라졌다.
    """

    llm = RecordingLLMProvider(
        MemoryExtraction(profile=["플레이어는 밤을 싫어한다"], episode=None, episode_importance=1)
    )
    store = make_store(tmp_path / "memories")
    brain = make_brain(llm, tmp_path, long_term=store, extract_every_n_turns=3)

    await brain.respond(make_turn())
    await brain._drain(now=later(120))

    assert len(llm.extraction_specs) == 1
    recalled = await store.recall("player-a", query="", limit=5)
    assert [memory.text for memory in recalled] == ["플레이어는 밤을 싫어한다"]


async def test_the_cursor_never_re_extracts_the_same_turns(tmp_path: Path) -> None:
    llm = RecordingLLMProvider()
    brain = make_brain(llm, tmp_path, extract_every_n_turns=1)

    await brain.respond(make_turn("첫 마디"))
    await brain._drain(now=later(0))
    await brain.respond(make_turn("둘째 마디"))
    await brain._drain(now=later(0))

    assert [spec.recent_turns[0].text for spec in llm.extraction_specs] == [
        "첫 마디",
        "둘째 마디",
    ]


async def test_a_finished_conversation_is_summarized_exactly_once(tmp_path: Path) -> None:
    llm = RecordingLLMProvider(summary=SessionSummary(summary="마을을 둘러본 이야기를 했다"))
    store = make_store(tmp_path / "memories")
    brain = make_brain(llm, tmp_path, long_term=store)

    await brain.respond(make_turn())
    await brain._drain(now=later(1200))
    await brain._drain(now=later(2400))

    assert len(llm.summary_specs) == 1
    recalled = await store.recall("player-a", query="", limit=5)
    assert "마을을 둘러본 이야기를 했다" in [memory.text for memory in recalled]


async def test_the_summary_sees_the_whole_conversation(tmp_path: Path) -> None:
    """마지막 몇 왕복만 보던 회귀 — 요약은 전사 전체(상한 안)를 봐야 한다."""

    llm = RecordingLLMProvider()
    brain = make_brain(llm, tmp_path)

    for label in _LABELS[:5]:
        await brain.respond(make_turn(f"{label} 이야기"))
    await brain._drain(now=later(1200))

    said = [turn.text for turn in llm.summary_specs[-1].turns]
    assert "가 이야기" in said
    assert "마 이야기" in said


async def test_a_summarized_conversation_leaves_the_queue(tmp_path: Path) -> None:
    llm = RecordingLLMProvider()
    brain = make_brain(llm, tmp_path)

    await brain.respond(make_turn())
    await brain._drain(now=later(1200))

    assert brain._pending == {}


async def test_closing_the_brain_flushes_what_is_left(tmp_path: Path) -> None:
    """정상 종료에서는 아직 증류하지 않은 꼬리가 사라지지 않는다."""

    llm = RecordingLLMProvider(
        MemoryExtraction(
            profile=["플레이어는 서두르는 편이다"], episode=None, episode_importance=1
        )
    )
    brain = make_brain(llm, tmp_path, extract_every_n_turns=99)

    await brain.respond(make_turn())
    await brain.aclose()

    assert len(llm.extraction_specs) == 1
    assert len(llm.summary_specs) == 1
    assert (tmp_path / "memories" / "player-a.json").exists()


async def test_the_extractor_is_shown_what_it_already_knows(tmp_path: Path) -> None:
    """이미 아는 것을 보여 주지 않으면 같은 사실을 매 번 새로 만든다."""

    store = make_store(tmp_path / "memories")
    await store.remember("player-a", [make_memory("플레이어는 밤을 싫어한다")])
    llm = RecordingLLMProvider()
    brain = make_brain(llm, tmp_path, long_term=store, extract_every_n_turns=1)

    await brain.respond(make_turn())
    await brain._drain(now=later(0))

    assert llm.extraction_specs[-1].known == ("플레이어는 밤을 싫어한다",)
    assert llm.extraction_specs[-1].recent_turns[0].text == "안녕"


async def test_a_failing_extraction_does_not_break_the_turn(tmp_path: Path) -> None:
    """기억을 만들지 못한 것은 대화의 실패가 아니다."""

    class FailingExtractor(RecordingLLMProvider):
        async def extract_memories(self, spec: MemoryExtractionSpec) -> MemoryExtraction:
            raise RuntimeError("extraction down")

    brain = make_brain(FailingExtractor(), tmp_path, extract_every_n_turns=1)

    reply = await brain.respond(make_turn())
    await brain._drain(now=later(0))
    await brain.aclose()

    assert reply.text
    assert not (tmp_path / "memories").exists()


async def test_one_broken_conversation_does_not_block_the_others(tmp_path: Path) -> None:
    """항목 하나의 실패가 대기열 전체를 막으면 한 사람이 모두의 기억을 멈춘다."""

    class PickyExtractor(RecordingLLMProvider):
        async def extract_memories(self, spec: MemoryExtractionSpec) -> MemoryExtraction:
            if spec.recent_turns[0].text == "터지는 말":
                raise RuntimeError("extraction down")
            return await super().extract_memories(spec)

    llm = PickyExtractor()
    brain = make_brain(llm, tmp_path, extract_every_n_turns=1)

    await brain.respond(
        CompanionTurn(
            text="터지는 말",
            conversation_key="conv-boom",
            player_key="player-a",
            allowed_actions=frozenset(CommandType),
        )
    )
    await brain.respond(make_turn("멀쩡한 말"))
    await brain._drain(now=later(0))

    assert [spec.recent_turns[0].text for spec in llm.extraction_specs] == ["멀쩡한 말"]


# --- 상한에 닿았을 때만 합친다 -----------------------------------------------------


async def test_consolidation_does_not_run_below_the_cap(tmp_path: Path) -> None:
    llm = RecordingLLMProvider(
        MemoryExtraction(profile=["플레이어는 밤을 싫어한다"], episode=None, episode_importance=1)
    )
    brain = make_brain(llm, tmp_path, extract_every_n_turns=1)

    await brain.respond(make_turn())
    await brain._drain(now=later(0))

    assert llm.consolidation_specs == []


async def test_consolidation_runs_when_the_cap_is_reached(tmp_path: Path) -> None:
    store = make_store(tmp_path / "memories")
    await store.remember(
        "player-a",
        [
            make_memory(f"사실 {chr(ord('가') + index)}")
            for index in range(MAX_MEMORIES_PER_PLAYER)
        ],
    )
    llm = RecordingLLMProvider(
        consolidation=Consolidation(
            memories=[
                ConsolidatedMemory(
                    text="사실 가와 사실 나를 합친 사실",
                    sources=[0, 1],
                )
            ]
        )
    )
    brain = make_brain(llm, tmp_path, long_term=store, extract_every_n_turns=1)

    await brain.respond(make_turn())
    await brain._drain(now=later(0))

    assert len(llm.consolidation_specs) == 1
    recalled = await store.recall("player-a", query="", limit=MAX_MEMORIES_PER_PLAYER)
    assert len(recalled) == MAX_MEMORIES_PER_PLAYER - 1
    assert "사실 가와 사실 나를 합친 사실" in [memory.text for memory in recalled]


async def test_a_brain_without_a_long_term_store_recalls_nothing(tmp_path: Path) -> None:
    """장기기억은 선택이다. 넘기지 않으면 한 대화 안에서만 기억한다."""

    llm = RecordingLLMProvider()
    brain = CompanionBrain(
        llm,
        transcript=FileTranscriptStore(directory=tmp_path, max_conversations=10),
        extract_every_n_turns=1,
    )

    await brain.respond(make_turn())
    await brain.aclose()

    assert llm.dialogue_specs[-1].memories == ()
    assert llm.extraction_specs == []


async def test_a_brain_without_a_transcript_never_extracts(tmp_path: Path) -> None:
    """증류는 전사에 대한 커서 작업이다. 읽을 로그가 없으면 새 기억도 없다."""

    llm = RecordingLLMProvider()
    store = make_store(tmp_path)
    await store.remember("player-a", [make_memory("플레이어는 밤을 싫어한다")])
    brain = CompanionBrain(llm, long_term=store, extract_every_n_turns=1)

    await brain.respond(make_turn())
    await brain.aclose()

    assert llm.extraction_specs == []
    # 회수는 그대로 동작한다 — 이미 있는 기억은 쓰인다.
    assert tuple(
        prompt_memory_claim(memory) for memory in llm.dialogue_specs[-1].memories
    ) == ("플레이어는 밤을 싫어한다",)


def test_semantic_similarity_can_out_rank_a_non_matching_memory() -> None:
    semantic = build_memory(
        "profile",
        "플레이어는 밤이 무섭다",
        importance=6,
        embedding=(1.0, 0.0),
        embedding_model="test-model",
    )
    unrelated = build_memory(
        "profile",
        "플레이어는 돌을 좋아한다",
        importance=6,
        embedding=(-1.0, 0.0),
        embedding_model="test-model",
    )
    assert semantic is not None
    assert unrelated is not None

    ranked = rank(
        (unrelated, semantic),
        query="완전히 다른 말",
        query_embedding=(1.0, 0.0),
        embedding_model="test-model",
        limit=2,
    )

    assert ranked[0].text == "플레이어는 밤이 무섭다"


def test_missing_query_embedding_keeps_keyword_ranking() -> None:
    memories = (
        make_memory("플레이어는 밤을 싫어한다"),
        make_memory("플레이어는 돌을 좋아한다"),
    )

    assert rank(memories, query="밤", limit=2) == rank(
        memories, query="밤", query_embedding=None, limit=2
    )


def test_importance_scale_keeps_the_strength_ceiling() -> None:
    memory = make_memory("플레이어는 밤을 싫어한다", importance=10, recall_count=5)

    assert strength(memory, now=_NOW) == pytest.approx(5.5)
