from datetime import datetime

from app.brain.gametime import KST, derive_real_world_answer, describe, period_for_hour
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


def test_current_real_world_time_uses_the_same_minute_for_fact_and_fallback() -> None:
    fixed_now = datetime(2026, 8, 26, 0, 54, 42, tzinfo=KST)

    answer = derive_real_world_answer("지금몇시지", (), now=fixed_now)

    assert answer is not None
    assert answer.fact == "지금은 한밤중 0시 54분이야."
    assert answer.fallback == answer.fact
    assert answer.required_numbers == ("0", "54")


def test_remaining_time_uses_recalled_work_schedule_and_next_occurrence() -> None:
    fixed_now = datetime(2026, 8, 26, 0, 55, 37, tzinfo=KST)

    answer = derive_real_world_answer(
        "출근까지 몇시간 남았지",
        ("출근시간은 9시 반이야", "퇴근은 언제나 6시반이야"),
        now=fixed_now,
    )

    assert answer is not None
    assert answer.fact == "출근까지 8시간 35분 남았어."
    assert answer.required_numbers == ("8", "35")
    assert answer.memory_index == 0


def test_unqualified_departure_time_is_interpreted_as_evening() -> None:
    fixed_now = datetime(2026, 8, 26, 0, 55, tzinfo=KST)

    answer = derive_real_world_answer(
        "퇴근까지 얼마나 남았지", ("퇴근은 언제나 6시반이야",), now=fixed_now
    )

    assert answer is not None
    assert answer.fact == "퇴근까지 17시간 35분 남았어."

