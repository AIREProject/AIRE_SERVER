"""Contract tests for the deterministic Companion AI provider doubles."""

from datetime import UTC, datetime

import pytest

from app.brain.dialogue import DialogueSpec
from app.brain.intent import CommandLabel, ResourceSlot, TopIntent
from app.brain.memory import ConsolidationSpec, MemoryExtractionSpec, SessionSummarySpec
from app.brain.store import ConversationTurn, PendingSlot
from tests.support.companion_ai_provider_stubs import (
    InvalidLLMProvider,
    InvalidMode,
    ProviderMethod,
    ProviderScriptStep,
    ScriptedLLMProvider,
)


def step(method: ProviderMethod, semantic_key: str, result: object) -> ProviderScriptStep:
    return ProviderScriptStep(method=method, semantic_key=semantic_key, result=result)


async def test_scripted_provider_implements_every_typed_method_in_order() -> None:
    provider = ScriptedLLMProvider(
        "p0.provider.contract.001",
        (
            step("classify_top", "top", "conversation"),
            step(
                "classify_command",
                "command",
                {"command": "attack", "resource": "unspecified", "quantity": None},
            ),
            step("resolve_pending", "pending", "wood"),
            step("generate_dialogue", "dialogue:conversation", "합성 대사"),
            step(
                "extract_memories",
                "memory:extract",
                {"profile": [], "episode": None, "episode_importance": 1},
            ),
            step("summarize_session", "memory:summary", {"summary": None}),
            step("consolidate_memories", "memory:consolidate", {"memories": []}),
        ),
    )
    pending = PendingSlot(
        kind="gather_resource", quantity=None, ask_count=1, asked_at=datetime.now(UTC)
    )
    turns = (ConversationTurn(speaker="player", text="합성 발화"),)

    assert await provider.classify_top("원문은 기록하지 않는다", clarification_pending=False) is (
        TopIntent.CONVERSATION
    )
    classification = await provider.classify_command("원문은 기록하지 않는다")
    assert classification.command is CommandLabel.ATTACK
    assert await provider.resolve_pending("원문은 기록하지 않는다", pending) is ResourceSlot.WOOD
    assert (
        await provider.generate_dialogue(
            DialogueSpec(scene="conversation", fallback="고정 대사", user_text="합성 발화")
        )
        == "합성 대사"
    )
    extraction = await provider.extract_memories(MemoryExtractionSpec(recent_turns=turns))
    summary = await provider.summarize_session(SessionSummarySpec(turns=turns))
    consolidation = await provider.consolidate_memories(ConsolidationSpec(memories=()))
    await provider.aclose()

    assert extraction.profile == []
    assert summary.summary is None
    assert consolidation.memories == []
    provider.assert_consumed()
    assert [record.order for record in provider.calls] == list(range(1, 8))
    assert {field for record in provider.calls for field in record.__dataclass_fields__} == {
        "fixture_id",
        "method",
        "order",
        "semantic_key",
    }
    assert "원문은 기록하지 않는다" not in repr(provider.calls)


async def test_scripted_provider_rejects_unexpected_or_reordered_calls() -> None:
    provider = ScriptedLLMProvider(
        "p0.provider.order.001", (step("classify_top", "top", "conversation"),)
    )

    with pytest.raises(AssertionError, match="expected classify_top/top"):
        await provider.generate_dialogue(DialogueSpec(scene="conversation", fallback="고정 대사"))


async def test_scripted_provider_rejects_unconfigured_call() -> None:
    provider = ScriptedLLMProvider("p0.provider.missing.001", ())

    with pytest.raises(AssertionError, match="unexpected provider call classify_top"):
        await provider.classify_top("합성 발화", clarification_pending=False)


def test_scripted_provider_rejects_unconsumed_steps() -> None:
    provider = ScriptedLLMProvider(
        "p0.provider.remaining.001", (step("classify_top", "top", "conversation"),)
    )

    with pytest.raises(AssertionError, match="unconsumed provider steps: classify_top"):
        provider.assert_consumed()


@pytest.mark.parametrize(
    ("mode", "reason", "expected"),
    [
        ("timeout", "provider_timeout", "고정 대사"),
        ("unavailable", "provider_unavailable", "고정 대사"),
        ("empty", "empty_dialogue", " "),
    ],
)
async def test_invalid_provider_separates_dialogue_failure_modes(
    mode: InvalidMode, reason: str, expected: str
) -> None:
    provider = InvalidLLMProvider(
        f"p0.provider.{mode}.unit",
        (step("generate_dialogue", "dialogue:conversation", "사용되지 않는 대사"),),
        mode=mode,
    )

    result = await provider.generate_dialogue(
        DialogueSpec(scene="conversation", fallback="고정 대사")
    )

    assert result == expected
    assert provider.failures[0].reason == reason
    assert provider.failures[0].fallback_used
    provider.assert_consumed()


async def test_invalid_provider_returns_typed_disallowed_candidate() -> None:
    provider = InvalidLLMProvider(
        "p0.provider.invalid_candidate.unit",
        (
            step(
                "classify_command",
                "command",
                {"command": "attack", "resource": "unspecified", "quantity": None},
            ),
        ),
        mode="invalid_candidate",
    )

    result = await provider.classify_command("합성 공격 요청")

    assert result.command is CommandLabel.ATTACK
    assert provider.failures[0].reason == "invalid_command_candidate"
    assert not provider.failures[0].fallback_used
    provider.assert_consumed()
