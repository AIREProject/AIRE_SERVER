"""오간 말을 그대로 남기는 전사 층 검증.

장기기억이 증류라면 여기는 원본이다. 원본이 틀리면 증류는 아무리 정교해도 틀리므로,
번호가 겹치지 않는지·깨진 줄이 나머지를 삼키지 않는지·대화가 섞이지 않는지를 본다.
"""

import os
from datetime import UTC, datetime, timedelta
from pathlib import Path

from app.brain.store import ConversationTurn
from app.brain.transcript import FileTranscriptStore


def make_store(directory: Path, *, max_conversations: int = 10) -> FileTranscriptStore:
    return FileTranscriptStore(directory=directory, max_conversations=max_conversations)


def exchange(player: str, companion: str) -> tuple[ConversationTurn, ...]:
    return (
        ConversationTurn(speaker="player", text=player),
        ConversationTurn(speaker="companion", text=companion),
    )


def file_names(directory: Path) -> list[str]:
    """디렉터리에 실제로 남은 파일 이름. 동기 함수로 두는 이유는 ruff `ASYNC240` 이다."""

    return sorted(path.name for path in directory.iterdir())


async def test_what_goes_in_comes_back_out(tmp_path: Path) -> None:
    store = make_store(tmp_path)

    await store.append("conv-1", exchange("안녕", "안녕! 오늘은 어디부터 둘러볼까?"))

    entries = await store.read("conv-1", since=0, limit=10)
    assert [(entry.speaker, entry.text) for entry in entries] == [
        ("player", "안녕"),
        ("companion", "안녕! 오늘은 어디부터 둘러볼까?"),
    ]


async def test_sequence_numbers_keep_climbing(tmp_path: Path) -> None:
    """번호가 겹치면 커서가 이미 증류한 구간을 다시 읽거나 건너뛴다."""

    store = make_store(tmp_path)

    assert await store.append("conv-1", exchange("첫", "첫 답")) == 2
    assert await store.append("conv-1", exchange("둘", "둘 답")) == 4

    entries = await store.read("conv-1", since=0, limit=10)
    assert [entry.seq for entry in entries] == [1, 2, 3, 4]


async def test_a_new_store_continues_the_numbering(tmp_path: Path) -> None:
    """재시작을 대신하는 검증 — 캐시가 비어도 파일의 마지막 번호에서 이어야 한다."""

    await make_store(tmp_path).append("conv-1", exchange("첫", "첫 답"))

    assert await make_store(tmp_path).append("conv-1", exchange("둘", "둘 답")) == 4


async def test_reading_since_a_cursor_skips_what_was_already_read(tmp_path: Path) -> None:
    store = make_store(tmp_path)
    await store.append("conv-1", exchange("첫", "첫 답"))
    await store.append("conv-1", exchange("둘", "둘 답"))

    entries = await store.read("conv-1", since=2, limit=10)

    assert [entry.text for entry in entries] == ["둘", "둘 답"]


async def test_reading_is_capped_and_resumable(tmp_path: Path) -> None:
    """상한에 걸려도 커서는 읽은 만큼만 전진하므로 건너뛰는 구간이 생기지 않는다."""

    store = make_store(tmp_path)
    for label in ("가", "나", "다"):
        await store.append("conv-1", exchange(label, f"{label} 답"))

    first = await store.read("conv-1", since=0, limit=2)
    second = await store.read("conv-1", since=first[-1].seq, limit=2)

    assert [entry.text for entry in first] == ["가", "가 답"]
    assert [entry.text for entry in second] == ["나", "나 답"]


async def test_tail_returns_the_end_of_the_conversation(tmp_path: Path) -> None:
    """요약은 마지막 몇 왕복이 아니라 대화 전체(상한 안)를 봐야 한다."""

    store = make_store(tmp_path)
    for label in ("가", "나", "다"):
        await store.append("conv-1", exchange(label, f"{label} 답"))

    entries = await store.tail("conv-1", limit=60)

    said = [entry.text for entry in entries]
    assert said[0] == "가"
    assert said[-1] == "다 답"


async def test_a_truncated_last_line_only_costs_that_line(tmp_path: Path) -> None:
    """덧붙이기만 하는 로그의 마지막 줄은 크래시로 잘려 있을 수 있다.

    그 한 줄 때문에 앞의 수백 줄을 버리면, 원본을 지키려고 만든 층이 원본을 버린다.
    """

    store = make_store(tmp_path)
    await store.append("conv-1", exchange("멀쩡한 말", "멀쩡한 답"))
    path = tmp_path / "conv-1.jsonl"
    path.write_text(path.read_text(encoding="utf-8") + '{"seq": 3, "spea', encoding="utf-8")

    entries = await store.read("conv-1", since=0, limit=10)

    assert [entry.text for entry in entries] == ["멀쩡한 말", "멀쩡한 답"]


async def test_conversations_do_not_share_a_transcript(tmp_path: Path) -> None:
    store = make_store(tmp_path)

    await store.append("conv-1", exchange("A 의 말", "A 의 답"))
    await store.append("conv-2", exchange("B 의 말", "B 의 답"))

    entries = await store.read("conv-1", since=0, limit=10)
    assert [entry.text for entry in entries] == ["A 의 말", "A 의 답"]
    assert sorted(file_names(tmp_path)) == ["conv-1.jsonl", "conv-2.jsonl"]


async def test_nothing_is_written_without_something_to_write(tmp_path: Path) -> None:
    directory = tmp_path / "transcripts"
    store = make_store(directory)

    assert await store.append("conv-1", ()) == 0
    assert await store.append("", exchange("말", "답")) == 0
    assert not directory.exists()


async def test_sweep_only_removes_what_is_past_the_retention(tmp_path: Path) -> None:
    """유일하게 무한히 자라는 층이라 이 정리는 청소 도구가 아니라 설계의 일부다."""

    store = make_store(tmp_path)
    await store.append("old", exchange("오래된 말", "오래된 답"))
    await store.append("fresh", exchange("방금 한 말", "방금 한 답"))
    stale = (datetime.now(UTC) - timedelta(days=60)).timestamp()
    os.utime(tmp_path / "old.jsonl", (stale, stale))

    removed = await store.sweep(older_than=datetime.now(UTC) - timedelta(days=30))

    assert removed == 1
    assert file_names(tmp_path) == ["fresh.jsonl"]


async def test_sweeping_an_empty_directory_is_harmless(tmp_path: Path) -> None:
    assert await make_store(tmp_path / "nothing").sweep(older_than=datetime.now(UTC)) == 0


async def test_evicting_the_counter_cache_does_not_reuse_numbers(tmp_path: Path) -> None:
    """카운터는 캐시일 뿐이다. 밀려나도 번호는 파일에서 다시 읽어 온다."""

    store = make_store(tmp_path, max_conversations=1)
    await store.append("conv-1", exchange("첫", "첫 답"))
    await store.append("conv-2", exchange("다른 대화", "다른 답"))

    assert await store.append("conv-1", exchange("둘", "둘 답")) == 4
