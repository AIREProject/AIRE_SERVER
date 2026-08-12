"""턴에 포함된 시간 맥락을 대사 프롬프트용 문장으로 바꾼다."""

from app.models import TimeContext

_PERIOD_NAMES: dict[str, str] = {
    "dawn": "새벽",
    "morning": "아침",
    "noon": "정오",
    "afternoon": "오후",
    "evening": "저녁",
    "night": "밤",
    "midnight": "한밤중",
}


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


def describe(time_context: TimeContext | None) -> tuple[str, ...]:
    """시간 맥락을 모델이 참고할 한 문장으로 만든다."""

    if time_context is None:
        return ()

    source = "게임 세계" if time_context.source.value == "GameWorld" else "현실"
    period = _period_name(time_context.period, time_context.hour)
    return (f"지금은 {source} 기준 {time_context.day}일차 {period}, {time_context.hour}시다.",)
