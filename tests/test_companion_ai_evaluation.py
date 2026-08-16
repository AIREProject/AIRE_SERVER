"""Synthetic CAI-P0 semantic baseline through the current Companion service seam."""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace
from typing import Literal
from unittest.mock import AsyncMock, MagicMock

import openai
import pytest
from pydantic import BaseModel, ConfigDict, Field, JsonValue, SecretStr, model_validator
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.brain import CompanionBrain
from app.brain.llm import LLMProvider, LocalLLMProvider
from app.credentials import CredentialProtector
from app.db.connection import Database
from app.db.models import EpisodicMemoryModel, OfflineTaskModel
from app.db.save_slot_repository import SaveSlotRepository
from app.identity import AuthenticatedDevice
from app.models import AIMetadata, ChatRequest, ChatResponse, CommandType, Surface
from app.service import CompanionService
from tests.conftest import make_authenticated_device, make_database, make_settings
from tests.support.companion_ai_provider_stubs import (
    InvalidLLMProvider,
    InvalidMode,
    ProviderMethod,
    ProviderScriptStep,
    ScriptedLLMProvider,
)

FIXTURE_DIRECTORY = Path(__file__).parent / "fixtures" / "companion_ai"
FIXTURE_ID_PATTERN = re.compile(r"^p0\.[a-z0-9_]+\.[a-z0-9_]+\.\d{3}$")
PROTECTOR = CredentialProtector(SecretStr("fixture-only-pepper-not-for-production"))
METADATA = AIMetadata(
    provider="scripted-v1", model_version="scripted-v1", prompt_version="companion-v2"
)

ProviderName = Literal["scripted-v1", "invalid-v1", "local-invalid-json-v1"]
KnownGapField = Literal["query_mode", "fact_ids", "fallback_reason"]
QueryMode = Literal[
    "list_known",
    "detail",
    "compare",
    "ambiguous",
    "unknown_recipe",
    "conversation",
    "preference_share",
    "unsupported_fact",
    "information_question",
    "not_observed",
]


class StrictFixtureModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class FixtureRequest(StrictFixtureModel):
    text: str = Field(min_length=1, max_length=2000)
    surface: Surface
    conversation_id: str = Field(min_length=1, max_length=128)
    allowed_commands: list[CommandType] = Field(max_length=16)
    prior_turns: list[str] = Field(max_length=4)


class FixtureResponse(StrictFixtureModel):
    method: ProviderMethod
    semantic_key: str = Field(min_length=1, max_length=64)
    result: JsonValue


class FixtureScript(StrictFixtureModel):
    provider: ProviderName
    invalid_mode: InvalidMode | None = None
    responses: list[FixtureResponse] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_invalid_mode(self) -> FixtureScript:
        if self.provider == "invalid-v1" and self.invalid_mode is None:
            raise ValueError("invalid-v1 requires invalid_mode")
        if self.provider != "invalid-v1" and self.invalid_mode is not None:
            raise ValueError("invalid_mode is only valid for invalid-v1")
        return self


class FixtureExpectation(StrictFixtureModel):
    top_intent: Literal["command", "recipe", "enemy", "lore", "conversation", "unknown"]
    query_mode: QueryMode
    allowed_fact_ids: list[str]
    forbidden_fact_ids: list[str]
    allowed_command_types: list[CommandType]
    fallback: bool
    fallback_reason: str | None
    memory_persisted: bool
    db_side_effect: Literal["none"]
    known_gaps: dict[KnownGapField, str]


class CompanionAIFixture(StrictFixtureModel):
    fixture_id: str
    request: FixtureRequest
    script: FixtureScript
    expect: FixtureExpectation

    @model_validator(mode="after")
    def validate_fixture_id(self) -> CompanionAIFixture:
        if FIXTURE_ID_PATTERN.fullmatch(self.fixture_id) is None:
            raise ValueError("fixture_id must use p0.<domain>.<case>.<number>")
        return self


@dataclass(frozen=True, slots=True)
class BusinessTableCounts:
    episodic_memories: int
    offline_tasks: int


@dataclass(frozen=True, slots=True)
class ProviderBundle:
    provider: LLMProvider
    recorder: ScriptedLLMProvider
    injected_fallback: bool


@dataclass(frozen=True, slots=True)
class ObservedEvaluation:
    top_intent: str
    query_mode: str
    fact_ids: tuple[str, ...] | str
    command_types: tuple[CommandType, ...]
    fallback: bool
    fallback_reason: str | None
    memory_persisted: bool
    db_side_effect: str


class _ProvenanceHandler(logging.Handler):
    def __init__(self) -> None:
        super().__init__()
        self.records: list[logging.LogRecord] = []

    def emit(self, record: logging.LogRecord) -> None:
        if record.msg == "response_provenance":
            self.records.append(record)


def load_fixtures(directory: Path = FIXTURE_DIRECTORY) -> tuple[CompanionAIFixture, ...]:
    loaded: list[CompanionAIFixture] = []
    seen_ids: set[str] = set()
    for path in sorted(directory.glob("*.json"), key=lambda item: item.name):
        fixture = CompanionAIFixture.model_validate_json(path.read_text(encoding="utf-8"))
        if fixture.fixture_id in seen_ids:
            raise ValueError(f"duplicate fixture_id: {fixture.fixture_id}")
        seen_ids.add(fixture.fixture_id)
        loaded.append(fixture)
    if not loaded:
        raise ValueError(f"no Companion AI fixtures found in {directory}")
    return tuple(loaded)


def _script_steps(fixture: CompanionAIFixture) -> tuple[ProviderScriptStep, ...]:
    return tuple(
        ProviderScriptStep(
            method=response.method,
            semantic_key=response.semantic_key,
            result=response.result,
        )
        for response in fixture.script.responses
    )


def _build_provider(
    fixture: CompanionAIFixture, monkeypatch: pytest.MonkeyPatch
) -> ProviderBundle:
    steps = _script_steps(fixture)
    if fixture.script.provider == "scripted-v1":
        provider = ScriptedLLMProvider(fixture.fixture_id, steps)
        return ProviderBundle(provider=provider, recorder=provider, injected_fallback=False)
    if fixture.script.provider == "invalid-v1":
        assert fixture.script.invalid_mode is not None
        provider = InvalidLLMProvider(
            fixture.fixture_id, steps, mode=fixture.script.invalid_mode
        )
        return ProviderBundle(provider=provider, recorder=provider, injected_fallback=False)

    fallback = ScriptedLLMProvider(fixture.fixture_id, steps)
    client = MagicMock()
    client.chat.completions.create = AsyncMock(
        side_effect=[
            SimpleNamespace(
                choices=[SimpleNamespace(message=SimpleNamespace(content="not valid json"))]
            ),
            ConnectionError("synthetic local adapter failure"),
        ]
    )
    client.close = AsyncMock()
    monkeypatch.setattr(openai, "AsyncOpenAI", lambda **_kwargs: client)
    provider = LocalLLMProvider(
        make_settings(llm_provider="local", local_llm_api_key="not-required"), fallback=fallback
    )
    return ProviderBundle(provider=provider, recorder=fallback, injected_fallback=True)


def _empty_game_context() -> dict[str, JsonValue]:
    return {
        "schema_version": 1,
        "location_id": "forest_camp",
        "threat": {"present": False, "count": 0, "nearest_kind": None},
        "nearby_resources": [],
        "available_workstations": [],
        "current_work": None,
        "inventories": [],
    }


def _request(fixture: CompanionAIFixture, text: str, *, turn_index: int) -> ChatRequest:
    request = fixture.request
    return ChatRequest(
        request_id=f"fixture-request-{fixture.fixture_id}-{turn_index}",
        session_id=request.conversation_id,
        save_slot_id="fixture-slot-001",
        companion_id="mako",
        user_message=text,
        surface=request.surface,
        game_context=_empty_game_context() if request.surface is Surface.GAME else None,
        allowed_commands=request.allowed_commands,
    )


async def _business_counts(session: AsyncSession) -> BusinessTableCounts:
    memories = await session.scalar(select(func.count()).select_from(EpisodicMemoryModel))
    tasks = await session.scalar(select(func.count()).select_from(OfflineTaskModel))
    return BusinessTableCounts(episodic_memories=memories or 0, offline_tasks=tasks or 0)


def _scripted_top_intent(fixture: CompanionAIFixture) -> str:
    top_steps = [
        response.result
        for response in fixture.script.responses
        if response.method == "classify_top"
    ]
    if not top_steps or not isinstance(top_steps[-1], str):
        raise AssertionError(f"{fixture.fixture_id}: main turn has no typed top intent")
    return top_steps[-1]


def _observe(
    fixture: CompanionAIFixture,
    response: ChatResponse,
    before: BusinessTableCounts,
    after: BusinessTableCounts,
    bundle: ProviderBundle,
    provenance: logging.LogRecord,
) -> ObservedEvaluation:
    failures = (
        bundle.provider.failures if isinstance(bundle.provider, InvalidLLMProvider) else ()
    )
    fallback = bundle.injected_fallback or any(failure.fallback_used for failure in failures)
    fact_ids: tuple[str, ...] | str = tuple(provenance.fact_ids)
    memory_persisted = after.episodic_memories > before.episodic_memories
    side_effect = "none" if before == after else "changed"
    return ObservedEvaluation(
        top_intent=_scripted_top_intent(fixture),
        query_mode=(
            "not_observed"
            if fixture.expect.query_mode == "not_observed"
            else provenance.query_mode
        ),
        fact_ids=fact_ids,
        command_types=tuple(candidate.type for candidate in response.command_candidates),
        fallback=fallback,
        fallback_reason=("not_observed" if fixture.expect.fallback_reason is not None else None),
        memory_persisted=memory_persisted,
        db_side_effect=side_effect,
    )


def _assert_semantic_contract(
    fixture: CompanionAIFixture, observed: ObservedEvaluation
) -> None:
    expected: dict[str, object] = {
        "top_intent": fixture.expect.top_intent,
        "query_mode": fixture.expect.query_mode,
        "fact_ids": tuple(fixture.expect.allowed_fact_ids),
        "command_types": tuple(fixture.expect.allowed_command_types),
        "fallback": fixture.expect.fallback,
        "fallback_reason": fixture.expect.fallback_reason,
        "memory_persisted": fixture.expect.memory_persisted,
        "db_side_effect": fixture.expect.db_side_effect,
    }
    mismatches: set[str] = set()
    for field, expected_value in expected.items():
        actual_value = getattr(observed, field)
        if actual_value == expected_value:
            continue
        mismatches.add(field)
        assert field in fixture.expect.known_gaps, (
            f"{fixture.fixture_id}: unexpected {field} mismatch: "
            f"expected {expected_value!r}, observed {actual_value!r}"
        )
    stale_gaps = set(fixture.expect.known_gaps) - mismatches
    assert not stale_gaps, f"{fixture.fixture_id}: stale known gaps: {sorted(stale_gaps)}"


FIXTURES = load_fixtures()


def test_fixture_catalog_is_complete_and_deterministic() -> None:
    assert len(FIXTURES) == 13
    assert [fixture.fixture_id for fixture in FIXTURES] == [
        "p0.recipe.list.001",
        "p0.recipe.detail.001",
        "p0.recipe.compare.001",
        "p0.recipe.followup.001",
        "p0.conversation.greeting.001",
        "p0.preference.share.001",
        "p0.unsupported.fact.001",
        "p0.command.boundary.001",
        "p0.provider.timeout.001",
        "p0.provider.unavailable.001",
        "p0.provider.invalid_json.001",
        "p0.provider.empty.001",
        "p0.provider.invalid_command.001",
    ]


def test_fixture_schema_rejects_missing_fields_unknown_provider_and_gap() -> None:
    payload = FIXTURES[0].model_dump(mode="json")
    del payload["request"]["text"]
    with pytest.raises(ValueError):
        CompanionAIFixture.model_validate(payload)

    payload = FIXTURES[0].model_dump(mode="json")
    payload["script"]["provider"] = "unknown-provider"
    with pytest.raises(ValueError):
        CompanionAIFixture.model_validate(payload)

    payload = FIXTURES[0].model_dump(mode="json")
    payload["expect"]["known_gaps"]["unknown_gap"] = "CAI-P9"
    with pytest.raises(ValueError):
        CompanionAIFixture.model_validate(payload)


def test_fixture_loader_rejects_duplicate_ids(tmp_path: Path) -> None:
    payload = FIXTURES[0].model_dump(mode="json")
    serialized = json.dumps(payload, ensure_ascii=False)
    (tmp_path / "a.json").write_text(serialized, encoding="utf-8")
    (tmp_path / "b.json").write_text(serialized, encoding="utf-8")

    with pytest.raises(ValueError, match="duplicate fixture_id"):
        load_fixtures(tmp_path)


@pytest.mark.parametrize("fixture", FIXTURES, ids=lambda fixture: fixture.fixture_id)
async def test_companion_ai_semantic_baseline(
    fixture: CompanionAIFixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    database: Database = await make_database(make_settings())
    identity: AuthenticatedDevice
    identity, _token = await make_authenticated_device(
        database, PROTECTOR, profile_id="fixture-profile"
    )
    bundle = _build_provider(fixture, monkeypatch)
    service = CompanionService(
        CompanionBrain(bundle.provider),
        metadata=METADATA,
        ai_timeout_seconds=5.0,
    )
    logger = logging.getLogger("aire.backend")
    previous_level = logger.level
    handler = _ProvenanceHandler()
    logger.setLevel(logging.INFO)
    logger.addHandler(handler)

    try:
        async with database.session_factory() as session:
            await SaveSlotRepository(session).get_or_create(
                profile_id=identity.profile_id, save_slot_id="fixture-slot-001"
            )
            await session.commit()
            before = await _business_counts(session)

            for index, prior_text in enumerate(fixture.request.prior_turns):
                await service.create_response(
                    _request(fixture, prior_text, turn_index=index),
                    identity,
                    session,
                    PROTECTOR,
                )
            response = await service.create_response(
                _request(
                    fixture,
                    fixture.request.text,
                    turn_index=len(fixture.request.prior_turns),
                ),
                identity,
                session,
                PROTECTOR,
            )
            await session.flush()
            after = await _business_counts(session)
    finally:
        await service.aclose()
        logger.removeHandler(handler)
        logger.setLevel(previous_level)

    assert response.display_text.strip(), f"{fixture.fixture_id}: empty display text escaped"
    bundle.recorder.assert_consumed()
    assert all(record.fixture_id == fixture.fixture_id for record in bundle.recorder.calls)
    assert fixture.request.text not in repr(bundle.recorder.calls)

    if isinstance(bundle.provider, InvalidLLMProvider):
        assert bundle.provider.failures
        assert bundle.provider.failures[-1].reason == fixture.expect.fallback_reason

    request_id = f"fixture-request-{fixture.fixture_id}-{len(fixture.request.prior_turns)}"
    provenance_records = [
        record for record in handler.records if record.request_id == request_id
    ]
    assert len(provenance_records) == 1
    observed = _observe(
        fixture,
        response,
        before,
        after,
        bundle,
        provenance_records[0],
    )
    _assert_semantic_contract(fixture, observed)
