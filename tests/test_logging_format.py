"""구조화 로그 포매터가 실제로 내보내는 필드 검증.

`JsonFormatter._fields` 는 허용 목록이라, 여기 없는 값은 호출부가 아무리 정성껏 채워도
조용히 사라진다. WebSocket 프레임의 성패(`outcome`)가 실제로 그렇게 사라져 있었다.
"""

import json
import logging

from app.logging import JsonFormatter


def format_record(**extra: object) -> dict[str, object]:
    record = logging.LogRecord(
        name="aire.backend",
        level=logging.INFO,
        pathname=__file__,
        lineno=1,
        msg="ws_message_complete",
        args=(),
        exc_info=None,
    )
    for key, value in extra.items():
        setattr(record, key, value)
    parsed: dict[str, object] = json.loads(JsonFormatter().format(record))
    return parsed


def test_websocket_outcome_survives_the_formatter() -> None:
    """WS 에는 status_code 가 없다. outcome 이 빠지면 프레임 실패가 로그에서 사라진다."""

    payload = format_record(
        event="ws_message_complete",
        request_id="req-1",
        path="/api/v1/chat",
        outcome="error",
        duration_ms=1.5,
    )

    assert payload["outcome"] == "error"
    assert payload["event"] == "ws_message_complete"
    assert payload["request_id"] == "req-1"


def test_conversation_text_is_never_formatted() -> None:
    """허용 목록에 없는 값은 통과하지 못한다 — 대사가 로그로 새지 않는 마지막 방어선이다."""

    payload = format_record(
        event="request_complete",
        request_id="req-2",
        user_message="플레이어가 한 말",
        display_text="마코가 한 말",
    )

    assert "user_message" not in payload
    assert "display_text" not in payload
