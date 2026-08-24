from unittest.mock import AsyncMock

import pytest

from app.brain.dialogue import (
    SURFACE_PROFILES,
    DialogueOutput,
    DialogueScene,
    DialogueSpec,
    begin_memory_reference_trace,
    finish_memory_reference_trace,
    provider_failure_fallback,
    render,
    sanitize,
)
from app.brain.store import ConversationTurn
from app.models import Surface


def generated(
    text: str,
    purpose: DialogueScene,
    *,
    fact_references: tuple[int, ...] = (),
    memory_references: tuple[int, ...] = (),
    accepts_command: bool = False,
) -> DialogueOutput:
    return DialogueOutput(
        text=text,
        purpose=purpose,
        fact_references=fact_references,
        memory_references=memory_references,
        situation_references=(),
        accepts_command=accepts_command,
    )


def test_every_surface_has_a_profile() -> None:
    """창구를 늘리고 표를 잊으면 그 창구의 첫 요청에서 KeyError 로 터진다."""

    assert set(SURFACE_PROFILES) == set(Surface)


def test_spec_speaks_as_the_game_companion_unless_told_otherwise() -> None:
    assert DialogueSpec(scene="wait", fallback="기다릴게.").surface is Surface.GAME


def test_mobile_refusals_never_name_a_game_action() -> None:
    """폰에는 따라오기도 대기도 없다. 그 이름이 확정 사실로 들어가면 없는 동작을 사실로 말한다."""

    mobile = SURFACE_PROFILES[Surface.MOBILE]
    named = ("따라", "대기", "채집", "캐")
    for line in (mobile.unsupported, mobile.not_allowed, mobile.lore_missing):
        assert not any(action in line.fact for action in named), line.fact


@pytest.mark.parametrize("surface", list(Surface))
def test_fallback_lines_never_carry_a_number(surface: Surface) -> None:
    """폴백은 `facts` 를 거치지 않고 그대로 나간다. 숫자가 있으면 근거 없는 수치가 된다."""

    profile = SURFACE_PROFILES[surface]
    lines = (
        profile.unsupported.text,
        profile.not_allowed.text,
        profile.lore_missing.text,
        profile.greeting,
        profile.thanks,
    )
    assert not any(character.isdigit() for line in lines for character in line)


@pytest.mark.parametrize("text", ["", " \n\t ", "가" * 201])
def test_sanitize_rejects_empty_or_overlong_dialogue(text: str) -> None:
    spec = DialogueSpec(scene="wait", fallback="기다릴게.")

    assert sanitize(text, spec) is None


def test_sanitize_folds_newlines_and_repeated_whitespace() -> None:
    spec = DialogueSpec(scene="wait", fallback="기다릴게.", command_candidate_present=True)

    assert sanitize("  여기서\n  기다릴게.  ", spec) == "여기서 기다릴게."


def test_sanitize_removes_unprompted_laughter_and_limits_prompted_laughter() -> None:
    serious = DialogueSpec(
        scene="conversation",
        fallback="무슨 일이야?",
        user_text="벌써 배고파",
    )
    joking = DialogueSpec(
        scene="conversation",
        fallback="무슨 일이야?",
        user_text="이거 웃기다 ㅋㅋ",
    )

    assert sanitize("헉, 벌써? ㅋㅋ 뭐 좀 먹어.", serious) == "헉, 벌써? 뭐 좀 먹어."
    assert sanitize("ㅋㅋㅋㅋ 맞아 ㅎㅎ 진짜 웃기네.", joking) == "ㅋㅋ 맞아 진짜 웃기네."


def test_sanitize_rejects_numbers_not_present_in_facts() -> None:
    spec = DialogueSpec(
        scene="recipe",
        fallback="기존 제작법",
        facts=("철괴 3개와 나무 2개가 필요하다",),
    )

    assert sanitize("철괴 4개가 필요해.", spec) is None
    assert sanitize("철괴 3개와 나무 2개가 필요해.", spec) is not None


def test_sanitize_exempts_conversation_from_number_guard() -> None:
    spec = DialogueSpec(scene="conversation", fallback="안녕!", facts=())

    assert sanitize("오늘도 100점짜리 하루를 보내자.", spec) is not None


def test_sanitize_rejects_mismatched_purpose_and_out_of_range_reference() -> None:
    spec = DialogueSpec(scene="enemy", fallback="확인된 정보가 없어.", facts=("약점은 다리다",))

    assert (
        sanitize(
            generated("다리를 노려.", "conversation", fact_references=(0,)),
            spec,
        )
        is None
    )
    assert (
        sanitize(
            generated("다리를 노려.", "enemy", fact_references=(1,)),
            spec,
        )
        is None
    )


def test_sanitize_requires_a_lexical_anchor_for_each_reference() -> None:
    spec = DialogueSpec(scene="enemy", fallback="확인된 정보가 없어.", facts=("약점은 다리다",))

    assert (
        sanitize(
            generated("불로 공격해.", "enemy", fact_references=(0,)),
            spec,
        )
        is None
    )
    assert (
        sanitize(
            generated("다리를 노려.", "enemy", fact_references=(0,)),
            spec,
        )
        == "다리를 노려."
    )


def test_sanitize_rejects_fact_claim_without_a_fact_reference() -> None:
    spec = DialogueSpec(scene="conversation", fallback="안녕!")

    assert (
        sanitize(
            generated("그 적의 약점은 불이야.", "conversation"),
            spec,
        )
        is None
    )


def test_sanitize_rejects_command_acceptance_without_a_candidate() -> None:
    spec = DialogueSpec(scene="conversation", fallback="안녕!")

    assert (
        sanitize(
            generated(text="알겠어. 따라갈게.", purpose="conversation", accepts_command=True),
            spec,
        )
        is None
    )
    assert (
        sanitize(
            generated("알겠어. 따라갈게.", "conversation"),
            spec,
        )
        is None
    )


def test_sanitize_allows_acceptance_with_a_real_candidate() -> None:
    spec = DialogueSpec(
        scene="follow_player",
        fallback="알겠어. 따라갈게.",
        command_candidate_present=True,
    )

    assert (
        sanitize(
            generated(text="좋아, 따라갈게.", purpose="follow_player", accepts_command=True),
            spec,
        )
        == "좋아, 따라갈게."
    )


def test_required_memory_rejects_missing_numeric_and_negation_distortion() -> None:
    spec = DialogueSpec(
        scene="conversation",
        fallback="아직 확실히 기억나지 않아.",
        memories=("[M0] 플레이어는 겨울 여행을 좋아하고 12월에 간다",),
        memory_use_policy="Required",
    )

    assert sanitize(generated("다른 얘기를 하자.", "conversation"), spec) is None
    assert sanitize("겨울 여행을 기억해.", spec) is None
    assert (
        sanitize(
            generated(
                "너는 겨울 여행을 좋아하고 11월에 가.",
                "conversation",
                memory_references=(0,),
            ),
            spec,
        )
        is None
    )
    assert (
        sanitize(
            generated(
                "너는 겨울 여행을 좋아하지 않고 12월에 가.",
                "conversation",
                memory_references=(0,),
            ),
            spec,
        )
        is None
    )
    assert (
        sanitize(
            generated(
                "너는 겨울 여행을 좋아하고 12월에 가.",
                "conversation",
                memory_references=(0,),
            ),
            spec,
        )
        is not None
    )


def test_required_memory_accepts_a_concise_grounded_answer() -> None:
    spec = DialogueSpec(
        scene="conversation",
        fallback="기억나는 게 없어.",
        memories=("[M0] 출근시간은 9시 반이야 기억해",),
        memory_use_policy="Required",
    )

    assert (
        sanitize(
            generated("9시 반이야.", "conversation", memory_references=(0,)),
            spec,
        )
        == "9시 반이야."
    )


@pytest.mark.asyncio
async def test_render_uses_fallback_for_invalid_dialogue() -> None:
    provider = AsyncMock()
    provider.generate_dialogue.return_value = "돌 12개를 모았어."
    spec = DialogueSpec(
        scene="event_completed",
        fallback="돌 10개를 모았어.",
        facts=("돌 10개를 채집했다",),
    )

    assert await render(provider, spec) == spec.fallback


@pytest.mark.asyncio
async def test_required_memory_rejection_falls_back_to_the_recalled_memory() -> None:
    provider = AsyncMock()
    provider.generate_dialogue.return_value = generated("잘 모르겠어.", "conversation")
    spec = DialogueSpec(
        scene="conversation",
        fallback="기억나는 게 없어.",
        memories=(
            "[M0] type=ProfileFact; source=RealWorld; priority=Normal; 출근시간은 9시 반이야",
        ),
        memory_use_policy="Required",
    )
    token = begin_memory_reference_trace()
    try:
        response = await render(provider, spec)
        references = finish_memory_reference_trace(token)
    except BaseException:
        finish_memory_reference_trace(token)
        raise

    assert response == "전에 네가 이렇게 알려줬어: “출근시간은 9시 반이야”"
    assert references == ((0,),)


@pytest.mark.asyncio
async def test_memory_fallback_quotes_a_raw_storage_request_instead_of_concatenating_it() -> None:
    provider = AsyncMock()
    provider.generate_dialogue.return_value = generated("모르겠어.", "conversation")
    spec = DialogueSpec(
        scene="conversation",
        fallback="기억나는 게 없어.",
        memories=(
            "[M0] type=ProfileFact; source=RealWorld; priority=Normal; "
            "출근시간은 9시 반이야 기억해",
        ),
        memory_use_policy="Required",
    )

    assert await render(provider, spec) == (
        "전에 네가 이렇게 알려줬어: “출근시간은 9시 반이야 기억해”"
    )


@pytest.mark.asyncio
async def test_optional_memory_rejection_respects_the_llm_memory_selection() -> None:
    provider = AsyncMock()
    provider.generate_dialogue.return_value = generated(
        "출근시간은 8시야.",
        "conversation",
        memory_references=(0,),
    )
    spec = DialogueSpec(
        scene="conversation",
        fallback="일반 대화 폴백",
        memories=(
            "[M0] type=ProfileFact; source=RealWorld; priority=Normal; 출근시간은 9시 반이야",
        ),
        memory_use_policy="Optional",
    )
    token = begin_memory_reference_trace()
    try:
        response = await render(provider, spec)
        references = finish_memory_reference_trace(token)
    except BaseException:
        finish_memory_reference_trace(token)
        raise

    assert response == "전에 네가 이렇게 알려줬어: “출근시간은 9시 반이야”"
    assert references == ((0,),)


@pytest.mark.asyncio
async def test_optional_memory_rejection_without_llm_selection_keeps_scene_fallback() -> None:
    provider = AsyncMock()
    provider.generate_dialogue.return_value = generated("레시피는 나무 3개야.", "conversation")
    spec = DialogueSpec(
        scene="conversation",
        fallback="일반 대화 폴백",
        memories=("[M0] type=ProfileFact; 출근시간은 9시 반이야",),
        memory_use_policy="Optional",
    )

    assert await render(provider, spec) == spec.fallback


@pytest.mark.parametrize(
    "reason",
    [
        "provider_timeout",
        "provider_unavailable",
        "invalid_structured_output",
        "empty_output",
        "sanitizer_rejection",
    ],
)
def test_conversation_provider_failure_keeps_safe_scene_fallback(reason: str) -> None:
    spec = DialogueSpec(scene="conversation", fallback="기존 대사")

    assert provider_failure_fallback(spec, reason) == spec.fallback


@pytest.mark.parametrize(
    "reason",
    [
        "provider_timeout",
        "provider_unavailable",
        "invalid_structured_output",
        "empty_output",
        "sanitizer_rejection",
    ],
)
def test_action_scene_keeps_its_action_coupled_fallback(reason: str) -> None:
    spec = DialogueSpec(scene="follow_player", fallback="알겠어. 따라갈게.")

    assert provider_failure_fallback(spec, reason) == spec.fallback


def test_situation_numbers_are_allowed_but_not_other_context_numbers() -> None:
    spec = DialogueSpec(
        scene="gather_wood",
        fallback="알겠어. 근처의 나무를 찾아볼게.",
        situation=("지금은 게임 세계 기준 7일차 밤, 23시다.",),
        history=(ConversationTurn(speaker="player", text="아까 3개 말했잖아"),),
    )

    assert sanitize("7일차 밤이네.", spec) == "7일차 밤이네."
    assert sanitize("23시니까 쉬자.", spec) == "23시니까 쉬자."
    assert sanitize("3개 캐 올게.", spec) is None


def test_history_numbers_are_not_treated_as_confirmed_facts() -> None:
    """기록의 수치가 확정 사실로 승격되면 세 턴 전 숫자를 지금의 게임 사실인 양 말한다."""

    spec = DialogueSpec(
        scene="gather_wood",
        fallback="알겠어. 근처의 나무를 찾아볼게.",
        facts=(),
        history=(
            ConversationTurn(speaker="player", text="나무 20개 캐 줘"),
            ConversationTurn(speaker="companion", text="20개 캐 올게."),
        ),
    )

    assert sanitize("20개 캐 올게.", spec) is None


def test_situation_scene_permits_only_numbers_the_client_sent() -> None:
    """상황 이벤트는 `facts` 가 비어 있으므로 `situation` 이 유일한 숫자 근거다."""

    spec = DialogueSpec(
        scene="situation",
        fallback="방금 그거, 봤어?",
        situation=("플레이어 체력이 20% 남았다",),
    )

    assert sanitize("체력이 20%밖에 안 남았어, 조심해.", spec) is not None
    assert sanitize("적이 5마리나 있어.", spec) is None


def test_confirmed_facts_still_permit_their_own_numbers() -> None:
    spec = DialogueSpec(
        scene="gather_wood",
        fallback="알겠어. 근처의 나무를 찾아볼게.",
        facts=("요청 수량은 20개다",),
        history=(ConversationTurn(speaker="player", text="아까 3개 말했잖아"),),
        command_candidate_present=True,
    )

    assert sanitize("20개 캐 올게.", spec) == "20개 캐 올게."
    # 기록에만 있는 3은 여전히 허용되지 않는다.
    assert sanitize("3개 캐 올게.", spec) is None
