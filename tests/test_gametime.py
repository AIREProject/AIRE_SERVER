from datetime import datetime

from app.brain.gametime import KST, describe, period_for_hour
from app.models import TimeContext, TimeSource


def test_known_period_is_rendered_in_korean() -> None:
    context = TimeContext(source=TimeSource.GAME_WORLD, day=7, hour=23, period="Night")

    assert describe(context) == ("지금은 게임 세계 기준 7일차 밤, 23시다.",)


def test_unknown_period_falls_back_to_hour() -> None:
    context = TimeContext(source=TimeSource.GAME_WORLD, day=1, hour=6, period="Dusk")

    assert describe(context) == ("지금은 게임 세계 기준 1일차 새벽, 6시다.",)


def test_period_for_hour_covers_day_boundaries() -> None:
    assert period_for_hour(0) == "한밤중"
    assert period_for_hour(7) == "새벽"
    assert period_for_hour(8) == "아침"
    assert period_for_hour(12) == "정오"
    assert period_for_hour(14) == "오후"
    assert period_for_hour(18) == "저녁"
    assert period_for_hour(22) == "한밤중"


def test_real_world_renders_kst_date_weekday_and_period() -> None:
    context = TimeContext(source=TimeSource.REAL_WORLD, day=19, hour=16, period="afternoon")
    fixed_now = datetime(2026, 8, 19, 16, 54, tzinfo=KST)

    assert describe(context, now=fixed_now) == (
        "지금은 현실 시간(KST) 기준 2026년 8월 19일 수요일 오후, 16시 54분이다.",
    )

