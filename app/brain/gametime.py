"""턴에 포함된 시간 맥락을 대사 프롬프트용 문장과 검증된 계산으로 바꾼다."""

import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from app.models import TimeContext

KST = timezone(timedelta(hours=9))
_WEEKDAY_KOREAN = ("월", "화", "수", "목", "금", "토", "일")

_PERIOD_NAMES: dict[str, str] = {
    "dawn": "새벽",
    "morning": "아침",
    "noon": "정오",
    "afternoon": "오후",
    "evening": "저녁",
    "night": "밤",
    "midnight": "한밤중",
}
_CURRENT_TIME_PATTERN = re.compile(r"(?:지금|현재)\s*(?:몇\s*시|시간(?:이|은)?\s*(?:몇|어떻게))")
_REMAINING_TIME_PATTERN = re.compile(
    r"(?P<label>출근|퇴근)\s*까지.*(?:몇\s*시간|얼마나|남았|남아)"
)
_CLOCK_PATTERN = re.compile(
    r"(?:(?P<period>오전|오후|아침|저녁)\s*)?"
    r"(?P<hour>\d{1,2})\s*시\s*(?:(?P<half>반)|(?P<minute>\d{1,2})\s*분)?"
)


@dataclass(frozen=True, slots=True)
class DerivedTimeAnswer:
    fact: str
    fallback: str
    required_numbers: tuple[str, ...]
    memory_index: int | None = None


def period_code_for_hour(hour: int) -> str:
    """시간으로부터 영문 period 코드를 유도한다."""

    if 5 <= hour < 8:
        return "dawn"
    if 8 <= hour < 12:
        return "morning"
    if 12 <= hour < 14:
        return "noon"
    if 14 <= hour < 18:
        return "afternoon"
    if 18 <= hour < 22:
        return "evening"
    if hour == 0 or hour >= 22:
        return "midnight"
    return "night"


def period_for_hour(hour: int) -> str:
    """알려지지 않은 period를 시간으로부터 안전하게 유도한다."""

    if 5 <= hour < 8:
        return "새벽"
    if 8 <= hour < 12:
        return "아침"
    if 12 <= hour < 14:
        return "정오"
    if 14 <= hour < 18:
        return "오후"
    if 18 <= hour < 22:
        return "저녁"
    if hour == 0 or hour >= 22:
        return "한밤중"
    return "밤"


def _period_name(period: str, hour: int) -> str:
    return _PERIOD_NAMES.get(period.casefold(), period_for_hour(hour))


def describe_real_world(
    time_context: TimeContext | None = None,
    *,
    now: datetime | None = None,
) -> tuple[str, ...]:
    """현실 시간(KST)을 모델이 참고할 한 문장으로 만든다."""

    kst_now = (now or datetime.now(KST)).astimezone(KST)
    weekday = _WEEKDAY_KOREAN[kst_now.weekday()]
    hour = time_context.hour if time_context is not None else kst_now.hour
    period = (
        _period_name(time_context.period, hour)
        if time_context is not None
        else period_for_hour(kst_now.hour)
    )
    return (
        f"지금은 현실 시간(KST) 기준 {kst_now.year}년 {kst_now.month}월 {kst_now.day}일 "
        f"{weekday}요일 {period}, {kst_now.hour}시 {kst_now.minute}분이다.",
    )


def describe(
    time_context: TimeContext | None,
    *,
    now: datetime | None = None,
) -> tuple[str, ...]:
    """시간 맥락을 모델이 참고할 한 문장으로 만든다."""

    if time_context is None:
        return ()

    if time_context.source.value == "RealWorld":
        return describe_real_world(time_context, now=now)

    period = _period_name(time_context.period, time_context.hour)
    return (f"지금은 게임 세계 기준 {time_context.day}일차 {period}, {time_context.hour}시다.",)


def derive_real_world_answer(
    text: str,
    memory_claims: tuple[str, ...],
    *,
    now: datetime | None = None,
) -> DerivedTimeAnswer | None:
    """명확한 현실 시각 질문만 코드로 계산해 LLM의 숫자를 검증한다."""

    kst_now = (now or datetime.now(KST)).astimezone(KST).replace(second=0, microsecond=0)
    if _CURRENT_TIME_PATTERN.search(text) is not None:
        answer = f"지금은 {period_for_hour(kst_now.hour)} {kst_now.hour}시 {kst_now.minute}분이야."
        return DerivedTimeAnswer(answer, answer, (str(kst_now.hour), str(kst_now.minute)))

    remaining = _REMAINING_TIME_PATTERN.search(text)
    if remaining is None:
        return None
    label = remaining.group("label")
    candidates: list[tuple[int, int, int]] = []
    for index, claim in enumerate(memory_claims):
        if label not in claim:
            continue
        parsed = _clock_from_claim(claim, label)
        if parsed is not None:
            candidates.append((index, *parsed))
    unique_times = {(hour, minute) for _, hour, minute in candidates}
    if len(unique_times) != 1:
        return DerivedTimeAnswer(
            fact=f"{label} 시각을 하나로 확정할 수 없다.",
            fallback=f"{label} 시간이 정확히 몇 시인지 한 번만 다시 알려 줄래?",
            required_numbers=(),
        )
    hour, minute = next(iter(unique_times))
    target = kst_now.replace(hour=hour, minute=minute, second=0, microsecond=0)
    if target < kst_now:
        target += timedelta(days=1)
    remaining_minutes = max(0, int((target - kst_now).total_seconds() // 60))
    hours, minutes = divmod(remaining_minutes, 60)
    duration = _duration_text(hours, minutes)
    answer = f"{label}까지 {duration} 남았어."
    required = tuple(
        value for value in (str(hours) if hours else "", str(minutes) if minutes else "") if value
    )
    if not required:
        answer = f"지금이 {label} 시간이야."
    memory_index = next(
        index
        for index, candidate_hour, candidate_minute in candidates
        if (candidate_hour, candidate_minute) == (hour, minute)
    )
    return DerivedTimeAnswer(answer, answer, required, memory_index)


def _clock_from_claim(claim: str, label: str) -> tuple[int, int] | None:
    matches = tuple(_CLOCK_PATTERN.finditer(claim))
    if len(matches) != 1:
        return None
    match = matches[0]
    hour = int(match.group("hour"))
    minute = 30 if match.group("half") else int(match.group("minute") or 0)
    if hour > 23 or minute > 59:
        return None
    period = match.group("period")
    if period in {"오후", "저녁"} and hour < 12:
        hour += 12
    elif period in {"오전", "아침"} and hour == 12:
        hour = 0
    elif period is None and label == "퇴근" and 1 <= hour < 12:
        hour += 12
    return hour, minute


def _duration_text(hours: int, minutes: int) -> str:
    parts: list[str] = []
    if hours:
        parts.append(f"{hours}시간")
    if minutes:
        parts.append(f"{minutes}분")
    return " ".join(parts) or "0분"

