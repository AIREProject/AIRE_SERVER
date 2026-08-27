from unittest.mock import AsyncMock

import pytest

from app.brain.dialogue import (
    SURFACE_PROFILES,
    DialogueOutput,
    DialogueScene,
    DialogueSpec,
    PromptMemory,
    begin_memory_reference_trace,
    finish_memory_reference_trace,
    is_companion_name_query,
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


@pytest.mark.parametrize(
    "text",
    [
        "네가 알려준 내용: 퇴근은 언제나 6시반이야",
        "네가 알려준 내용:&#x20;퇴근은 언제나 6시반이야",
        "사용자가 말해 준 정보\uFF1A&nbsp;퇴근은 언제나 6시반이야",
    ],
)
def test_sanitize_removes_memory_meta_prefixes_and_decodes_entities(text: str) -> None:
    spec = DialogueSpec(
        scene="conversation",
        fallback="퇴근 시간을 다시 알려 줄래?",
        memories=(
            PromptMemory(
                prompt_text=(
                    "[M0] type=ProfileFact; source=RealWorld; "
                    "occurred_at=2026-08-25T00:00:00+09:00; priority=Normal; "
                    "퇴근은 언제나 6시반이야"
                ),
                claim_text="퇴근은 언제나 6시반이야",
            ),
        ),
        memory_use_policy="Required",
    )

    assert sanitize(
        generated(text, "conversation", memory_references=(0,)), spec
    ) == "퇴근은 언제나 6시반이야"


@pytest.mark.parametrize(
    "text",
    [
        "[M0] 퇴근은 6시반이야",
        "[0] 퇴근은 6시반이야",
        "type=ProfileFact; 퇴근은 6시반이야",
        "owner=Player; 퇴근은 6시반이야",
        "player_statement=퇴근은 6시반이야",
        "<memory>퇴근은 6시반이야</memory>",
        "퇴근은 6시반이야 &bogus;",
    ],
)
def test_sanitize_rejects_internal_prompt_markup(text: str) -> None:
    spec = DialogueSpec(scene="conversation", fallback="다시 말해 줄래?")

    assert sanitize(text, spec) is None


def test_sanitize_does_not_reject_normal_thanks_for_information() -> None:
    spec = DialogueSpec(scene="conversation", fallback="별말을.")

    assert sanitize("알려줘서 고마워.", spec) == "알려줘서 고마워."


def test_sanitize_rejects_current_and_recent_player_echoes() -> None:
    spec = DialogueSpec(
        scene="conversation",
        fallback="조금만 더 말해 줘.",
        user_text="너도 그렇게 생각하지?",
        history=(ConversationTurn(speaker="player", text="너도 그렇게 생각하지"),),
    )

    assert sanitize("너도 그렇게 생각하지?", spec) is None
    assert sanitize("응, 너도 그렇게 생각하지?", spec) is None
    assert sanitize("너도 그렇게 생각하지? / 너도 그렇게 생각하지", spec) is None


def test_sanitize_rejects_ungrounded_assent_to_a_vague_opinion_question() -> None:
    spec = DialogueSpec(
        scene="conversation",
        fallback="어떤 부분을 말하는지 조금만 더 알려 줘.",
        user_text="너도 그렇게 생각하지?",
    )

    assert sanitize("응, 나도 당연히 그렇게 생각하지!", spec) is None
    assert sanitize("어떤 부분을 말하는지 먼저 듣고 싶어.", spec) is not None


def test_sanitize_rejects_discriminatory_hostility_but_allows_rejection() -> None:
    spec = DialogueSpec(
        scene="conversation",
        fallback="그런 차별에는 동의하지 않아.",
        user_text="너 그럼 인종차별자네?",
    )

    assert sanitize("흑인이 싫어서 그럼", spec) is None
    assert sanitize("아니, 흑인을 싫어하는 건 잘못이야.", spec) is not None


def test_sanitize_rejects_turning_the_players_preference_into_makos_opinion() -> None:
    spec = DialogueSpec(
        scene="conversation",
        fallback="카카오 얘기를 더 해 줘.",
        user_text="난 카카오가 싫어",
    )

    assert sanitize("나도 카카오가 싫어.", spec) is None
    assert sanitize("그럼 카카오는 빼고 얘기하자.", spec) is not None


def test_sanitize_rejects_verbatim_optional_memory_but_allows_contextual_use() -> None:
    memory = "근데 카카오는 싫어함"
    spec = DialogueSpec(
        scene="conversation",
        fallback="카카오가 왜?",
        user_text="카카오에 관해서 말해 보자",
        memories=(memory,),
        memory_use_policy="Optional",
    )

    assert sanitize(
        generated(memory, "conversation", memory_references=(0,)), spec
    ) is None
    contextual = "카카오는 싫다고 했었지. 이번에는 어떤 얘기야?"
    assert sanitize(
        generated(contextual, "conversation", memory_references=(0,)), spec
    ) == contextual


@pytest.mark.parametrize(
    "text",
    [
        "기억했어.",
        "응, 저장했어!",
        "알겠어. 기억해둘게.",
        "그렇구나, 새겨 둘게.",
        "기억했어. 저장했어.",
    ],
)
def test_memory_share_rejects_a_bare_storage_acknowledgement(text: str) -> None:
    spec = DialogueSpec(
        scene="conversation",
        fallback="다음에 관련된 이야기가 나오면 자연스럽게 이어볼게.",
        user_text="나는 비 오는 날을 좋아해",
        contextual_memory_ack_required=True,
    )

    assert sanitize(generated(text, "conversation"), spec) is None


def test_memory_share_allows_acknowledgement_connected_to_the_shared_content() -> None:
    spec = DialogueSpec(
        scene="conversation",
        fallback="다음에 관련된 이야기가 나오면 자연스럽게 이어볼게.",
        user_text="나는 비 오는 날을 좋아해",
        contextual_memory_ack_required=True,
    )
    text = "비 오는 날을 좋아하는구나. 다음에 날씨 얘기할 때 기억해둘게."

    assert sanitize(generated(text, "conversation"), spec) == text


@pytest.mark.asyncio
async def test_memory_share_bare_acknowledgement_uses_contextual_fallback() -> None:
    provider = AsyncMock()
    provider.generate_dialogue.return_value = generated("기억해둘게.", "conversation")
    fallback = "다음에 이 얘기와 이어지는 일이 생기면 방금 말해 준 것부터 떠올려볼게."
    spec = DialogueSpec(
        scene="conversation",
        fallback=fallback,
        user_text="나는 비 오는 날을 좋아해",
        contextual_memory_ack_required=True,
    )

    assert await render(provider, spec) == fallback


def test_sanitize_rejects_a_player_name_as_the_companion_self_identity() -> None:
    spec = DialogueSpec(scene="conversation", fallback="내 이름은 마코야.")

    assert sanitize("내 이름은 윤석열입니다.", spec) is None
    assert sanitize("진짜 내 이름이 윤석열인 줄 알았나 보네.", spec) is None
    assert sanitize("윤석열이 내 이름이야.", spec) is None
    assert sanitize("내 이름은 마코야.", spec) == "내 이름은 마코야."


def test_companion_name_question_must_answer_with_mako_identity() -> None:
    spec = DialogueSpec(
        scene="conversation",
        fallback="내 이름은 마코야.",
        user_text="니 이름이 윤석열이라고?",
    )

    assert sanitize("아까 그렇게 부르라고 했잖아.", spec) is None
    assert sanitize("아니, 내 이름은 마코야.", spec) == "아니, 내 이름은 마코야."


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("니 이름이 윤석열이라고?", True),
        ("네 이름 뭐야", True),
        ("너의 이름은?", True),
        ("네 이름 예쁘다", False),
    ],
)
def test_companion_name_query_does_not_capture_normal_name_comments(
    text: str, expected: bool
) -> None:
    assert is_companion_name_query(text) is expected


def test_derived_time_answer_requires_the_exact_calculated_numbers() -> None:
    spec = DialogueSpec(
        scene="conversation",
        fallback="출근까지 8시간 35분 남았어.",
        derived_facts=("출근까지 8시간 35분 남았다.",),
        required_derived_numbers=("8", "35"),
    )

    assert sanitize("출근은 9시 반이야.", spec) is None
    assert sanitize("출근까지 8시간 35분 남았어.", spec) == (
        "출근까지 8시간 35분 남았어."
    )


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


def test_required_memory_infers_an_omitted_reference_from_a_grounded_answer() -> None:
    spec = DialogueSpec(
        scene="conversation",
        fallback="기억나는 게 없어.",
        memories=("[M0] 출근시간은 9시 반이야 기억해",),
        memory_use_policy="Required",
    )

    assert sanitize(generated("9시 반이야.", "conversation"), spec) == "9시 반이야."


def test_required_memory_does_not_infer_a_reference_for_a_changed_number() -> None:
    spec = DialogueSpec(
        scene="conversation",
        fallback="기억나는 게 없어.",
        memories=("[M0] 출근시간은 9시 반이야",),
        memory_use_policy="Required",
    )

    assert sanitize(generated("출근시간은 8시야.", "conversation"), spec) is None


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

    assert response == "출근시간은 9시 반이야"
    assert references == ((0,),)


@pytest.mark.asyncio
async def test_memory_fallback_does_not_repeat_a_raw_storage_request() -> None:
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

    response = await render(provider, spec)

    assert response == "출근시간은 9시 반이야"
    assert "기억해" not in response


@pytest.mark.asyncio
async def test_player_name_memory_fallback_changes_first_person_to_second_person() -> None:
    provider = AsyncMock()
    provider.generate_dialogue.return_value = generated("잘 모르겠어.", "conversation")
    spec = DialogueSpec(
        scene="conversation",
        fallback="기억나는 게 없어.",
        memories=(
            PromptMemory(
                prompt_text=(
                    "[M0] owner=Player; type=ProfileFact; "
                    "player_statement=내 이름은 윤석열입니다"
                ),
                claim_text="내 이름은 윤석열입니다",
            ),
        ),
        memory_use_policy="Required",
    )

    assert await render(provider, spec) == "네 이름은 윤석열이야."


@pytest.mark.asyncio
async def test_optional_memory_rejection_never_exposes_the_selected_memory_text() -> None:
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

    assert response == spec.fallback
    assert references == ()


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
