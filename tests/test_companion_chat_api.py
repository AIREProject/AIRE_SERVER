"""HTTP 경로(POST /api/v1/chat → ChatService → CompanionAIService) end-to-end 검증.

LLM_PROVIDER=mock 으로 마코 두뇌가 한국어 대사와 명령 후보를 만드는지, Backend
재검증을 그대로 통과하는지 본다. 신원은 이제 인증된 디바이스 토큰이 준다
(`docs/temporary-scaffolds.md` §2) — `tests.conftest.make_authenticated_device` 로
실제 페어링 플로우를 매 테스트 거치지 않고 DB 에 디바이스 행을 직접 만든다.
"""

from typing import Any

import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr

from app.brain.dialogue import SURFACE_PROFILES
from app.credentials import CredentialProtector
from app.main import create_app
from app.models import Surface
from tests.conftest import make_authenticated_device, make_database, make_settings

PROTECTOR = CredentialProtector(SecretStr("test-only-pepper-not-for-production"))


def _empty_game_context() -> dict[str, Any]:
    return {
        "schema_version": 1,
        "location_id": None,
        "threat": {"present": False, "count": 0, "nearest_kind": None},
        "nearby_resources": [],
        "available_workstations": [],
        "current_work": None,
        "inventories": [],
    }


@pytest.fixture
async def authed_client() -> Any:
    settings = make_settings(llm_provider="mock")
    database = await make_database(settings)
    identity, token = await make_authenticated_device(database, PROTECTOR)
    with TestClient(create_app(settings)) as client:
        yield client, token, identity.profile_id


def _headers(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def _body(user_message: str, **overrides: Any) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "request_id": "req-companion-1",
        "session_id": "session-1",
        "save_slot_id": "slot-1",
        "companion_id": "mako",
        "user_message": user_message,
        "game_context": _empty_game_context(),
    }
    payload.update(overrides)
    if payload.get("surface") == "mobile" and "game_context" not in overrides:
        payload.pop("game_context")
    return payload


async def test_wait_message_yields_dialogue_and_hold_position(authed_client: Any) -> None:
    client, token, _profile_id = authed_client

    response = client.post(
        "/api/v1/chat",
        headers=_headers(token),
        json=_body("여기서 기다려", allowed_commands=["Command.HoldPosition"]),
    )

    assert response.status_code == 200
    payload = response.json()
    # 마코의 결정론적 대기 대사(mock 공급자 폴백).
    assert payload["display_text"] == "알겠어. 여기서 기다릴게."
    assert len(payload["command_candidates"]) == 1
    assert payload["command_candidates"][0]["type"] == "Command.HoldPosition"
    assert payload["ai_metadata"]["provider"] == "mock"
    assert payload["session_id"] == "session-1"
    assert payload["save_slot_id"] == "slot-1"
    assert payload["companion_id"] == "mako"


async def test_attack_message_yields_dialogue_and_target(authed_client: Any) -> None:
    client, token, _profile_id = authed_client

    response = client.post(
        "/api/v1/chat",
        headers=_headers(token),
        json=_body("참호병 공격해", allowed_commands=["Command.Attack"]),
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["display_text"] == "알겠어. 공격할게."
    assert len(payload["command_candidates"]) == 1
    candidate = payload["command_candidates"][0]
    assert candidate["type"] == "Command.Attack"
    assert candidate["parameters"] == {"target_id": "TrenchCrawler"}


async def test_game_gather_message_yields_canonical_wood_candidate(
    authed_client: Any,
) -> None:
    client, token, _profile_id = authed_client

    response = client.post(
        "/api/v1/chat",
        headers=_headers(token),
        json=_body("나무 캐줘", allowed_commands=["Command.GatherResource"]),
    )

    assert response.status_code == 200
    candidates = response.json()["command_candidates"]
    assert len(candidates) == 1
    candidate = candidates[0]
    assert candidate["type"] == "Command.GatherResource"
    assert candidate["parameters"] == {"resource": "wood"}


async def test_request_context_fields_round_trip(authed_client: Any) -> None:
    client, token, _profile_id = authed_client

    response = client.post(
        "/api/v1/chat",
        headers=_headers(token),
        json=_body(
            "안녕, 마코",
            schema_version=1,
            message_id="message-1",
            time_context={
                "source": "GameWorld",
                "day": 7,
                "hour": 23,
                "period": "Night",
            },
            recent_event_ids=["event-1"],
        ),
    )

    assert response.status_code == 200
    assert response.json()["message_id"] == "message-1"


async def test_same_request_replays_and_different_payload_conflicts(
    authed_client: Any,
) -> None:
    client, token, _profile_id = authed_client
    payload = _body("안녕, 마코", message_id="message-replay-1")

    first = client.post("/api/v1/chat", headers=_headers(token), json=payload)
    replay = client.post("/api/v1/chat", headers=_headers(token), json=payload)
    conflict = client.post(
        "/api/v1/chat",
        headers=_headers(token),
        json={**payload, "user_message": "다른 내용"},
    )

    assert first.status_code == 200
    assert replay.json() == first.json()
    assert conflict.status_code == 409
    assert conflict.json()["error"]["code"] == "DuplicateRequest"


async def test_server_generates_canonical_input_message_id(authed_client: Any) -> None:
    client, token, _profile_id = authed_client

    response = client.post(
        "/api/v1/chat", headers=_headers(token), json=_body("안녕, 마코")
    )

    assert response.status_code == 200
    assert response.json()["message_id"].startswith("message-")
    assert response.json()["response_id"].startswith("response-")


async def test_unsupported_schema_version_is_rejected(authed_client: Any) -> None:
    client, token, _profile_id = authed_client

    response = client.post(
        "/api/v1/chat",
        headers=_headers(token),
        json=_body("안녕, 마코", schema_version=2),
    )

    assert response.status_code == 400


async def test_game_chat_rejects_real_world_time_context(authed_client: Any) -> None:
    client, token, _profile_id = authed_client

    response = client.post(
        "/api/v1/chat",
        headers=_headers(token),
        json=_body(
            "안녕, 마코",
            time_context={
                "source": "RealWorld",
                "day": 1,
                "hour": 12,
                "period": "Afternoon",
            },
        ),
    )

    assert response.status_code == 400


async def test_missing_bearer_token_is_rejected(authed_client: Any) -> None:
    client, _token, _profile_id = authed_client

    response = client.post("/api/v1/chat", json=_body("안녕, 마코"))

    assert response.status_code == 401
    assert response.json()["error"]["code"] == "UnauthorizedDevice"


async def test_invalid_bearer_token_is_rejected(authed_client: Any) -> None:
    client, _token, _profile_id = authed_client

    response = client.post(
        "/api/v1/chat",
        headers=_headers("token-does-not-exist.invalid-secret"),
        json=_body("안녕, 마코"),
    )

    assert response.status_code == 401
    assert response.json()["error"]["code"] == "UnauthorizedDevice"


async def test_missing_save_slot_id_is_rejected(authed_client: Any) -> None:
    client, token, _profile_id = authed_client
    body = _body("안녕, 마코")
    del body["save_slot_id"]

    response = client.post("/api/v1/chat", headers=_headers(token), json=body)

    assert response.status_code == 400
    assert response.json()["error"]["code"] == "InvalidRequest"


async def test_unknown_companion_id_is_rejected(authed_client: Any) -> None:
    client, token, _profile_id = authed_client

    response = client.post(
        "/api/v1/chat",
        headers=_headers(token),
        json=_body("안녕, 마코", companion_id="not-mako"),
    )

    assert response.status_code == 400
    assert response.json()["error"]["code"] == "UnknownCompanion"


async def test_profile_claim_mismatch_is_rejected(authed_client: Any) -> None:
    """자기가 밝힌 profile_id 가 인증된 신원과 다르면 신원 위조 시도로 취급한다."""

    client, token, _profile_id = authed_client

    response = client.post(
        "/api/v1/chat",
        headers=_headers(token),
        json=_body("안녕", profile_id="profile-someone-else"),
    )

    assert response.status_code == 403
    assert response.json()["error"]["code"] == "IdentityScopeMismatch"


async def test_unknown_field_is_rejected(authed_client: Any) -> None:
    """계약에 없는 필드를 그대로 보내면 조용히 무시되지 않고 400 이 된다."""

    client, token, _profile_id = authed_client

    response = client.post(
        "/api/v1/chat", headers=_headers(token), json=_body("안녕", not_a_real_field="x")
    )

    assert response.status_code == 400


async def test_command_outside_allowlist_is_not_emitted(authed_client: Any) -> None:
    client, token, _profile_id = authed_client

    response = client.post(
        "/api/v1/chat",
        headers=_headers(token),
        json=_body("따라와", allowed_commands=[]),
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["display_text"]
    assert payload["command_candidates"] == []


async def test_multi_turn_ask_back_carries_across_requests(authed_client: Any) -> None:
    """되묻기 상태는 프로필+세이브슬롯+컴패니언+세션으로 파생한 키를 통해 이어진다."""

    client, token, _profile_id = authed_client
    allowed = ["Command.GatherResource"]

    asked = client.post(
        "/api/v1/chat",
        headers=_headers(token),
        json=_body("저것 좀 캐 줘", request_id="req-1", allowed_commands=allowed),
    )
    answered = client.post(
        "/api/v1/chat",
        headers=_headers(token),
        json=_body("나무", request_id="req-2", allowed_commands=allowed),
    )

    assert asked.json()["command_candidates"] == []
    candidates = answered.json()["command_candidates"]
    assert len(candidates) == 1
    assert candidates[0]["type"] == "Command.GatherResource"
    assert candidates[0]["parameters"] == {"resource": "wood"}


async def test_surface_defaults_to_the_game_companion(authed_client: Any) -> None:
    """기존 게임 클라이언트는 이 필드를 모른다. 생략이 곧 게임이어야 한다."""

    client, token, _profile_id = authed_client

    response = client.post(
        "/api/v1/chat", headers=_headers(token), json=_body("안녕, 마코")
    )

    assert response.status_code == 200
    assert response.json()["display_text"] == SURFACE_PROFILES[Surface.GAME].greeting


async def test_mobile_surface_changes_the_voice_not_the_contract(authed_client: Any) -> None:
    client, token, _profile_id = authed_client

    response = client.post(
        "/api/v1/chat",
        headers=_headers(token),
        json=_body("안녕, 마코", surface="mobile"),
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["display_text"] == SURFACE_PROFILES[Surface.MOBILE].greeting
    # 창구는 말투 축이다. 응답 모양은 게임과 완전히 같다.
    assert payload["command_candidates"] == []
    assert payload["session_id"] == "session-1"


async def test_mobile_still_gets_a_command_when_the_client_allows_one(
    authed_client: Any,
) -> None:
    """"모바일이니까 명령 없음" 을 코드에 넣으면 모바일 작업 지시가 생기는 날 되돌려야 한다."""

    client, token, _profile_id = authed_client

    response = client.post(
        "/api/v1/chat",
        headers=_headers(token),
        json=_body(
            "여기서 기다려",
            surface="mobile",
            allowed_commands=["Command.HoldPosition"],
        ),
    )

    assert response.status_code == 200
    assert response.json()["command_candidates"][0]["type"] == "Command.HoldPosition"


async def test_unknown_surface_is_rejected(authed_client: Any) -> None:
    """모르는 창구를 조용히 게임으로 되돌리면 엉뚱한 말투가 나간 것을 아무도 모른다."""

    client, token, _profile_id = authed_client

    response = client.post(
        "/api/v1/chat", headers=_headers(token), json=_body("안녕", surface="watch")
    )

    assert response.status_code == 400
    assert response.json()["error"]["code"] == "InvalidRequest"


async def test_request_id_header_must_match_body(authed_client: Any) -> None:
    client, token, _profile_id = authed_client

    response = client.post(
        "/api/v1/chat",
        headers={**_headers(token), "X-Request-ID": "req-other"},
        json=_body("안녕"),
    )

    assert response.status_code == 400
