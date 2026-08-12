from app.brain.gametime import describe, period_for_hour
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
