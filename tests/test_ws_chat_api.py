"""WebSocket 채팅 경로 검증.

`/api/v1/chat` WebSocket 이 HTTP `POST /api/v1/chat` 과 같은 페이로드를 받고 같은 응답을
내는지, 인증(봉투의 `token`)이 매 프레임 검증되는지, 그리고 어떤 실패도 연결을 끊지
않는지 확인한다.
"""

from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import SecretStr

from app.credentials import CredentialProtector
from app.errors import AIServiceUnavailableError
from app.main import create_app
from tests.conftest import make_authenticated_device, make_database, make_settings

WS_PATH = "/api/v1/chat"
PROTECTOR = CredentialProtector(SecretStr("test-only-pepper-not-for-production"))


def empty_game_context() -> dict[str, Any]:
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
async def authed_app() -> Any:
    settings = make_settings(llm_provider="mock")
    database = await make_database(settings)
    _identity, token = await make_authenticated_device(database, PROTECTOR)
    return create_app(settings), token


def body(user_message: str, **overrides: Any) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "request_id": "req-ws-1",
        "session_id": "session-ws-1",
        "save_slot_id": "slot-1",
        "companion_id": "mako",
        "user_message": user_message,
        "game_context": empty_game_context(),
    }
    payload.update(overrides)
    return payload


def chat_frame(payload: dict[str, Any], *, token: str | None) -> dict[str, Any]:
    frame: dict[str, Any] = {"type": "chat", "payload": payload}
    if token is not None:
        frame["token"] = token
    return frame


def situation_body(situation: list[str], **overrides: Any) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "request_id": "req-ws-sit-1",
        "session_id": "session-ws-1",
        "save_slot_id": "slot-1",
        "companion_id": "mako",
        "situation": situation,
    }
    payload.update(overrides)
    return payload


def situation_frame(payload: dict[str, Any], *, token: str | None) -> dict[str, Any]:
    frame: dict[str, Any] = {"type": "situation", "payload": payload}
    if token is not None:
        frame["token"] = token
    return frame


# --- 동등성: WS 응답이 HTTP 응답과 같은가 -------------------------------------


async def test_websocket_response_matches_http_response(authed_app: Any) -> None:
    app, token = authed_app
    payload = body("여기서 기다려", allowed_commands=["Command.HoldPosition"])

    with TestClient(app) as client:
        http_response = client.post(
            WS_PATH, headers={"Authorization": f"Bearer {token}"}, json=payload
        ).json()
        with client.websocket_connect(WS_PATH) as websocket:
            websocket.send_json(chat_frame(payload, token=token))
            ws_message = websocket.receive_json()

    assert ws_message["type"] == "chat_response"
    ws_payload = ws_message["payload"]
    # response_id 는 매번 새로 만들어지므로 비교에서 제외한다.
    for field in ("request_id", "session_id", "display_text", "ai_metadata"):
        assert ws_payload[field] == http_response[field]
    assert len(ws_payload["command_candidates"]) == 1
    assert ws_payload["command_candidates"][0]["type"] == "Command.HoldPosition"


async def test_websocket_game_gather_yields_canonical_wood_candidate(
    authed_app: Any,
) -> None:
    app, token = authed_app
    payload = body("나무 캐줘", allowed_commands=["Command.GatherResource"])

    with TestClient(app) as client, client.websocket_connect(WS_PATH) as websocket:
        websocket.send_json(chat_frame(payload, token=token))
        message = websocket.receive_json()

    candidates = message["payload"]["command_candidates"]
    assert len(candidates) == 1
    candidate = candidates[0]
    assert candidate["type"] == "Command.GatherResource"
    assert candidate["parameters"] == {"resource": "wood"}


# --- 정상 흐름 ----------------------------------------------------------------


async def test_multiple_messages_over_single_connection(authed_app: Any) -> None:
    app, token = authed_app

    with TestClient(app) as client, client.websocket_connect(WS_PATH) as websocket:
        websocket.send_json(chat_frame(body("안녕, 마코", request_id="req-1"), token=token))
        first = websocket.receive_json()
        websocket.send_json(chat_frame(body("고마워", request_id="req-2"), token=token))
        second = websocket.receive_json()

    assert first["payload"]["request_id"] == "req-1"
    assert second["payload"]["request_id"] == "req-2"
    assert first["type"] == second["type"] == "chat_response"


async def test_ask_back_carries_across_frames(authed_app: Any) -> None:
    """한 연결 안에서 되묻기 상태가 이어진다(키는 프로필+세이브슬롯+컴패니언+세션 파생)."""

    app, token = authed_app
    allowed = ["Command.GatherResource"]

    with TestClient(app) as client, client.websocket_connect(WS_PATH) as websocket:
        websocket.send_json(
            chat_frame(
                body("저것 좀 캐 줘", request_id="req-1", allowed_commands=allowed), token=token
            )
        )
        asked = websocket.receive_json()
        websocket.send_json(
            chat_frame(body("나무", request_id="req-2", allowed_commands=allowed), token=token)
        )
        answered = websocket.receive_json()

    assert asked["payload"]["command_candidates"] == []
    assert answered["payload"]["command_candidates"][0]["type"] == "Command.GatherResource"


# --- 인증 ----------------------------------------------------------------------


async def test_missing_token_reports_unauthorized_and_keeps_connection(authed_app: Any) -> None:
    app, token = authed_app

    with TestClient(app) as client, client.websocket_connect(WS_PATH) as websocket:
        websocket.send_json(chat_frame(body("안녕, 마코"), token=None))
        error = websocket.receive_json()
        # 연결이 살아 있어야 다음 정상 메시지가 처리된다.
        websocket.send_json(chat_frame(body("안녕, 마코"), token=token))
        recovered = websocket.receive_json()

    assert error["type"] == "error"
    assert error["payload"]["error"]["code"] == "UnauthorizedDevice"
    assert recovered["type"] == "chat_response"


async def test_invalid_token_reports_unauthorized(authed_app: Any) -> None:
    app, _token = authed_app

    with TestClient(app) as client, client.websocket_connect(WS_PATH) as websocket:
        websocket.send_json(
            chat_frame(body("안녕, 마코"), token="token-does-not-exist.invalid-secret")
        )
        error = websocket.receive_json()

    assert error["type"] == "error"
    assert error["payload"]["error"]["code"] == "UnauthorizedDevice"


# --- 실패는 연결을 끊지 않는다 -------------------------------------------------


@pytest.mark.parametrize(
    "frame",
    [
        {"type": "unknown", "payload": {}},
        {"payload": {}},
        ["not", "an", "object"],
        {"type": "chat", "payload": {"request_id": "req-x"}},
        {"type": "chat", "payload": {"request_id": "req-x", "session_id": "  "}},
    ],
)
async def test_malformed_frames_report_invalid_request_and_keep_connection(
    frame: Any, authed_app: Any
) -> None:
    app, token = authed_app

    with TestClient(app) as client, client.websocket_connect(WS_PATH) as websocket:
        websocket.send_json(frame)
        error = websocket.receive_json()
        # 연결이 살아 있어야 다음 정상 메시지가 처리된다.
        websocket.send_json(chat_frame(body("안녕, 마코"), token=token))
        recovered = websocket.receive_json()

    assert error["type"] == "error"
    assert error["payload"]["error"]["code"] == "InvalidRequest"
    assert recovered["type"] == "chat_response"


async def test_non_json_frame_reports_invalid_request(authed_app: Any) -> None:
    app, _token = authed_app

    with TestClient(app) as client, client.websocket_connect(WS_PATH) as websocket:
        websocket.send_text("not json at all")
        error = websocket.receive_json()

    assert error["type"] == "error"
    assert error["payload"]["error"]["code"] == "InvalidRequest"


async def test_oversized_message_is_rejected_and_connection_survives() -> None:
    settings = make_settings(llm_provider="mock", max_request_body_bytes=64)
    database = await make_database(settings)
    _identity, token = await make_authenticated_device(database, PROTECTOR)
    app = create_app(settings)

    with TestClient(app) as client, client.websocket_connect(WS_PATH) as websocket:
        websocket.send_json(chat_frame(body("가" * 500), token=token))
        error = websocket.receive_json()

    assert error["type"] == "error"
    assert error["payload"]["error"]["code"] == "RequestTooLarge"
    assert error["payload"]["error"]["retryable"] is False


async def test_ai_failure_reports_error_and_keeps_connection(authed_app: Any) -> None:
    class BrokenCompanion:
        async def create_response(
            self, request: Any, identity: Any, session: Any, protector: Any
        ) -> Any:
            raise AIServiceUnavailableError

        async def aclose(self) -> None:
            return None

    app: FastAPI
    app, token = authed_app
    app.state.companion = BrokenCompanion()

    with TestClient(app) as client, client.websocket_connect(WS_PATH) as websocket:
        websocket.send_json(chat_frame(body("안녕, 마코"), token=token))
        error = websocket.receive_json()

    assert error["type"] == "error"
    assert error["payload"]["error"]["code"] == "AIServiceUnavailable"
    assert error["payload"]["error"]["retryable"] is True


# --- situation 봉투 ------------------------------------------------------------


async def test_situation_frame_yields_a_situation_response(authed_app: Any) -> None:
    app, token = authed_app

    with TestClient(app) as client, client.websocket_connect(WS_PATH) as websocket:
        websocket.send_json(
            situation_frame(situation_body(["적이 나타났다"]), token=token)
        )
        message = websocket.receive_json()

    assert message["type"] == "situation_response"
    assert message["payload"]["display_text"]
    assert "command_candidates" not in message["payload"]


async def test_unknown_message_type_is_still_rejected(authed_app: Any) -> None:
    """`chat`/`situation` 둘 다 아니면 여전히 `InvalidRequest` 다."""

    app, token = authed_app

    with TestClient(app) as client, client.websocket_connect(WS_PATH) as websocket:
        websocket.send_json({"type": "unknown", "payload": {}, "token": token})
        error = websocket.receive_json()
        websocket.send_json(situation_frame(situation_body(["적이 나타났다"]), token=token))
        recovered = websocket.receive_json()

    assert error["type"] == "error"
    assert error["payload"]["error"]["code"] == "InvalidRequest"
    assert recovered["type"] == "situation_response"


async def test_error_envelope_carries_request_id_for_correlation(authed_app: Any) -> None:
    """검증에 실패해도 클라이언트가 어느 요청인지 알 수 있어야 한다."""

    app, token = authed_app

    with TestClient(app) as client, client.websocket_connect(WS_PATH) as websocket:
        websocket.send_json(
            chat_frame({"request_id": "req-broken"}, token=token)
        )
        error = websocket.receive_json()

    assert error["payload"]["request_id"] == "req-broken"
