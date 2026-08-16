"""Deterministic, de-identified CAI-P0-T04 baseline report."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

import pytest

from app.brain import CompanionBrain
from app.brain.contract import FallbackReason, FinalResponseSource
from app.db.save_slot_repository import SaveSlotRepository
from app.service import CompanionService
from tests.conftest import make_authenticated_device, make_database, make_settings
from tests.test_companion_ai_evaluation import (
    FIXTURES,
    METADATA,
    PROTECTOR,
    CompanionAIFixture,
    _build_provider,
    _request,
)

MetricName = Literal[
    "classification_accuracy",
    "core_response_rate",
    "irrelevant_response_rate",
    "unnecessary_refusal_rate",
    "hallucination_rate",
    "unnecessary_clarification_rate",
    "structured_output_failure_rate",
]
MetricStatus = Literal["pass", "fail", "known_gap", "not_observed"]
MetricDirection = Literal["success", "failure"]
FailureCode = Literal[
    "intent_mismatch",
    "route_mismatch",
    "required_fact_missing",
    "unexpected_fact_selected",
    "unexpected_refusal",
    "structured_output_failure",
]

NORMAL_FIXTURE_IDS = tuple(fixture.fixture_id for fixture in FIXTURES[:8])
PROVIDER_FAULT_FIXTURE_IDS = tuple(fixture.fixture_id for fixture in FIXTURES[8:])
_FALLBACK_SOURCES = frozenset({"fixed_fallback", "validation_rejection"})
_STRUCTURED_OUTPUT_REASONS = frozenset(
    {"invalid_structured_output", "empty_output", "sanitizer_rejection"}
)
_EXPECTED_ROUTE = {
    "command": "unsupported",
    "recipe": "recipe",
    "enemy": "enemy",
    "lore": "lore",
    "conversation": "conversation",
    "unknown": "unsupported",
}
_METRIC_DIRECTIONS: dict[MetricName, MetricDirection] = {
    "classification_accuracy": "success",
    "core_response_rate": "success",
    "irrelevant_response_rate": "failure",
    "unnecessary_refusal_rate": "failure",
    "hallucination_rate": "failure",
    "unnecessary_clarification_rate": "failure",
    "structured_output_failure_rate": "failure",
}


@dataclass(frozen=True, slots=True)
class ProvenanceObservation:
    top_intent: str | None
    query_mode: str | None
    selected_route: str
    repository_match: bool
    fact_ids: tuple[str, ...]
    final_response_source: FinalResponseSource
    final_fallback_reason: FallbackReason | None
    provider_fallback_reasons: tuple[FallbackReason, ...]


@dataclass(frozen=True, slots=True)
class MetricAssessment:
    metric: MetricName
    status: MetricStatus
    failure_code: FailureCode | None = None


@dataclass(frozen=True, slots=True)
class FixtureAssessment:
    fixture_id: str
    query_mode_status: MetricStatus
    observation: ProvenanceObservation
    metrics: tuple[MetricAssessment, ...]


@dataclass(frozen=True, slots=True)
class MetricSummary:
    metric: MetricName
    direction: MetricDirection
    passed: int
    failed: int
    known_gap: int
    not_observed: int
    numerator: int
    denominator: int
    rate: float | None


@dataclass(frozen=True, slots=True)
class BaselineReport:
    fixture_ids: tuple[str, ...]
    fixtures: tuple[FixtureAssessment, ...]
    summaries: tuple[MetricSummary, ...]


class _ProvenanceHandler(logging.Handler):
    def __init__(self) -> None:
        super().__init__()
        self.records: list[logging.LogRecord] = []

    def emit(self, record: logging.LogRecord) -> None:
        if record.msg == "response_provenance":
            self.records.append(record)


def _known_gap(fixture: CompanionAIFixture, field: str) -> bool:
    return field in fixture.expect.known_gaps


def _assessment(
    metric: MetricName,
    status: MetricStatus,
    failure_code: FailureCode | None = None,
) -> MetricAssessment:
    if status != "fail":
        failure_code = None
    return MetricAssessment(metric=metric, status=status, failure_code=failure_code)


def _query_mode_status(
    fixture: CompanionAIFixture, observation: ProvenanceObservation
) -> MetricStatus:
    expected = fixture.expect.query_mode
    if expected == "not_observed":
        return "not_observed"
    if observation.query_mode == expected:
        return "pass"
    if _known_gap(fixture, "query_mode"):
        return "known_gap"
    return "fail"


def _classification(
    fixture: CompanionAIFixture, observation: ProvenanceObservation
) -> MetricAssessment:
    if observation.top_intent == fixture.expect.top_intent:
        return _assessment("classification_accuracy", "pass")
    return _assessment("classification_accuracy", "fail", "intent_mismatch")


def _fact_status(
    fixture: CompanionAIFixture,
    observation: ProvenanceObservation,
) -> tuple[MetricStatus, FailureCode | None]:
    allowed = set(fixture.expect.allowed_fact_ids)
    observed = set(observation.fact_ids)
    if allowed and not observed:
        if _known_gap(fixture, "fact_ids"):
            return "known_gap", None
        return "fail", "required_fact_missing"
    if observed - allowed:
        if _known_gap(fixture, "fact_ids"):
            return "known_gap", None
        return "fail", "unexpected_fact_selected"
    return "pass", None


def _core_response(
    fixture: CompanionAIFixture, observation: ProvenanceObservation
) -> MetricAssessment:
    if fixture.fixture_id not in NORMAL_FIXTURE_IDS:
        return _assessment("core_response_rate", "not_observed")
    if observation.top_intent != fixture.expect.top_intent:
        return _assessment("core_response_rate", "fail", "intent_mismatch")
    if observation.selected_route != _EXPECTED_ROUTE[fixture.expect.top_intent]:
        return _assessment("core_response_rate", "fail", "route_mismatch")
    if not fixture.expect.fallback and observation.final_response_source in _FALLBACK_SOURCES:
        return _assessment("core_response_rate", "fail", "unexpected_refusal")
    if fixture.expect.allowed_fact_ids:
        fact_status, failure_code = _fact_status(fixture, observation)
        if fact_status != "pass":
            return _assessment("core_response_rate", fact_status, failure_code)
        if not observation.repository_match:
            return _assessment("core_response_rate", "fail", "required_fact_missing")
    return _assessment("core_response_rate", "pass")


def _irrelevant_response(
    fixture: CompanionAIFixture, observation: ProvenanceObservation
) -> MetricAssessment:
    metric: MetricName = "irrelevant_response_rate"
    if fixture.fixture_id not in NORMAL_FIXTURE_IDS or not (
        fixture.expect.allowed_fact_ids or fixture.expect.forbidden_fact_ids
    ):
        return _assessment(metric, "not_observed")
    if observation.selected_route != _EXPECTED_ROUTE[fixture.expect.top_intent]:
        return _assessment(metric, "fail", "route_mismatch")
    fact_status, failure_code = _fact_status(fixture, observation)
    return _assessment(metric, fact_status, failure_code)


def _unnecessary_refusal(
    fixture: CompanionAIFixture, observation: ProvenanceObservation
) -> MetricAssessment:
    metric: MetricName = "unnecessary_refusal_rate"
    if fixture.fixture_id not in NORMAL_FIXTURE_IDS or fixture.expect.fallback:
        return _assessment(metric, "not_observed")
    expected_route = _EXPECTED_ROUTE[fixture.expect.top_intent]
    unexpected_unsupported = (
        observation.selected_route == "unsupported" and expected_route != "unsupported"
    )
    if observation.final_response_source in _FALLBACK_SOURCES or unexpected_unsupported:
        return _assessment(metric, "fail", "unexpected_refusal")
    return _assessment(metric, "pass")


def _hallucination(
    fixture: CompanionAIFixture, observation: ProvenanceObservation
) -> MetricAssessment:
    metric: MetricName = "hallucination_rate"
    forbidden = set(fixture.expect.forbidden_fact_ids)
    if fixture.fixture_id not in NORMAL_FIXTURE_IDS or not forbidden:
        return _assessment(metric, "not_observed")
    if forbidden.intersection(observation.fact_ids):
        return _assessment(metric, "fail", "unexpected_fact_selected")
    if fixture.expect.allowed_fact_ids and not observation.fact_ids:
        if _known_gap(fixture, "fact_ids"):
            return _assessment(metric, "known_gap")
        return _assessment(metric, "fail", "required_fact_missing")
    return _assessment(metric, "pass")


def _unnecessary_clarification(fixture: CompanionAIFixture) -> MetricAssessment:
    del fixture
    return _assessment("unnecessary_clarification_rate", "not_observed")


def _structured_output_failure(
    fixture: CompanionAIFixture, observation: ProvenanceObservation
) -> MetricAssessment:
    metric: MetricName = "structured_output_failure_rate"
    if fixture.fixture_id not in PROVIDER_FAULT_FIXTURE_IDS:
        return _assessment(metric, "not_observed")
    if _STRUCTURED_OUTPUT_REASONS.intersection(observation.provider_fallback_reasons):
        return _assessment(metric, "fail", "structured_output_failure")
    if observation.final_fallback_reason in _STRUCTURED_OUTPUT_REASONS:
        return _assessment(metric, "fail", "structured_output_failure")
    if observation.final_fallback_reason == fixture.expect.fallback_reason:
        return _assessment(metric, "pass")
    if _known_gap(fixture, "fallback_reason"):
        return _assessment(metric, "known_gap")
    return _assessment(metric, "fail", "structured_output_failure")


def _assess_fixture(
    fixture: CompanionAIFixture, observation: ProvenanceObservation
) -> FixtureAssessment:
    metrics: list[MetricAssessment] = [_classification(fixture, observation)]
    if fixture.fixture_id in NORMAL_FIXTURE_IDS:
        metrics.extend(
            (
                _core_response(fixture, observation),
                _irrelevant_response(fixture, observation),
                _unnecessary_refusal(fixture, observation),
                _hallucination(fixture, observation),
                _unnecessary_clarification(fixture),
            )
        )
    else:
        metrics.append(_structured_output_failure(fixture, observation))
    return FixtureAssessment(
        fixture_id=fixture.fixture_id,
        query_mode_status=_query_mode_status(fixture, observation),
        observation=observation,
        metrics=tuple(metrics),
    )


def _summarize(
    fixtures: tuple[FixtureAssessment, ...], metric: MetricName
) -> MetricSummary:
    statuses = [
        assessment.status
        for fixture in fixtures
        for assessment in fixture.metrics
        if assessment.metric == metric
    ]
    passed = statuses.count("pass")
    failed = statuses.count("fail")
    known_gap = statuses.count("known_gap")
    not_observed = statuses.count("not_observed")
    denominator = passed + failed
    direction = _METRIC_DIRECTIONS[metric]
    numerator = passed if direction == "success" else failed
    rate = numerator / denominator if denominator else None
    return MetricSummary(
        metric=metric,
        direction=direction,
        passed=passed,
        failed=failed,
        known_gap=known_gap,
        not_observed=not_observed,
        numerator=numerator,
        denominator=denominator,
        rate=rate,
    )


def _observation(record: logging.LogRecord) -> ProvenanceObservation:
    provider_reasons = tuple(
        call["fallback_reason"]
        for call in record.provider_calls
        if call["fallback_reason"] is not None
    )
    return ProvenanceObservation(
        top_intent=record.top_intent,
        query_mode=record.query_mode,
        selected_route=record.selected_route,
        repository_match=record.repository_match,
        fact_ids=tuple(record.fact_ids),
        final_response_source=record.final_response_source,
        final_fallback_reason=record.final_fallback_reason,
        provider_fallback_reasons=provider_reasons,
    )


async def _evaluate_fixture(
    fixture: CompanionAIFixture,
    database_path: Path,
    handler: _ProvenanceHandler,
) -> FixtureAssessment:
    settings = make_settings(database_url=f"sqlite+aiosqlite:///{database_path.as_posix()}")
    database = await make_database(settings)
    identity, _token = await make_authenticated_device(
        database, PROTECTOR, profile_id=f"profile-{fixture.fixture_id}"
    )
    with pytest.MonkeyPatch.context() as monkeypatch:
        bundle = _build_provider(fixture, monkeypatch)
        service = CompanionService(
            CompanionBrain(bundle.provider),
            metadata=METADATA,
            ai_timeout_seconds=5.0,
        )
        try:
            async with database.session_factory() as session:
                await SaveSlotRepository(session).get_or_create(
                    profile_id=identity.profile_id, save_slot_id="fixture-slot-001"
                )
                await session.commit()
                for index, prior_text in enumerate(fixture.request.prior_turns):
                    await service.create_response(
                        _request(fixture, prior_text, turn_index=index),
                        identity,
                        session,
                        PROTECTOR,
                    )
                turn_index = len(fixture.request.prior_turns)
                request = _request(fixture, fixture.request.text, turn_index=turn_index)
                await service.create_response(request, identity, session, PROTECTOR)
            matching = [
                record for record in handler.records if record.request_id == request.request_id
            ]
            if len(matching) != 1:
                raise AssertionError(
                    f"{fixture.fixture_id}: expected one main-turn provenance record"
                )
            bundle.recorder.assert_consumed()
            return _assess_fixture(fixture, _observation(matching[0]))
        finally:
            await service.aclose()
            await database.dispose()


@pytest.fixture(scope="module")
async def baseline_report(tmp_path_factory: pytest.TempPathFactory) -> BaselineReport:
    directory = tmp_path_factory.mktemp("companion-ai-baseline-report")
    logger = logging.getLogger("aire.backend")
    previous_level = logger.level
    handler = _ProvenanceHandler()
    logger.setLevel(logging.INFO)
    logger.addHandler(handler)
    try:
        fixtures = tuple(
            [
                await _evaluate_fixture(
                    fixture,
                    directory / f"fixture-{index:03}.db",
                    handler,
                )
                for index, fixture in enumerate(FIXTURES)
            ]
        )
    finally:
        logger.removeHandler(handler)
        logger.setLevel(previous_level)

    metrics = tuple(_METRIC_DIRECTIONS)
    return BaselineReport(
        fixture_ids=tuple(fixture.fixture_id for fixture in fixtures),
        fixtures=fixtures,
        summaries=tuple(_summarize(fixtures, metric) for metric in metrics),
    )


@pytest.mark.parametrize("fixture_id", [fixture.fixture_id for fixture in FIXTURES])
def test_fixture_report_contains_one_deidentified_result(
    baseline_report: BaselineReport, fixture_id: str
) -> None:
    matches = [fixture for fixture in baseline_report.fixtures if fixture.fixture_id == fixture_id]
    assert len(matches) == 1
    expected_metric_count = 6 if fixture_id in NORMAL_FIXTURE_IDS else 2
    assert len(matches[0].metrics) == expected_metric_count


def test_metric_contract_rejects_unregistered_gap() -> None:
    fixture = FIXTURES[0].model_copy(deep=True)
    observation = ProvenanceObservation(
        top_intent=fixture.expect.top_intent,
        query_mode=None,
        selected_route="recipe",
        repository_match=True,
        fact_ids=(),
        final_response_source="game_repository",
        final_fallback_reason=None,
        provider_fallback_reasons=(),
    )

    result = _irrelevant_response(fixture, observation)

    assert result.status == "fail"
    assert result.failure_code == "required_fact_missing"


def test_report_summary_is_deterministic(baseline_report: BaselineReport) -> None:
    assert baseline_report.fixture_ids == tuple(fixture.fixture_id for fixture in FIXTURES)
    assert tuple(
        (
            summary.metric,
            summary.direction,
            summary.passed,
            summary.failed,
            summary.known_gap,
            summary.not_observed,
            summary.numerator,
            summary.denominator,
            summary.rate,
        )
        for summary in baseline_report.summaries
    ) == (
        ("classification_accuracy", "success", 13, 0, 0, 0, 13, 13, 1.0),
        ("core_response_rate", "success", 4, 3, 1, 0, 4, 7, 4 / 7),
        ("irrelevant_response_rate", "failure", 5, 0, 1, 2, 0, 5, 0.0),
        ("unnecessary_refusal_rate", "failure", 5, 3, 0, 0, 3, 8, 0.375),
        ("hallucination_rate", "failure", 5, 0, 0, 3, 0, 5, 0.0),
        ("unnecessary_clarification_rate", "failure", 0, 0, 0, 8, 0, 0, None),
        ("structured_output_failure_rate", "failure", 0, 2, 3, 0, 2, 2, 1.0),
    )
    failure_rows = tuple(
        (fixture.fixture_id, metric.metric, metric.failure_code)
        for fixture in baseline_report.fixtures
        for metric in fixture.metrics
        if metric.status == "fail"
    )
    assert failure_rows == (
        ("p0.conversation.greeting.001", "core_response_rate", "unexpected_refusal"),
        ("p0.conversation.greeting.001", "unnecessary_refusal_rate", "unexpected_refusal"),
        ("p0.preference.share.001", "core_response_rate", "unexpected_refusal"),
        ("p0.preference.share.001", "unnecessary_refusal_rate", "unexpected_refusal"),
        ("p0.unsupported.fact.001", "core_response_rate", "unexpected_refusal"),
        ("p0.unsupported.fact.001", "unnecessary_refusal_rate", "unexpected_refusal"),
        (
            "p0.provider.invalid_json.001",
            "structured_output_failure_rate",
            "structured_output_failure",
        ),
        (
            "p0.provider.empty.001",
            "structured_output_failure_rate",
            "structured_output_failure",
        ),
    )
    query_mode_statuses = tuple(
        fixture.query_mode_status for fixture in baseline_report.fixtures
    )
    assert query_mode_statuses.count("known_gap") == 0
    assert query_mode_statuses.count("not_observed") == 5
    assert query_mode_statuses.count("pass") == 8
    assert query_mode_statuses.count("fail") == 0


def test_report_contains_only_deidentified_metadata(baseline_report: BaselineReport) -> None:
    serialized = repr(baseline_report)
    forbidden = [
        fixture.request.text
        for fixture in FIXTURES
    ] + [
        text
        for fixture in FIXTURES
        for text in fixture.request.prior_turns
    ] + [
        response.result
        for fixture in FIXTURES
        for response in fixture.script.responses
        if response.method == "generate_dialogue" and isinstance(response.result, str)
    ]
    if any(value and value in serialized for value in forbidden):
        raise AssertionError("baseline report contains forbidden conversation content")
    assert "fixture-only-pepper-not-for-production" not in serialized
    assert "not-required" not in serialized
    assert "synthetic local adapter failure" not in serialized
