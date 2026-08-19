"""턴에 포함된 시간 맥락을 대사 프롬프트용 문장으로 바꾼다."""

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

