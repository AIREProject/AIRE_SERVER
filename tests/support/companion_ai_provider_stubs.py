"""Deterministic, test-only implementations of the Companion LLM boundary."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from app.brain.dialogue import DialogueOutput, DialogueSpec
from app.brain.intent import CommandClassification, ResourceSlot, TopIntent
from app.brain.llm import LLMProvider, MockLLMProvider
from app.brain.memory import (
    Consolidation,
    ConsolidationSpec,
    MemoryExtraction,
    MemoryExtractionSpec,
    SessionSummary,
    SessionSummarySpec,
)
from app.brain.store import PendingSlot

ProviderMethod = Literal[
    "classify_top",
    "classify_command",
    "resolve_pending",
    "generate_dialogue",
    "extract_memories",
    "summarize_session",
    "consolidate_memories",
]
InvalidMode = Literal["timeout", "unavailable", "empty", "invalid_candidate"]


@dataclass(frozen=True, slots=True)
class ProviderScriptStep:
    method: ProviderMethod
    semantic_key: str
    result: object


@dataclass(frozen=True, slots=True)
class ProviderCallRecord:
    fixture_id: str
    method: ProviderMethod
    order: int
    semantic_key: str


@dataclass(frozen=True, slots=True)
class ProviderFailureRecord:
    fixture_id: str
    reason: str
    fallback_used: bool


class ScriptedLLMProvider(LLMProvider):
    """Return an ordered sequence of typed results and reject any script drift."""

    def __init__(self, fixture_id: str, steps: tuple[ProviderScriptStep, ...]) -> None:
        self._fixture_id = fixture_id
        self._steps = steps
        self._next_step = 0
        self._calls: list[ProviderCallRecord] = []

    @property
    def calls(self) -> tuple[ProviderCallRecord, ...]:
        return tuple(self._calls)

    def assert_consumed(self) -> None:
        remaining = self._steps[self._next_step :]
        if remaining:
            methods = ", ".join(step.method for step in remaining)
            raise AssertionError(f"{self._fixture_id}: unconsumed provider steps: {methods}")

    def _take(self, method: ProviderMethod, semantic_key: str) -> object:
        order = len(self._calls) + 1
        self._calls.append(
            ProviderCallRecord(
                fixture_id=self._fixture_id,
                method=method,
                order=order,
                semantic_key=semantic_key,
            )
        )
        if self._next_step >= len(self._steps):
            raise AssertionError(f"{self._fixture_id}: unexpected provider call {method}")
        step = self._steps[self._next_step]
        if step.method != method or step.semantic_key != semantic_key:
            raise AssertionError(
                f"{self._fixture_id}: expected {step.method}/{step.semantic_key}, "
                f"got {method}/{semantic_key}"
            )
        self._next_step += 1
        return step.result

    async def classify_top(self, text: str, *, clarification_pending: bool) -> TopIntent:
        del text, clarification_pending
        result = self._take("classify_top", "top")
        if not isinstance(result, str):
            raise AssertionError(f"{self._fixture_id}: classify_top result must be a string")
        return TopIntent(result)

    async def classify_command(self, text: str) -> CommandClassification:
        del text
        return CommandClassification.model_validate(self._take("classify_command", "command"))

    async def resolve_pending(self, text: str, pending: PendingSlot) -> ResourceSlot | None:
        del text, pending
        result = self._take("resolve_pending", "pending")
        if result is None:
            return None
        if not isinstance(result, str):
            raise AssertionError(f"{self._fixture_id}: resolve_pending result must be a string")
        return ResourceSlot(result)

    async def generate_dialogue(self, spec: DialogueSpec) -> DialogueOutput:
        result = self._take("generate_dialogue", f"dialogue:{spec.scene}")
        if not isinstance(result, str):
            raise AssertionError(f"{self._fixture_id}: dialogue result must be a string")
        grounded_scenes = {
            "recipe",
            "enemy",
            "lore",
            "unsupported",
            "event_completed",
            "event_failed",
        }
        return DialogueOutput(
            text=result,
            purpose=spec.scene,
            fact_references=(
                (0,) if spec.facts and spec.scene in grounded_scenes else ()
            ),
            memory_references=(),
            situation_references=(),
            accepts_command=spec.command_candidate_present,
        )

    async def extract_memories(self, spec: MemoryExtractionSpec) -> MemoryExtraction:
        del spec
        return MemoryExtraction.model_validate(self._take("extract_memories", "memory:extract"))

    async def summarize_session(self, spec: SessionSummarySpec) -> SessionSummary:
        del spec
        return SessionSummary.model_validate(self._take("summarize_session", "memory:summary"))

    async def consolidate_memories(self, spec: ConsolidationSpec) -> Consolidation:
        del spec
        return Consolidation.model_validate(
            self._take("consolidate_memories", "memory:consolidate")
        )


class InvalidLLMProvider(ScriptedLLMProvider):
    """Reproduce adapter failures while keeping every Python result typed."""

    def __init__(
        self,
        fixture_id: str,
        steps: tuple[ProviderScriptStep, ...],
        *,
        mode: InvalidMode,
        fallback: LLMProvider | None = None,
    ) -> None:
        super().__init__(fixture_id, steps)
        self._mode = mode
        self._fallback = fallback or MockLLMProvider()
        self._failures: list[ProviderFailureRecord] = []

    @property
    def failures(self) -> tuple[ProviderFailureRecord, ...]:
        return tuple(self._failures)

    def _record_failure(self, reason: str, *, fallback_used: bool) -> None:
        self._failures.append(
            ProviderFailureRecord(
                fixture_id=self._fixture_id,
                reason=reason,
                fallback_used=fallback_used,
            )
        )

    async def classify_command(self, text: str) -> CommandClassification:
        classification = await super().classify_command(text)
        if self._mode == "invalid_candidate":
            self._record_failure("invalid_command_candidate", fallback_used=False)
        return classification

    async def generate_dialogue(self, spec: DialogueSpec) -> DialogueOutput | str:
        scripted = await super().generate_dialogue(spec)
        if self._mode == "timeout":
            self._record_failure("provider_timeout", fallback_used=True)
            return await self._fallback.generate_dialogue(spec)
        if self._mode == "unavailable":
            self._record_failure("provider_unavailable", fallback_used=True)
            return await self._fallback.generate_dialogue(spec)
        if self._mode == "empty":
            self._record_failure("empty_dialogue", fallback_used=True)
            return " "
        return scripted
