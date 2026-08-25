from __future__ import annotations

import json
import logging
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.brain.dialogue import DialogueOutput, DialogueScene, DialogueSpec
from app.brain.intent import CommandLabel, ResourceSlot, TopIntent
from app.brain.llm import (
    LocalLLMProvider,
    MockLLMProvider,
    OpenAIProvider,
    TimingLLMProvider,
    _dialogue_user_message,
    build_llm_provider,
)
from app.brain.recipes import RecipeSelectionOption
from app.brain.store import ConversationTurn
from app.models import Surface
from app.settings import Settings
from tests.conftest import make_settings


def test_legacy_prompt_version_is_upgraded_to_the_active_persona() -> None:
    settings = Settings(_env_file=None, companion_prompt_version="companion-v4")  # type: ignore[arg-type]

    assert settings.companion_prompt_version == "companion-v8"


def dialogue_json(
    text: str,
    purpose: DialogueScene,
    *,
    fact_references: tuple[int, ...] = (),
    accepts_command: bool = False,
) -> str:
    return json.dumps(
        {
            "text": text,
            "purpose": purpose,
            "fact_references": fact_references,
            "memory_references": (),
            "situation_references": (),
            "accepts_command": accepts_command,
        },
        ensure_ascii=False,
    )


# 단언이 확인하는 값은 **전부 명시한다.** 기본값에 기대면 그 기본값이 바뀔 때
# 테스트가 조용히 다른 것을 검사하게 된다.
def local_config() -> Settings:
    return make_settings(
        llm_provider="local",
        local_llm_base_url="http://192.168.0.55:18080/v1",
        local_llm_api_key="test-key",
        local_llm_model="balanced-q4-k-m-mtp",
        local_llm_timeout_seconds=30.0,
        classify_temperature=0.0,
        classify_max_tokens=64,
        dialogue_temperature=0.6,
        dialogue_max_tokens=160,
    )


def openai_config() -> Settings:
    return make_settings(
        llm_provider="openai",
        openai_api_key="test-key",
        openai_model="gpt-5-nano",
        openai_timeout_seconds=8.0,
        classify_temperature=0.0,
        classify_max_tokens=64,
        dialogue_temperature=0.6,
        dialogue_max_tokens=160,
    )


@pytest.mark.asyncio
async def test_openai_provider_uses_minimal_reasoning() -> None:
    client = MagicMock()
    client.responses.create = AsyncMock(
        side_effect=[
            SimpleNamespace(output_text='{"intent":"conversation"}'),
            SimpleNamespace(
                output_text='{"command":"wait","resource":"unspecified","quantity":null}'
            ),
            SimpleNamespace(
                output_text=dialogue_json("여기서 기다릴게.", "wait", accepts_command=True)
            ),
        ]
    )

    with patch("openai.AsyncOpenAI", return_value=client):
        provider = OpenAIProvider(openai_config())

    assert (
        await provider.classify_top("안녕", clarification_pending=False) is TopIntent.CONVERSATION
    )
    assert (await provider.classify_command("기다려")).command is CommandLabel.WAIT
    assert await provider.generate_dialogue(
        DialogueSpec(
            scene="wait",
            fallback="알겠어. 여기서 기다릴게.",
            command_candidate_present=True,
        )
    ) == DialogueOutput(
        text="여기서 기다릴게.",
        purpose="wait",
        fact_references=(),
        memory_references=(),
        situation_references=(),
        accepts_command=True,
    )

    assert client.responses.create.await_count == 3
    for call in client.responses.create.await_args_list:
        assert call.kwargs["reasoning"] == {"effort": "minimal"}


def test_builds_local_provider_when_configured() -> None:
    with patch("openai.AsyncOpenAI") as client_class:
        selected = build_llm_provider(local_config())

    assert isinstance(selected.provider, TimingLLMProvider)
    assert isinstance(selected.provider._inner, LocalLLMProvider)
    assert (selected.name, selected.model_version) == ("local", "balanced-q4-k-m-mtp")
    client_class.assert_called_once_with(
        base_url="http://192.168.0.55:18080/v1",
        api_key="test-key",
        timeout=30.0,
    )


@pytest.mark.asyncio
async def test_local_provider_uses_chat_completions() -> None:
    client = MagicMock()
    client.chat.completions.create = AsyncMock(
        return_value=SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content='{"text":"반가워!"}'))]
        )
    )

    with patch("openai.AsyncOpenAI", return_value=client):
        provider = LocalLLMProvider(local_config())

    spec = DialogueSpec(
        scene="conversation",
        fallback="안녕! 오늘은 어디부터 둘러볼까?",
        user_text="안녕, 마코",
    )
    assert (await provider.generate_dialogue(spec)).text == "반가워!"
    call = client.chat.completions.create.await_args.kwargs
    assert call["model"] == "balanced-q4-k-m-mtp"
    assert call["temperature"] == 0.6
    assert call["max_tokens"] == 160
    assert call["extra_body"] == {"chat_template_kwargs": {"enable_thinking": False}}
    dialogue_schema = call["response_format"]["json_schema"]
    assert dialogue_schema["name"] == "conversation_dialogue_output"
    assert set(dialogue_schema["schema"]["required"]) == {"text"}
    assert call["messages"][0]["role"] == "system"
    assert "확정 사실" in call["messages"][0]["content"]
    assert "출력 JSON에는 text만 넣는다" in call["messages"][0]["content"]
    assert call["messages"][1] == {
        "role": "user",
        "content": (
            "[지시] 플레이어의 말에 가볍게 반응한다.\n"
            "[확정 사실] 없음\n"
            "[Command Candidate] 없음\n"
            "[플레이어] 안녕, 마코"
        ),
    }
    assert spec.fallback not in str(call["messages"])


@pytest.mark.asyncio
async def test_local_provider_resolves_natural_language_to_allowlisted_recipe_ids() -> None:
    client = MagicMock()
    client.chat.completions.create = AsyncMock(
        return_value=SimpleNamespace(
            choices=[
                SimpleNamespace(
                    message=SimpleNamespace(
                        content=(
                            '{"decision":"match","candidate_recipe_ids":["recipe-1"],'
                            '"confidence":94}'
                        )
                    )
                )
            ]
        )
    )
    with patch("openai.AsyncOpenAI", return_value=client):
        provider = LocalLLMProvider(local_config())

    result = await provider.resolve_recipe(
        "상처에 대충 감는 거 만드는 법",
        (
            RecipeSelectionOption(
                recipe_id="recipe-1",
                result_name="엉성한 붕대",
                aliases=("엉성한 붕대", "붕대"),
            ),
        ),
    )

    assert result.candidate_recipe_ids == ("recipe-1",)
    call = client.chat.completions.create.await_args.kwargs
    assert call["response_format"]["json_schema"]["name"] == "recipe_selection"
    assert "재료·수량·작업대·시간" in call["messages"][0]["content"]
    assert "recipe-1 | 엉성한 붕대" in call["messages"][1]["content"]


def test_missing_local_key_keeps_mock_provider() -> None:
    selected = build_llm_provider(make_settings(llm_provider="local", local_llm_api_key=None))

    assert isinstance(selected.provider, TimingLLMProvider)
    assert isinstance(selected.provider._inner, MockLLMProvider)
    # 폴백했으면 메타데이터도 mock 을 가리켜야 한다. 어긋나면 응답이 거짓말을 한다.
    assert (selected.name, selected.model_version) == ("mock", "mock-v1")


def test_timing_disabled_returns_bare_provider() -> None:
    selected = build_llm_provider(
        make_settings(llm_provider="local", local_llm_api_key=None, llm_step_timing=False)
    )

    assert isinstance(selected.provider, MockLLMProvider)


@pytest.mark.asyncio
async def test_timing_provider_logs_step_duration() -> None:
    # aire.backend 로거는 configure_logging 에서 propagate=False 라 caplog(루트 전파)로는
    # 잡히지 않는다. 로거에 직접 핸들러를 달아 스텝 로그를 수집한다.
    provider = TimingLLMProvider(MockLLMProvider())
    records: list[logging.LogRecord] = []
    handler = logging.Handler()
    handler.emit = records.append  # type: ignore[method-assign]
    logger = logging.getLogger("aire.backend")
    previous_level = logger.level
    logger.setLevel(logging.INFO)
    logger.addHandler(handler)
    try:
        await provider.classify_top("따라와", clarification_pending=False)
    finally:
        logger.removeHandler(handler)
        logger.setLevel(previous_level)

    record = next(r for r in records if getattr(r, "event", None) == "llm_step")
    assert record.step == "classify_top"  # type: ignore[attr-defined]
    assert isinstance(record.duration_ms, float)  # type: ignore[attr-defined]


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("따라와", TopIntent.COMMAND),
        ("저것 좀 캐 줘", TopIntent.COMMAND),
        ("철광석을 캐 줘", TopIntent.COMMAND),
        ("취소", TopIntent.COMMAND),
        ("참호병 공격해", TopIntent.COMMAND),
        ("내 옆으로 돌아와", TopIntent.COMMAND),
        ("철검 만드는 법을 알려 줘", TopIntent.RECIPE),
        ("골리앗 약점이 뭐야?", TopIntent.ENEMY),
        ("철검으로 골리앗 잡는 방법", TopIntent.ENEMY),
        # "잡아"는 공격 명령이 아니라 공략법을 묻는 질문이어야 한다 — 공격 동사와
        # 겹치지 않게 고른 이유(app/brain/command_intent.py 참고).
        ("참호병 어떻게 잡아?", TopIntent.ENEMY),
        ("이 마을은 어떤 곳이야?", TopIntent.LORE),
        ("안녕, 마코", TopIntent.CONVERSATION),
        ("오늘 비가 올까?", TopIntent.CONVERSATION),
        ("너는 오늘 뭐 하고 싶어?", TopIntent.CONVERSATION),
        ("오늘은 조금 힘들었어", TopIntent.CONVERSATION),
        ("나는 비 오는 날의 조용한 소리를 좋아해", TopIntent.CONVERSATION),
        ("미확인 보스의 체력이 몇이야?", TopIntent.UNKNOWN),
    ],
)
async def test_mock_provider_classifies_top_intent(text: str, expected: TopIntent) -> None:
    provider = MockLLMProvider()

    assert await provider.classify_top(text, clarification_pending=False) is expected


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("따라와", CommandLabel.FOLLOW_PLAYER),
        ("여기서 기다려", CommandLabel.WAIT),
        ("그만", CommandLabel.STOP_CURRENT_TASK),
        ("됐어", CommandLabel.STOP_CURRENT_TASK),
        ("취소", CommandLabel.STOP_CURRENT_TASK),
        ("나중에 하자", CommandLabel.STOP_CURRENT_TASK),
        ("나무를 모아 줘", CommandLabel.GATHER_RESOURCE),
        ("나무를 모아줘", CommandLabel.GATHER_RESOURCE),
        ("돌 캐줘", CommandLabel.GATHER_RESOURCE),
        ("저것 좀 캐 줘", CommandLabel.GATHER_RESOURCE),
        ("철광석을 캐 줘", CommandLabel.GATHER_RESOURCE),
        ("나무 30개만 캐놔줘", CommandLabel.GATHER_RESOURCE),
        ("나무 30개 캐 놓아줘", CommandLabel.GATHER_RESOURCE),
        ("나무 30개 캐둬", CommandLabel.GATHER_RESOURCE),
        ("나무 30개 모아놔줘", CommandLabel.GATHER_RESOURCE),
        ("참호병 공격해", CommandLabel.ATTACK),
        ("공격해", CommandLabel.ATTACK),
        ("싸워", CommandLabel.ATTACK),
        ("내 옆으로 돌아와", CommandLabel.RETURN_TO_PLAYER),
        ("이리 와", CommandLabel.RETURN_TO_PLAYER),
        ("문을 열어 줘", CommandLabel.UNKNOWN),
    ],
)
async def test_mock_provider_classifies_command(text: str, expected: CommandLabel) -> None:
    provider = MockLLMProvider()

    assert (await provider.classify_command(text)).command is expected


@pytest.mark.parametrize(
    ("text", "resource", "quantity"),
    [
        ("나무를 모아 줘", ResourceSlot.WOOD, None),
        ("장작 좀 모아줘", ResourceSlot.WOOD, None),
        ("나무 20개 캐 줘", ResourceSlot.WOOD, 20),
        ("돌 캐줘", ResourceSlot.STONE, None),
        ("바위 3개 캐 줘", ResourceSlot.STONE, 3),
        # 지시대명사는 자원 미지정, 허용 목록 밖 자원은 미지원으로 나뉜다.
        ("저것 좀 캐 줘", ResourceSlot.UNSPECIFIED, None),
        ("부싯돌 캐 줘", ResourceSlot.OTHER, None),
        ("철광석을 캐 줘", ResourceSlot.OTHER, None),
        # 정수로 옮길 수 없는 수량 표현은 반드시 None으로 떨어져야 한다.
        ("나무 가방 찰 때까지 모아 줘", ResourceSlot.WOOD, None),
        ("나무 많이 캐 줘", ResourceSlot.WOOD, None),
        ("나무 스무 개 캐 줘", ResourceSlot.WOOD, None),
        # 온전하지 않은 숫자를 수량으로 주워 담으면 안 된다.
        # normalize()가 문장부호를 지우므로 "1.5개"는 "1 5개"가 된다.
        ("나무 1.5개 캐 줘", ResourceSlot.WOOD, None),
        ("나무 -1개 캐 줘", ResourceSlot.WOOD, None),
        ("나무 20개 30개 캐 줘", ResourceSlot.WOOD, None),
        # 자원을 여럿 말하면 하나를 임의로 고르지 않고 되묻는다.
        ("돌이랑 나무를 모아 줘", ResourceSlot.UNSPECIFIED, None),
        ("나무랑 돌 20개 캐 줘", ResourceSlot.UNSPECIFIED, 20),
    ],
)
async def test_mock_provider_fills_gather_slots(
    text: str, resource: ResourceSlot, quantity: int | None
) -> None:
    classification = await MockLLMProvider().classify_command(text)

    assert classification.command is CommandLabel.GATHER_RESOURCE
    assert classification.resource is resource
    assert classification.quantity == quantity


@pytest.mark.asyncio
async def test_local_provider_parses_structured_top_classification() -> None:
    client = MagicMock()
    client.chat.completions.create = AsyncMock(
        return_value=SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content='{"intent":"recipe"}'))]
        )
    )

    with patch("openai.AsyncOpenAI", return_value=client):
        provider = LocalLLMProvider(local_config())

    assert (
        await provider.classify_top(
            "그건 어떻게 만들어?",
            clarification_pending=False,
            history=(
                ConversationTurn(speaker="player", text="돌도끼가 궁금해"),
                ConversationTurn(speaker="companion", text="제작법을 물어보는 거야?"),
            ),
        )
        is TopIntent.RECIPE
    )
    call = client.chat.completions.create.await_args.kwargs
    assert call["temperature"] == 0.0
    assert call["max_tokens"] == 64
    assert call["extra_body"] == {"chat_template_kwargs": {"enable_thinking": False}}
    assert "돌도끼가 궁금해" in call["messages"][1]["content"]
    assert "[현재 발화]\n그건 어떻게 만들어?" in call["messages"][1]["content"]
    schema = call["response_format"]["json_schema"]["schema"]
    assert schema["$defs"]["TopIntent"]["enum"] == [
        "command",
        "recipe",
        "enemy",
        "lore",
        "conversation",
        "unknown",
    ]
    assert schema["additionalProperties"] is False
    assert schema["required"] == ["intent"]


@pytest.mark.asyncio
async def test_local_top_classification_falls_back_on_invalid_output() -> None:
    client = MagicMock()
    client.chat.completions.create = AsyncMock(
        return_value=SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content="not json"))]
        )
    )

    with patch("openai.AsyncOpenAI", return_value=client):
        provider = LocalLLMProvider(local_config())

    assert await provider.classify_top("따라와", clarification_pending=False) is TopIntent.COMMAND


@pytest.mark.asyncio
async def test_local_provider_parses_structured_command_classification() -> None:
    client = MagicMock()
    client.chat.completions.create = AsyncMock(
        return_value=SimpleNamespace(
            choices=[
                SimpleNamespace(
                    message=SimpleNamespace(
                        content=('{"command":"gather_resource","resource":"wood","quantity":20}')
                    )
                )
            ]
        )
    )

    with patch("openai.AsyncOpenAI", return_value=client):
        provider = LocalLLMProvider(local_config())

    classification = await provider.classify_command("주변의 나무를 20개 구해 줘")
    assert classification.command is CommandLabel.GATHER_RESOURCE
    assert classification.resource is ResourceSlot.WOOD
    assert classification.quantity == 20

    call = client.chat.completions.create.await_args.kwargs
    assert call["temperature"] == 0.0
    assert call["max_tokens"] == 64
    assert call["extra_body"] == {"chat_template_kwargs": {"enable_thinking": False}}
    schema = call["response_format"]["json_schema"]["schema"]
    assert schema["$defs"]["CommandLabel"]["enum"] == [
        "follow_player",
        "wait",
            "stop_current_task",
            "gather_resource",
            "craft_item",
            "attack",
        "return_to_player",
        "unknown",
    ]
    assert schema["$defs"]["ResourceSlot"]["enum"] == [
        "wood",
        "stone",
        "other",
        "unspecified",
    ]
    assert schema["additionalProperties"] is False
    # strict 구조화 출력은 모든 프로퍼티가 required 여야 한다. 슬롯에 기본값을
    # 주면 여기서 빠져 스키마가 거부되므로 세 필드 모두 있어야 한다.
    assert schema["required"] == ["command", "resource", "quantity"]


@pytest.mark.asyncio
async def test_local_command_classification_falls_back_on_invalid_output() -> None:
    client = MagicMock()
    client.chat.completions.create = AsyncMock(
        return_value=SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content="not json"))]
        )
    )

    with patch("openai.AsyncOpenAI", return_value=client):
        provider = LocalLLMProvider(local_config())

    assert (await provider.classify_command("그만")).command is CommandLabel.STOP_CURRENT_TASK


@pytest.mark.asyncio
async def test_local_provider_falls_back_when_call_fails() -> None:
    client = MagicMock()
    client.chat.completions.create = AsyncMock(side_effect=ConnectionError)

    with patch("openai.AsyncOpenAI", return_value=client):
        provider = LocalLLMProvider(local_config())

    spec = DialogueSpec(
        scene="conversation",
        fallback="안녕! 오늘은 어디부터 둘러볼까?",
        user_text="안녕",
        relationship_state="High",
    )
    assert (await provider.generate_dialogue(spec)).text == spec.fallback


@pytest.mark.asyncio
async def test_local_dialogue_prompt_contains_facts_but_not_fallback() -> None:
    client = MagicMock()
    client.chat.completions.create = AsyncMock(
        return_value=SimpleNamespace(
            choices=[
                SimpleNamespace(
                    message=SimpleNamespace(
                        content=dialogue_json("철괴 3개가 필요해.", "recipe", fact_references=(0,))
                    )
                )
            ]
        )
    )

    with patch("openai.AsyncOpenAI", return_value=client):
        provider = LocalLLMProvider(local_config())

    spec = DialogueSpec(
        scene="recipe",
        fallback="이 폴백 문장은 프롬프트에 들어가면 안 돼.",
        user_text="철검는 어떻게 만들어?",
        facts=("철괴 3개가 필요하다",),
        relationship_state="High",
    )
    assert (await provider.generate_dialogue(spec)).text == "철괴 3개가 필요해."

    messages = client.chat.completions.create.await_args.kwargs["messages"]
    assert "철괴 3개가 필요하다" in str(messages)
    assert "현재 단계는 High" in messages[0]["content"]
    assert spec.fallback not in str(messages)


@pytest.mark.asyncio
async def test_local_memory_conversation_requires_declared_memory_references() -> None:
    client = MagicMock()
    client.chat.completions.create = AsyncMock(
        return_value=SimpleNamespace(
            choices=[
                SimpleNamespace(
                    message=SimpleNamespace(
                        content=json.dumps(
                            {
                                "text": "네 이름은 대통령 윤 석열이야.",
                                "memory_references": [0],
                            },
                            ensure_ascii=False,
                        )
                    )
                )
            ]
        )
    )

    with patch("openai.AsyncOpenAI", return_value=client):
        provider = LocalLLMProvider(local_config())

    result = await provider.generate_dialogue(
        DialogueSpec(
            scene="conversation",
            fallback="안녕! 무슨 일이야?",
            user_text="내 이름은?",
            memories=("[M0] 제이름은 대통령 윤 석열입니다",),
        )
    )

    assert result.text == "네 이름은 대통령 윤 석열이야."
    assert result.memory_references == (0,)
    call = client.chat.completions.create.await_args.kwargs
    assert call["response_format"]["json_schema"]["name"] == (
        "memory_conversation_dialogue_output"
    )


@pytest.mark.asyncio
async def test_mock_returns_the_scene_fallback_verbatim() -> None:
    """규칙으로 흉내 낸 대사가 있으면 창구가 늘 때 그 흉내도 창구마다 갈라진다."""

    spec = DialogueSpec(scene="conversation", fallback="안녕! 무슨 일이야?", user_text="안녕")

    assert (await MockLLMProvider().generate_dialogue(spec)).text == spec.fallback


@pytest.mark.asyncio
async def test_dialogue_system_prompt_switches_with_the_surface() -> None:
    """게임은 옆에 서서, 모바일은 폰 채팅으로 말한다. 어조는 시스템 프롬프트가 정한다."""

    client = MagicMock()
    client.chat.completions.create = AsyncMock(
        return_value=SimpleNamespace(
            choices=[
                SimpleNamespace(message=SimpleNamespace(content='{"text":"응, 나 여기 있어."}'))
            ]
        )
    )

    with patch("openai.AsyncOpenAI", return_value=client):
        provider = LocalLLMProvider(local_config())

    prompts: dict[Surface, str] = {}
    for surface in Surface:
        await provider.generate_dialogue(
            DialogueSpec(scene="conversation", fallback="안녕!", surface=surface)
        )
        messages = client.chat.completions.create.await_args.kwargs["messages"]
        prompts[surface] = messages[0]["content"]

    assert "바로 옆에서 함께 움직이는 동료" in prompts[Surface.GAME]
    assert "자연스러운 메신저 대화" in prompts[Surface.MOBILE]
    assert prompts[Surface.GAME] != prompts[Surface.MOBILE]
    # 사실 규칙은 창구와 무관하다. 말투를 바꾸다 가드가 창구마다 달라지면 안 된다.
    for prompt in prompts.values():
        assert "[prompt_version] companion-v8" in prompt
        assert "오랫동안 여러 일을 함께해 온 친근한 동료" in prompt
        assert "존재하지 않는 공동 경험" in prompt
        assert "이모지는 꼭 감정 전달에 필요할 때 하나만" in prompt
        assert "이모지와 따옴표를 쓰지 않는다" not in prompt
        assert "현재 단계는 Low" in prompt
        assert "과도한 애착·독점·영원한 약속" in prompt
        assert "Command Candidate가 없으면" in prompt
        assert "[확정 사실]에 적힌 내용만 게임 사실로 사용하고" in prompt
        assert '"네가 알려준 내용/정보"' in prompt
        assert "이름을 넣어 인사한다" in prompt


def test_dialogue_message_omits_the_history_block_when_there_is_none() -> None:
    """기록이 없으면 프롬프트 모양은 B층 이전과 같아야 한다."""

    message = _dialogue_user_message(
        DialogueSpec(scene="wait", fallback="여기서 기다릴게.", user_text="기다려")
    )

    assert "[최근 대화]" not in message
    assert message.startswith("[지시]")


def test_dialogue_message_puts_situation_between_memory_and_history() -> None:
    message = _dialogue_user_message(
        DialogueSpec(
            scene="conversation",
            fallback="안녕!",
            user_text="졸려",
            situation=("지금은 게임 세계 기준 7일차 밤, 23시다.",),
            memories=("플레이어는 밤을 싫어한다",),
            history=(ConversationTurn(speaker="player", text="오늘은 길게 걷자"),),
        )
    )

    assert message.index("[기억]") < message.index("[상황]")
    assert message.index("[상황]") < message.index("[최근 대화]")
    assert "[0] 지금은 게임 세계 기준 7일차 밤, 23시다." in message


def test_dialogue_message_puts_history_before_the_instruction() -> None:
    """지시·사실·현재 발화가 뒤에 와야 무엇을 말할지는 [지시]가 정한다."""

    message = _dialogue_user_message(
        DialogueSpec(
            scene="conversation",
            fallback="안녕!",
            user_text="그거 재료가 뭐라고?",
            history=(
                ConversationTurn(speaker="player", text="철검 어떻게 만들어?"),
                ConversationTurn(speaker="companion", text="철괴 3개가 필요해."),
            ),
        )
    )

    assert message.index("[최근 대화]") < message.index("[지시]")
    assert message.index("[지시]") < message.index("[플레이어]")
    assert "플레이어: 철검 어떻게 만들어?" in message
    assert "마코: 철괴 3개가 필요해." in message


def test_dialogue_message_omits_the_memory_block_when_there_is_none() -> None:
    """기억이 없으면 프롬프트 모양은 장기기억 이전과 같아야 한다."""

    message = _dialogue_user_message(
        DialogueSpec(scene="wait", fallback="여기서 기다릴게.", user_text="기다려")
    )

    assert "[기억]" not in message


def test_dialogue_message_puts_memories_before_the_history() -> None:
    """기억은 기록보다도 더 오래된 배경이라 가장 앞이고, 무엇을 말할지는 [지시]가 정한다."""

    message = _dialogue_user_message(
        DialogueSpec(
            scene="conversation",
            fallback="안녕!",
            user_text="다시 왔어",
            memories=("플레이어는 밤을 싫어한다",),
            history=(ConversationTurn(speaker="player", text="안녕"),),
        )
    )

    assert message.index("[기억]") < message.index("[최근 대화]")
    assert message.index("[최근 대화]") < message.index("[지시]")
    assert "[0] 플레이어는 밤을 싫어한다" in message


def test_dialogue_message_keeps_memories_out_of_the_verified_facts() -> None:
    """기억이 [확정 사실] 로 승격되면 검증되지 않은 말이 게임 사실이 된다."""

    message = _dialogue_user_message(
        DialogueSpec(
            scene="recipe",
            fallback="확인된 제작법이 없어.",
            user_text="철검?",
            memories=("플레이어는 도끼를 자주 만든다",),
        )
    )

    assert "[확정 사실] 없음" in message


def test_dialogue_message_omits_the_player_line_without_user_text() -> None:
    """상황 이벤트는 플레이어가 한 말이 없다 — `[플레이어]` 줄 자체가 빠져야 한다."""

    message = _dialogue_user_message(
        DialogueSpec(
            scene="situation",
            fallback="방금 그거, 봤어?",
            user_text=None,
            situation=("적이 나타났다",),
        )
    )

    assert "[플레이어]" not in message
    assert "[0] 적이 나타났다" in message


def test_dialogue_message_labels_a_situation_speaker_in_history() -> None:
    message = _dialogue_user_message(
        DialogueSpec(
            scene="wait",
            fallback="여기서 기다릴게.",
            user_text="기다려",
            history=(
                ConversationTurn(speaker="situation", text="적이 나타났다"),
                ConversationTurn(speaker="companion", text="조심해!"),
            ),
        )
    )

    assert "상황: 적이 나타났다" in message
    assert "마코: 조심해!" in message


def test_dialogue_message_never_offers_the_fallback_as_a_template() -> None:
    spec = DialogueSpec(
        scene="wait",
        fallback="알겠어. 여기서 기다릴게.",
        user_text="기다려",
        history=(ConversationTurn(speaker="companion", text="이미 서 있어."),),
    )

    assert spec.fallback not in _dialogue_user_message(spec)
