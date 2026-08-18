"""오간 말을 그대로 남기는 전사(transcript) 층.

[store.py](store.py) 의 작업 기억, [memory.py](memory.py) 의 장기기억과 **셋 다 다르다.**
작업 기억은 다음 턴에 실어 보낼 최근 몇 마디고, 장기기억은 증류된 몇십 줄이다. 여기 있는
것은 **증류의 원본**이다 — 자르지도 요약하지도 않고, 대화 키마다 한 줄씩 덧붙이기만 한다.

이 층이 있어야 추출이 턴의 부수효과가 아니라 **로그에 대한 커서 작업**이 된다. 어디까지
증류했는지만 들고 있으면 추출 시점을 턴 주기에서 떼어 낼 수 있고, 실패한 구간을 다시 읽을
수 있으며, 규칙이 바뀌면 처음부터 다시 증류할 수 있다.

> [!IMPORTANT]
> **대화 원문이 디스크에 남는다.** 파일 이름은 인증된 신원(`AuthenticatedDevice`)에서
> HMAC 으로 파생된 키다(`app/service.py`, `docs/temporary-scaffolds.md` §2) — 이전에는
> 자기신고 `player_name` 이었다. 구조화 로그 스트림에는 여전히 대화가 한 글자도 들어가지
> 않는다 — 여기는 의도된 별도 저장소다.

키는 어댑터가 넘겨주는 불투명한 값이다. 그 값이 그대로 파일 이름이 되므로 경로로 쓸 수
있는 값이어야 한다.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Sequence
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal, Protocol

from pydantic import BaseModel, ConfigDict

from .store import ConversationTurn

# 한 번에 읽어 올릴 수 있는 항목 수의 안전 상한. 설정이 아니라 상수다 — 프롬프트에 실리는
# 양을 환경변수로 올릴 수 있게 두면 상한 구실을 못 한다.
MAX_READ_ENTRIES = 200

_SUFFIX = ".jsonl"


@dataclass(frozen=True, slots=True)
class TranscriptEntry:
    """전사에 적힌 한 마디."""

    # 대화 안에서 1부터 단조 증가한다. 증류 커서가 가리키는 값이다.
    seq: int
    speaker: Literal["player", "companion", "situation"]
    text: str
    at: datetime


class TranscriptStore(Protocol):
    """대화 키별 전사 보관소.

    `ConversationStore` 와 달리 비동기다 — 구현이 디스크를 만지기 때문이고, 이벤트 루프를
    막지 않으려면 그 사실이 인터페이스에 드러나야 한다.
    """

    async def append(self, conversation_key: str, turns: Sequence[ConversationTurn]) -> int:
        """오간 말을 덧붙이고 마지막 `seq` 를 돌려준다."""
        ...

    async def read(
        self, conversation_key: str, *, since: int, limit: int
    ) -> tuple[TranscriptEntry, ...]:
        """`since` 보다 뒤인 것을 **오래된 순으로** 최대 `limit` 개 돌려준다(커서용)."""
        ...

    async def tail(self, conversation_key: str, *, limit: int) -> tuple[TranscriptEntry, ...]:
        """대화의 **마지막** `limit` 개를 시간순으로 돌려준다(요약용)."""
        ...

    async def sweep(self, *, older_than: datetime) -> int:
        """마지막으로 쓰인 지 오래된 전사를 지우고 지운 수를 돌려준다."""
        ...


class _TranscriptRecord(BaseModel):
    """파일에 적히는 한 줄."""

    model_config = ConfigDict(extra="ignore")

    seq: int
    speaker: Literal["player", "companion", "situation"]
    text: str
    at: datetime


def _parse(raw: str) -> TranscriptEntry | None:
    """한 줄을 항목으로 옮긴다. 못 읽으면 `None` — 그 줄만 버린다."""

    line = raw.strip()
    if not line:
        return None
    try:
        record = _TranscriptRecord.model_validate_json(line)
    except ValueError:
        return None
    at = record.at if record.at.tzinfo is not None else record.at.replace(tzinfo=UTC)
    return TranscriptEntry(seq=record.seq, speaker=record.speaker, text=record.text, at=at)


def _read_file(path: Path) -> tuple[TranscriptEntry, ...]:
    """파일을 읽어 항목들로 옮긴다.

    **깨진 줄은 그 줄만 건너뛴다.** 기억 파일과 다른 점이다 — 저기는 통째로 다시 쓰므로
    파일이 깨졌으면 전부 못 믿지만, 여기는 덧붙이기만 하는 로그라 크래시로 마지막 줄이
    잘려 있을 수 있고 그 앞의 수백 줄은 멀쩡하다.
    """

    try:
        raw = path.read_text(encoding="utf-8")
    except OSError:
        return ()
    parsed = (_parse(line) for line in raw.splitlines())
    return tuple(entry for entry in parsed if entry is not None)


def _last_seq(path: Path) -> int:
    """파일에 적힌 마지막 `seq`. 파일이 없거나 못 읽으면 0."""

    entries = _read_file(path)
    return entries[-1].seq if entries else 0


def _append_file(path: Path, records: Sequence[_TranscriptRecord]) -> None:
    """줄들을 덧붙인다. 기억 파일과 달리 통째로 다시 쓰지 않는다 — 계속 자라는 로그다."""

    path.parent.mkdir(parents=True, exist_ok=True)
    payload = "".join(f"{record.model_dump_json()}\n" for record in records)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(payload)


def _sweep_directory(directory: Path, *, older_than: float) -> int:
    """마지막으로 쓰인 지 오래된 전사 파일을 지운다.

    유일하게 무한히 자라는 것이 전사이므로 이 정리는 설계의 일부지 청소 도구가 아니다.
    """

    removed = 0
    try:
        candidates = sorted(directory.glob(f"*{_SUFFIX}"))
    except OSError:
        return 0
    for path in candidates:
        try:
            if path.stat().st_mtime < older_than:
                path.unlink()
                removed += 1
        except OSError:
            continue
    return removed


class FileTranscriptStore:
    """대화마다 JSONL 파일 하나를 두는 기본 구현.

    쓰기는 진짜 append 다. 다음 `seq` 를 알아야 하므로 대화별 카운터를 상한 있는 캐시에
    들고 있고, 캐시에 없으면 파일의 마지막 줄에서 읽어 온다 — 재시작해도 번호가 겹치지
    않는다.
    """

    def __init__(self, *, directory: Path, max_conversations: int) -> None:
        self._directory = directory
        self._max_conversations = max_conversations
        # 삽입 순서를 유지하는 dict 라, 가장 오래 전에 쓰인 항목이 앞에 온다.
        self._last: dict[str, int] = {}
        # 대화별 직렬화 장치. 같은 대화의 append 둘이 겹치면 번호가 겹친다.
        self._locks: dict[str, asyncio.Lock] = {}
        self._lock_users: dict[str, int] = {}

    async def append(self, conversation_key: str, turns: Sequence[ConversationTurn]) -> int:
        if not conversation_key or not turns:
            return 0
        async with self._conversation_lock(conversation_key):
            last = self._last.get(conversation_key)
            if last is None:
                last = await asyncio.to_thread(_last_seq, self._path(conversation_key))
            at = datetime.now(UTC)
            records = [
                _TranscriptRecord(seq=last + offset, speaker=turn.speaker, text=turn.text, at=at)
                for offset, turn in enumerate(turns, start=1)
            ]
            await asyncio.to_thread(_append_file, self._path(conversation_key), records)
            last += len(records)
            self._remember_last(conversation_key, last)
            return last

    async def read(
        self, conversation_key: str, *, since: int, limit: int
    ) -> tuple[TranscriptEntry, ...]:
        if not conversation_key or limit <= 0:
            return ()
        entries = await asyncio.to_thread(_read_file, self._path(conversation_key))
        fresh = tuple(entry for entry in entries if entry.seq > since)
        return fresh[: min(limit, MAX_READ_ENTRIES)]

    async def tail(self, conversation_key: str, *, limit: int) -> tuple[TranscriptEntry, ...]:
        if not conversation_key or limit <= 0:
            return ()
        entries = await asyncio.to_thread(_read_file, self._path(conversation_key))
        return entries[-min(limit, MAX_READ_ENTRIES) :]

    async def sweep(self, *, older_than: datetime) -> int:
        removed = await asyncio.to_thread(
            _sweep_directory, self._directory, older_than=older_than.timestamp()
        )
        return removed

    def _path(self, conversation_key: str) -> Path:
        return self._directory / f"{conversation_key}{_SUFFIX}"

    def _remember_last(self, conversation_key: str, seq: int) -> None:
        """카운터를 갱신하고 상한을 넘으면 가장 오래 안 쓰인 대화를 떨군다.

        캐시일 뿐이라 떨궈도 번호는 파일에서 다시 읽어 온다.
        """

        self._last.pop(conversation_key, None)
        self._last[conversation_key] = seq
        while len(self._last) > self._max_conversations:
            self._last.pop(next(iter(self._last)))

    @asynccontextmanager
    async def _conversation_lock(self, conversation_key: str) -> AsyncIterator[None]:
        """한 파일의 번호 읽기·덧붙이기를 직렬화한다.

        락은 쓰는 사람이 없어지면 즉시 버린다. 대화마다 남겨 두면 캐시와 달리 상한이 없다.
        """

        lock = self._locks.get(conversation_key)
        if lock is None:
            lock = asyncio.Lock()
            self._locks[conversation_key] = lock
        self._lock_users[conversation_key] = self._lock_users.get(conversation_key, 0) + 1
        try:
            async with lock:
                yield
        finally:
            self._lock_users[conversation_key] -= 1
            if self._lock_users[conversation_key] == 0:
                del self._lock_users[conversation_key]
                del self._locks[conversation_key]
