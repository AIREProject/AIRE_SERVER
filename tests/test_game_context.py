"""Context v1 validation and structured facts at the chat/brain boundary."""

from __future__ import annotations

from collections.abc import Sequence
from copy import deepcopy
from dataclasses import FrozenInstanceError
from typing import Any

import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr, ValidationError

from app.brain import CompanionBrain, CompanionReply, CompanionTurn
from app.brain.companion import PreparedCompanionReply
from app.brain.dialogue import DialogueSpec
from app.brain.llm import MockLLMProvider
from app.brain.store import ConversationTurn
from app.credentials import CredentialProtector
from app.db.connection import Database
from app.game_context_models import GameContextV1
from app.identity import AuthenticatedDevice
from app.main import create_app
from app.models import ChatRequest, Surface
from app.service import CompanionService
from tests.conftest import make_authenticated_device, make_database, make_settings


def context_payload() -> dict[str, Any]:
    """Return a complete Context v1 payload with intentionally unsorted arrays."""

    return {
        "schema_version": 1,
        "location_id": "forest_camp",
        "threat": {
            "present": True,
            "count": 2,
            "nearest_kind": "Enemy.TrenchCrawler",
        },
        "nearby_resources": [
            {"kind": "stone", "count": 2},
            {"kind": "wood", "count": 3},
        ],
        "available_workstations": ["Workbench.Advanced", "Workbench.Basic"],
        "current_work": {"type": "Harvesting", "state": "Working"},
        "inventories": [
            {
                "container_id": "AIRE.Inventory.SharedStorage",
                "free_slots": 48,
                "item_totals": [
                    {"item_id": "Stone", "count": 5},
                    {"item_id": "PlantStem", "count": 4},
                ],
                "truncated": False,
            },
            {
                "container_id": "AIRE.Inventory.MAKO",
                "free_slots": 12,
                "item_totals": [
                    {"item_id": "IronOre", "count": 2},
                    {"item_id": "PlantStem", "count": 4},
                ],
                "truncated": True,
            },
        ],
    }


def empty_context_payload() -> dict[str, Any]:
    return {
        "schema_version": 1,
        "location_id": None,
        "threat": {"present": False, "count": 0, "nearest_kind": None},
        "nearby_resources": [],
        "available_workstations": [],
        "current_work": None,
        "inventories": [],
    }


def producer_partial_context_payload() -> dict[str, Any]:
    """현재 AX-I04가 권위 센서 없이 보낼 수 있는 완전한 7-field Context다."""

    return {
        "schema_version": 1,
        "location_id": "forest_camp",
        "threat": {"present": True, "count": 2, "nearest_kind": None},
        "nearby_resources": [],
        "available_workstations": [],
        "current_work": None,
        "inventories": [],
    }


def make_chat_payload(*, surface: str = "game", game_context: Any = None) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "request_id": "request-context-1",
        "session_id": "session-context-1",
        "save_slot_id": "slot-context-1",
        "companion_id": "mako",
        "user_message": "주변 상황을 알려줘",
        "surface": surface,
    }
    if game_context is not None:
        payload["game_context"] = game_context
    return payload


def api_headers(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture
async def context_client() -> Any:
    settings = make_settings(llm_provider="mock")
    database = await make_database(settings)
    _identity, token = await make_authenticated_device(
        database, CredentialProtector(SecretStr("test-only-pepper-not-for-production"))
    )
    with TestClient(create_app(settings)) as client:
        yield client, token


def test_http_invalid_context_is_normalized_to_invalid_request(context_client: Any) -> None:
    client, token = context_client
    payload = make_chat_payload(game_context=context_payload())
    payload["game_context"]["unknown"] = True

    response = client.post("/api/v1/chat", headers=api_headers(token), json=payload)

    assert response.status_code == 400
    assert response.json()["error"]["code"] == "InvalidRequest"


def test_http_oversized_context_is_normalized_to_invalid_request(context_client: Any) -> None:
    client, token = context_client
    payload = make_chat_payload(game_context=context_payload())
    payload["game_context"]["location_id"] = "x" * 8_193

    response = client.post("/api/v1/chat", headers=api_headers(token), json=payload)

    assert response.status_code == 400
    assert response.json()["error"]["code"] == "InvalidRequest"


def test_websocket_invalid_context_is_normalized_to_invalid_request(
    context_client: Any,
) -> None:
    client, token = context_client
    payload = make_chat_payload(game_context=context_payload())
    payload["game_context"]["threat"]["count"] = 33

    with client.websocket_connect("/api/v1/chat") as websocket:
        websocket.send_json({"type": "chat", "token": token, "payload": payload})
        message = websocket.receive_json()

    assert message["type"] == "error"
    assert message["payload"]["error"]["code"] == "InvalidRequest"


@pytest.mark.parametrize(
    "payload",
    [context_payload(), empty_context_payload(), producer_partial_context_payload()],
)
def test_context_v1_accepts_full_empty_and_producer_partial_payloads(
    payload: dict[str, Any],
) -> None:
    context = GameContextV1.model_validate(payload)

    assert context.schema_version == 1


@pytest.mark.parametrize(
    "missing_field",
    [
        "schema_version",
        "location_id",
        "threat",
        "nearby_resources",
        "available_workstations",
        "current_work",
        "inventories",
    ],
)
def test_context_v1_requires_every_top_level_field(missing_field: str) -> None:
    payload = context_payload()
    del payload[missing_field]

    with pytest.raises(ValidationError):
        GameContextV1.model_validate(payload)


def test_context_v1_rejects_unknown_fields_and_credential_like_fields() -> None:
    payload = context_payload()
    payload["token"] = "must-not-reach-the-brain"
    with pytest.raises(ValidationError):
        GameContextV1.model_validate(payload)

    payload = context_payload()
    payload["threat"]["authorization"] = "must-not-reach-the-brain"
    with pytest.raises(ValidationError):
        GameContextV1.model_validate(payload)


def test_context_v1_rejects_unsupported_version() -> None:
    payload = context_payload()
    payload["schema_version"] = 2

    with pytest.raises(ValidationError):
        GameContextV1.model_validate(payload)


@pytest.mark.parametrize(
    ("path", "value"),
    [
        (("location_id",), "../secret"),
        (("threat", "nearest_kind"), "Enemy/TrenchCrawler"),
        (("nearby_resources", 0, "kind"), "wood path"),
        (("available_workstations", 0), "UObject'/Game/Workbench.Basic'"),
        (("inventories", 0, "item_totals", 0, "item_id"), "Item/PlantStem"),
        (("location_id",), []),
        (("threat", "nearest_kind"), {}),
        (("inventories", 0, "item_totals", 0, "item_id"), ["PlantStem"]),
    ],
)
def test_context_v1_rejects_invalid_stable_ids(path: tuple[str | int, ...], value: Any) -> None:
    payload = context_payload()
    target: Any = payload
    for key in path[:-1]:
        target = target[key]
    target[path[-1]] = value

    with pytest.raises(ValidationError):
        GameContextV1.model_validate(payload)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("nearby_resources", [{"kind": "wood", "count": 1}] * 2),
        ("available_workstations", ["Workbench.Basic", "Workbench.Basic"]),
        (
            "inventories",
            [
                {
                    "container_id": "AIRE.Inventory.MAKO",
                    "free_slots": 1,
                    "item_totals": [],
                    "truncated": False,
                }
            ]
            * 2,
        ),
    ],
)
def test_context_v1_rejects_duplicate_collection_entries(field: str, value: list[Any]) -> None:
    payload = context_payload()
    payload[field] = value

    with pytest.raises(ValidationError):
        GameContextV1.model_validate(payload)


def test_context_v1_rejects_duplicate_inventory_item_ids() -> None:
    payload = context_payload()
    payload["inventories"][0]["item_totals"] = [
        {"item_id": "PlantStem", "count": 1},
        {"item_id": "PlantStem", "count": 2},
    ]

    with pytest.raises(ValidationError):
        GameContextV1.model_validate(payload)


@pytest.mark.parametrize(
    "change",
    [
        lambda payload: payload["threat"].update({"present": False, "count": 2}),
        lambda payload: payload["threat"].update(
            {"present": True, "count": 0, "nearest_kind": None}
        ),
        lambda payload: payload["threat"].update(
            {"present": False, "count": 0, "nearest_kind": "Enemy.TrenchCrawler"}
        ),
    ],
)
def test_context_v1_rejects_inconsistent_threat(change: Any) -> None:
    payload = context_payload()
    change(payload)

    with pytest.raises(ValidationError):
        GameContextV1.model_validate(payload)


@pytest.mark.parametrize(
    "change",
    [
        lambda payload: payload["threat"].update({"count": 33}),
        lambda payload: payload["nearby_resources"].__setitem__(0, {"kind": "wood", "count": 0}),
        lambda payload: payload["inventories"][1].update({"free_slots": 21}),
        lambda payload: payload["inventories"][0]["item_totals"].__setitem__(
            0, {"item_id": "Stone", "count": 0}
        ),
    ],
)
def test_context_v1_rejects_out_of_range_counts(change: Any) -> None:
    payload = context_payload()
    change(payload)

    with pytest.raises(ValidationError):
        GameContextV1.model_validate(payload)


def test_context_v1_rejects_oversized_arrays() -> None:
    payload = context_payload()
    payload["nearby_resources"] = [{"kind": f"resource-{index}", "count": 1} for index in range(9)]
    with pytest.raises(ValidationError):
        GameContextV1.model_validate(payload)

    payload = context_payload()
    payload["available_workstations"] = [f"Workbench.{index}" for index in range(9)]
    with pytest.raises(ValidationError):
        GameContextV1.model_validate(payload)

    payload = context_payload()
    payload["inventories"][0]["item_totals"] = [
        {"item_id": f"Item{index}", "count": 1} for index in range(17)
    ]
    with pytest.raises(ValidationError):
        GameContextV1.model_validate(payload)


def test_context_v1_rejects_inventory_total_above_capacity() -> None:
    payload = context_payload()
    payload["inventories"][1]["item_totals"] = [
        {"item_id": f"Item{index}", "count": 124} for index in range(16)
    ]

    with pytest.raises(ValidationError):
        GameContextV1.model_validate(payload)


def test_context_v1_rejects_oversized_compact_json() -> None:
    payload = context_payload()
    payload["location_id"] = "x" * 8_193

    with pytest.raises(ValidationError, match="8 KiB"):
        GameContextV1.model_validate(payload)


def test_chat_request_requires_context_for_game_and_allows_mobile_without_it() -> None:
    with pytest.raises(ValidationError):
        ChatRequest.model_validate(make_chat_payload())

    request = ChatRequest.model_validate(make_chat_payload(game_context=context_payload()))
    assert request.surface is Surface.GAME
    assert request.game_context is not None

    mobile = ChatRequest.model_validate(make_chat_payload(surface="mobile"))
    assert mobile.surface is Surface.MOBILE
    assert mobile.game_context is None

    mobile_payload = make_chat_payload(surface="mobile")
    mobile_payload["game_context"] = None
    assert ChatRequest.model_validate(mobile_payload).game_context is None


def test_chat_request_rejects_game_context_on_mobile() -> None:
    with pytest.raises(ValidationError):
        ChatRequest.model_validate(
            make_chat_payload(surface="mobile", game_context=empty_context_payload())
        )


def test_chat_request_rejects_context_as_free_form_values() -> None:
    values: tuple[Any, ...] = ({}, "not-an-object", [], {"schema_version": 1})
    for value in values:
        with pytest.raises(ValidationError):
            ChatRequest.model_validate(make_chat_payload(game_context=value))


class RecordingProvider(MockLLMProvider):
    def __init__(self) -> None:
        self.dialogue_specs: list[DialogueSpec] = []

    async def generate_dialogue(self, spec: DialogueSpec) -> str:
        self.dialogue_specs.append(spec)
        return await super().generate_dialogue(spec)


class RecordingBrain(CompanionBrain):
    def __init__(self, provider: RecordingProvider) -> None:
        super().__init__(provider)
        self.turns: list[CompanionTurn] = []

    async def respond(self, turn: CompanionTurn) -> CompanionReply:
        self.turns.append(turn)
        return await super().respond(turn)

    async def prepare_response(
        self,
        turn: CompanionTurn,
        *,
        history: Sequence[ConversationTurn] | None = None,
    ) -> PreparedCompanionReply:
        self.turns.append(turn)
        return await super().prepare_response(turn, history=history)


def make_service(
    provider: RecordingProvider, brain: RecordingBrain | None = None
) -> CompanionService:
    from app.models import AIMetadata

    return CompanionService(
        brain or RecordingBrain(provider),
        metadata=AIMetadata(
            provider="mock", model_version="mock-v1", prompt_version="companion-v4"
        ),
        ai_timeout_seconds=5.0,
    )


def make_request(context: dict[str, Any], *, request_id: str = "request-context-1") -> ChatRequest:
    payload = make_chat_payload(game_context=context)
    payload["request_id"] = request_id
    return ChatRequest.model_validate(payload)


@pytest.fixture
async def context_database() -> Database:
    return await make_database(make_settings(llm_provider="mock"))


@pytest.fixture
async def context_identity(context_database: Database) -> AuthenticatedDevice:
    identity, _token = await make_authenticated_device(
        context_database,
        CredentialProtector(SecretStr("test-only-pepper-not-for-production")),
    )
    return identity


async def record_context_facts(
    provider: RecordingProvider,
    database: Database,
    identity: AuthenticatedDevice,
    context: dict[str, Any],
    *,
    request_id: str = "request-context-1",
) -> tuple[DialogueSpec, CompanionTurn]:
    protector = CredentialProtector(SecretStr("test-only-pepper-not-for-production"))
    brain = RecordingBrain(provider)
    service = make_service(provider, brain)
    async with database.session_factory() as session:
        await service.create_response(
            make_request(context, request_id=request_id), identity, session, protector
        )
    assert provider.dialogue_specs
    assert brain.turns
    return provider.dialogue_specs[-1], brain.turns[-1]


async def test_service_passes_all_structured_context_values_to_dialogue_facts(
    context_database: Database,
    context_identity: AuthenticatedDevice,
) -> None:
    provider = RecordingProvider()

    spec, turn = await record_context_facts(
        provider, context_database, context_identity, context_payload()
    )

    facts = "\n".join(spec.facts)
    for expected in (
        "forest_camp",
        "Enemy.TrenchCrawler",
        "stone",
        "wood",
        "Workbench.Advanced",
        "Workbench.Basic",
        "Harvesting",
        "Working",
        "AIRE.Inventory.MAKO",
        "AIRE.Inventory.SharedStorage",
        "PlantStem",
        "IronOre",
        "Stone",
    ):
        assert expected in facts
    assert any("2" in fact for fact in spec.facts)
    assert any("3" in fact for fact in spec.facts)
    assert any("12" in fact for fact in spec.facts)
    assert any("48" in fact for fact in spec.facts)
    assert turn.world_context.is_available is True
    assert turn.world_context.location_id == "forest_camp"
    assert tuple(resource.kind for resource in turn.world_context.nearby_resources) == (
        "stone",
        "wood",
    )
    assert turn.world_context.available_workstations == (
        "Workbench.Advanced",
        "Workbench.Basic",
    )
    assert tuple(inventory.container_id for inventory in turn.world_context.inventories) == (
        "AIRE.Inventory.MAKO",
        "AIRE.Inventory.SharedStorage",
    )
    assert tuple(item.item_id for item in turn.world_context.inventories[0].item_totals) == (
        "IronOre",
        "PlantStem",
    )
    with pytest.raises(FrozenInstanceError):
        turn.world_context.location_id = "mutated"  # type: ignore[misc]


async def test_service_consumes_producer_partial_context_without_inventing_ids(
    context_database: Database,
    context_identity: AuthenticatedDevice,
) -> None:
    provider = RecordingProvider()

    spec, turn = await record_context_facts(
        provider,
        context_database,
        context_identity,
        producer_partial_context_payload(),
    )

    assert turn.world_context.location_id == "forest_camp"
    assert turn.world_context.is_available is True
    assert turn.world_context.threat is not None
    assert turn.world_context.threat.present is True
    assert turn.world_context.threat.count == 2
    assert turn.world_context.threat.nearest_kind is None
    assert turn.world_context.nearby_resources == ()
    assert turn.world_context.available_workstations == ()
    assert turn.world_context.current_work is None
    assert turn.world_context.inventories == ()
    assert "현재 위치 ID는 forest_camp다" in spec.facts
    assert "주변 위협은 2개다" in spec.facts
    assert "주변에 확인된 자원이 없다" in spec.facts
    assert "사용 가능한 작업대가 없다" in spec.facts
    assert all("None" not in fact and "null" not in fact for fact in spec.facts)


async def test_context_array_order_is_normalized_before_dialogue_facts(
    context_database: Database,
    context_identity: AuthenticatedDevice,
) -> None:
    first_provider = RecordingProvider()
    second_provider = RecordingProvider()
    first = context_payload()
    second = deepcopy(first)
    second["nearby_resources"].reverse()
    second["available_workstations"].reverse()
    for inventory in second["inventories"]:
        inventory["item_totals"].reverse()
    second["inventories"].reverse()

    first_spec, _first_turn = await record_context_facts(
        first_provider, context_database, context_identity, first
    )
    second_spec, _second_turn = await record_context_facts(
        second_provider,
        context_database,
        context_identity,
        second,
        request_id="request-context-2",
    )

    assert first_spec.facts == second_spec.facts
