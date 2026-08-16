import asyncio
import logging
from types import SimpleNamespace
from typing import Any

import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr

from app.brain import CompanionBrain, CompanionTurn, SituationTurn
from app.brain.dialogue import SURFACE_PROFILES, DialogueOutput, DialogueSpec
from app.brain.llm import LocalLLMProvider, MockLLMProvider
from app.credentials import CredentialProtector
from app.main import create_app
from app.models import Surface
from tests.conftest import make_authenticated_device, make_database, make_settings

PROTECTOR = CredentialProtector(SecretStr("test-only-pepper-not-for-production"))


def _turn(text: str, *, key: str = "conversation-1") -> CompanionTurn:
    return CompanionTurn(text=text, conversation_key=key)


class _UnsafeDialogueProvider(MockLLMProvider):
    async def generate_dialogue(self, spec: DialogueSpec) -> str:
        del spec
        return "확인되지 않은 수치 999를 알려 줄게."


class _InvalidConversationProvider(MockLLMProvider):
    async def generate_dialogue(self, spec: DialogueSpec) -> str:
        del spec
        return ""


class _FakeCompletions:
    def __init__(self, classification: str | Exception, dialogue: str | Exception) -> None:
        self._classification = classification
        self._dialogue = dialogue

    async def create(self, **kwargs: Any) -> Any:
        response_format = kwargs.get("response_format", {})
        schema_name = response_format.get("json_schema", {}).get("name")
        result = self._dialogue if schema_name == "dialogue_output" else self._classification
        if isinstance(result, Exception):
            raise result
        if schema_name == "dialogue_output" and isinstance(result, str) and result.strip():
            result = DialogueOutput(
                text=result,
                purpose="conversation",
                fact_references=(),
                memory_references=(),
                situation_references=(),
                accepts_command=False,
            ).model_dump_json()
        return SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content=result))]
        )


def _local_provider(
    classification: str | Exception,
    dialogue: str | Exception,
) -> LocalLLMProvider:
    provider = LocalLLMProvider(
        make_settings(llm_provider="local", local_llm_api_key="test-key")
    )
    provider._client = SimpleNamespace(  # type: ignore[assignment]
        chat=SimpleNamespace(completions=_FakeCompletions(classification, dialogue))
    )
    return provider


async def test_mock_and_repository_sources_are_distinct() -> None:
    brain = CompanionBrain(MockLLMProvider())

    dialogue = await brain.respond(_turn("안녕, 마코"))
    recipe = await brain.respond(_turn("철검 만드는 법을 알려 줘", key="conversation-2"))

    assert dialogue.provenance is not None
    assert dialogue.provenance.effective_provider == "mock"
    assert dialogue.provenance.final_response_source == "mock_fallback"
    assert dialogue.provenance.provider_calls[-1].fallback_used is False
    assert recipe.provenance is not None
    assert recipe.provenance.selected_route == "recipe"
    assert recipe.provenance.repository_match is True
    assert recipe.provenance.final_response_source == "game_repository"


@pytest.mark.parametrize(
    ("error", "reason"),
    [
        (TimeoutError(), "provider_timeout"),
        (RuntimeError("secret failure detail"), "provider_unavailable"),
    ],
)
async def test_local_call_failure_records_mock_fallback_without_error_text(
    error: Exception,
    reason: str,
) -> None:
    reply = await CompanionBrain(_local_provider(error, error)).respond(_turn("안녕, 마코"))

    assert reply.provenance is not None
    assert reply.provenance.effective_provider == "mock"
    assert reply.provenance.final_response_source == "mock_fallback"
    assert reply.provenance.final_fallback_reason == reason
    assert all(call.configured_provider == "local" for call in reply.provenance.provider_calls)
    assert all(call.fallback_used for call in reply.provenance.provider_calls)
    assert reply.text == SURFACE_PROFILES[Surface.GAME].provider_retry
    assert "secret failure detail" not in repr(reply.provenance)


async def test_recipe_repository_response_survives_provider_classification_failure() -> None:
    reply = await CompanionBrain(
        _local_provider(TimeoutError(), RuntimeError("dialogue must not be called"))
    ).respond(_turn("돌도끼 레시피 알려줘"))

    assert reply.text.startswith("돌도끼는 ")
    assert reply.provenance is not None
    assert reply.provenance.repository_match is True
    assert reply.provenance.final_response_source == "game_repository"
    assert reply.provenance.final_fallback_reason == "provider_timeout"
    assert [call.step for call in reply.provenance.provider_calls] == ["classify_top"]


@pytest.mark.parametrize(
    ("classification", "reason"),
    [
        ("not-json", "invalid_structured_output"),
        ("   ", "empty_output"),
    ],
)
async def test_route_fallback_and_final_local_dialogue_keep_both_sources(
    classification: str,
    reason: str,
) -> None:
    provider = _local_provider(classification, "반가워. 오늘도 같이 가자.")

    reply = await CompanionBrain(provider).respond(_turn("안녕, 마코"))

    assert reply.provenance is not None
    assert reply.provenance.effective_provider == "local"
    assert reply.provenance.final_response_source == "local_llm"
    assert reply.provenance.final_fallback_reason == reason
    assert reply.provenance.provider_calls[0].fallback_reason == reason
    assert reply.provenance.provider_calls[-1].succeeded is True


async def test_empty_conversation_output_uses_safe_guidance() -> None:
    provider = _local_provider('{"intent":"conversation"}', " ")

    reply = await CompanionBrain(provider).respond(_turn("안녕, 마코"))

    assert reply.text == SURFACE_PROFILES[Surface.GAME].provider_invalid
    assert reply.provenance is not None
    assert reply.provenance.final_response_source == "mock_fallback"
    assert reply.provenance.final_fallback_reason == "empty_output"


async def test_sanitizer_rejection_has_a_distinct_final_source() -> None:
    reply = await CompanionBrain(_UnsafeDialogueProvider()).respond(
        _turn("골리앗 약점이 뭐야?")
    )

    assert reply.provenance is not None
    assert reply.provenance.repository_match is True
    assert reply.provenance.fact_ids == ("Goliath",)
    assert reply.provenance.sanitizer_succeeded is False
    assert reply.provenance.final_response_source == "validation_rejection"
    assert reply.provenance.final_fallback_reason == "sanitizer_rejection"


async def test_conversation_sanitizer_rejection_uses_safe_guidance() -> None:
    reply = await CompanionBrain(_InvalidConversationProvider()).respond(_turn("안녕, 마코"))

    assert reply.text == SURFACE_PROFILES[Surface.GAME].provider_invalid
    assert reply.provenance is not None
    assert reply.provenance.final_response_source == "validation_rejection"
    assert reply.provenance.final_fallback_reason == "sanitizer_rejection"


async def test_sanitizer_rejection_is_deterministic_across_repeated_inputs() -> None:
    replies = [
        await CompanionBrain(_UnsafeDialogueProvider()).respond(
            _turn("골리앗 약점이 뭐야?", key=f"sanitizer-repeat-{index}")
        )
        for index in range(2)
    ]

    conclusions = {
        (
            reply.text,
            reply.provenance.final_response_source if reply.provenance else None,
            reply.provenance.final_fallback_reason if reply.provenance else None,
        )
        for reply in replies
    }
    assert len(conclusions) == 1
    assert next(iter(conclusions))[1:] == (
        "validation_rejection",
        "sanitizer_rejection",
    )


async def test_situation_reply_records_the_situation_route() -> None:
    reply = await CompanionBrain(MockLLMProvider()).react_with_provenance(
        SituationTurn(situation=("적이 나타났다",), conversation_key="situation-1")
    )

    assert reply.provenance is not None
    assert reply.provenance.top_intent is None
    assert reply.provenance.selected_route == "situation"
    assert reply.provenance.effective_provider == "mock"
    assert reply.provenance.final_response_source == "mock_fallback"


class _CollectingHandler(logging.Handler):
    def __init__(self) -> None:
        super().__init__()
        self.records: list[logging.LogRecord] = []

    def emit(self, record: logging.LogRecord) -> None:
        self.records.append(record)


def _context() -> dict[str, Any]:
    return {
        "schema_version": 1,
        "location_id": None,
        "threat": {"present": False, "count": 0, "nearest_kind": None},
        "nearby_resources": [],
        "available_workstations": [],
        "current_work": None,
        "inventories": [],
    }


def _chat_body(request_id: str) -> dict[str, Any]:
    return {
        "request_id": request_id,
        "session_id": request_id,
        "save_slot_id": "slot-1",
        "companion_id": "mako",
        "user_message": "안녕, 마코",
        "game_context": _context(),
    }


async def test_http_and_websocket_log_one_isolated_trace_per_valid_response() -> None:
    settings = make_settings(llm_provider="mock")
    database = await make_database(settings)
    _identity, token = await make_authenticated_device(database, PROTECTOR)
    logger = logging.getLogger("aire.backend")
    handler = _CollectingHandler()
    logger.addHandler(handler)
    try:
        with TestClient(create_app(settings)) as client:
            http = client.post(
                "/api/v1/chat",
                headers={"Authorization": f"Bearer {token}"},
                json=_chat_body("req-http"),
            )
            invalid_http = client.post(
                "/api/v1/chat",
                headers={"Authorization": f"Bearer {token}"},
                json={},
            )
            with client.websocket_connect("/api/v1/chat") as websocket:
                websocket.send_json(
                    {
                        "type": "chat",
                        "token": token,
                        "payload": _chat_body("req-ws"),
                    }
                )
                ws = websocket.receive_json()
                websocket.send_json({"type": "chat", "token": token, "payload": {}})
                invalid = websocket.receive_json()
    finally:
        logger.removeHandler(handler)

    assert http.status_code == 200
    assert invalid_http.status_code == 400
    assert "provenance" not in http.json()
    assert ws["type"] == "chat_response"
    assert "provenance" not in ws["payload"]
    assert invalid["type"] == "error"
    traces = [record for record in handler.records if record.msg == "response_provenance"]
    assert [record.request_id for record in traces] == ["req-http", "req-ws"]
    assert all(record.provider_fallback_used is False for record in traces)
    assert all(record.effective_provider == "mock" for record in traces)
    assert all(len(record.provider_calls) <= 8 for record in traces)
    assert "안녕, 마코" not in " ".join(repr(record.__dict__) for record in traces)


async def test_concurrent_brain_requests_keep_provider_calls_isolated() -> None:
    brain = CompanionBrain(MockLLMProvider())

    first, second = await asyncio.gather(
        brain.respond(_turn("안녕, 마코", key="first")),
        brain.respond(_turn("오늘 비가 올까?", key="second")),
    )

    assert first.provenance is not None
    assert second.provenance is not None
    assert first.provenance.top_intent == "conversation"
    assert second.provenance.top_intent == "conversation"
    assert len(first.provenance.provider_calls) == 2
    assert len(second.provenance.provider_calls) == 2
