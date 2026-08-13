"""AX-I06 CraftItem allowlist and recipe-question boundaries."""

from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr, ValidationError

from app.brain import CompanionTurn
from app.brain.enemies import EnemyRepository
from app.brain.graph import build_companion_graph
from app.brain.llm import MockLLMProvider
from app.brain.lore import LoreRepository
from app.brain.recipes import RecipeRepository
from app.brain.resources import ResourceRepository
from app.credentials import CredentialProtector
from app.main import create_app
from app.models import CommandCandidate, CommandType
from tests.conftest import make_authenticated_device, make_database, make_settings

PROTECTOR = CredentialProtector(SecretStr("test-only-pepper-not-for-production"))


def _graph():
    return build_companion_graph(
        MockLLMProvider(),
        RecipeRepository(),
        LoreRepository(),
        ResourceRepository(),
        EnemyRepository(),
    )


@pytest.mark.parametrize("text", ["철검 만들어줘", "Sword_Iron craft", "IronSword 제작해줘"])
async def test_explicit_allowlisted_iron_sword_request_emits_fixed_candidate(text: str) -> None:
    turn = CompanionTurn(
        text=text,
        conversation_key="craft-test",
        allowed_actions=frozenset({CommandType.CRAFT_ITEM}),
    )

    result = await _graph().ainvoke({"turn": turn, "text": text})

    action = result["action"]
    assert action is not None
    assert action.type is CommandType.CRAFT_ITEM
    assert action.parameters == {"recipe_id": "recipe-11", "quantity": 1}


@pytest.mark.parametrize(
    "text",
    [
        "철검 만드는 법 알려줘",
        "강철 대검 만들어줘",
        "철검 2개 만들어줘",
        "Sword_Iron craft quantity=2",
        "Sword_Iron recipe-12 craft",
        "철검 어떻게 만들어?",
    ],
)
async def test_recipe_questions_and_unsupported_or_malformed_requests_emit_no_action(
    text: str,
) -> None:
    turn = CompanionTurn(
        text=text,
        conversation_key="craft-test",
        allowed_actions=frozenset({CommandType.CRAFT_ITEM}),
    )

    result = await _graph().ainvoke({"turn": turn, "text": text})

    assert result.get("action") is None
    assert result["display_text"]


async def test_valid_craft_request_without_allowlist_is_refused() -> None:
    text = "철검 만들어줘"
    turn = CompanionTurn(text=text, conversation_key="craft-test", allowed_actions=frozenset())

    result = await _graph().ainvoke({"turn": turn, "text": text})

    assert result.get("action") is None


@pytest.mark.parametrize(
    "parameters",
    [
        {},
        {"recipe_id": "recipe-11"},
        {"recipe_id": "recipe-12", "quantity": 1},
        {"recipe_id": "recipe-11", "quantity": 2},
        {"recipe_id": "recipe-11", "quantity": True},
        {"recipe_id": "recipe-11", "quantity": 1, "extra": True},
    ],
)
def test_craft_candidate_rejects_noncanonical_parameters(
    parameters: dict[str, Any],
) -> None:
    issued_at = datetime.now(UTC)
    with pytest.raises(ValidationError):
        CommandCandidate(
            command_id="command-craft",
            request_id="request-craft",
            type=CommandType.CRAFT_ITEM,
            issued_at=issued_at,
            expires_at=issued_at + timedelta(seconds=30),
            parameters=parameters,
        )


def _game_context() -> dict[str, Any]:
    return {
        "schema_version": 1,
        "location_id": "forest_camp",
        "threat": {"present": False, "count": 0, "nearest_kind": None},
        "nearby_resources": [],
        "available_workstations": ["Workbench.Blacksmith"],
        "current_work": None,
        "inventories": [],
    }


def _chat_body() -> dict[str, Any]:
    return {
        "request_id": "request-craft",
        "session_id": "session-craft",
        "save_slot_id": "slot-1",
        "companion_id": "mako",
        "user_message": "철검 만들어줘",
        "game_context": _game_context(),
        "allowed_commands": ["Command.CraftItem"],
    }


@pytest.fixture
async def authed_app() -> Any:
    settings = make_settings(llm_provider="mock")
    database = await make_database(settings)
    _identity, token = await make_authenticated_device(database, PROTECTOR)
    return create_app(settings), token


async def test_http_and_websocket_emit_the_same_craft_contract(authed_app: Any) -> None:
    app, token = authed_app
    body = _chat_body()
    with TestClient(app) as client:
        http_response = client.post(
            "/api/v1/chat",
            headers={"Authorization": f"Bearer {token}"},
            json=body,
        )
        with client.websocket_connect("/api/v1/chat") as websocket:
            websocket.send_json({"type": "chat", "token": token, "payload": body})
            websocket_response = websocket.receive_json()

    assert http_response.status_code == 200
    http_candidate = http_response.json()["command_candidates"][0]
    websocket_candidate = websocket_response["payload"]["command_candidates"][0]
    for candidate in (http_candidate, websocket_candidate):
        assert candidate["type"] == "Command.CraftItem"
        assert candidate["target_id"] is None
        assert candidate["parameters"] == {"recipe_id": "recipe-11", "quantity": 1}


def test_openapi_exposes_craft_item_enum() -> None:
    with TestClient(create_app(make_settings(llm_provider="mock"))) as client:
        schemas = client.get("/openapi.json").json()["components"]["schemas"]

    assert "Command.CraftItem" in schemas["CommandType"]["enum"]
