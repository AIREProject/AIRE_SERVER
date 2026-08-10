"""요청 완료 이벤트가 stdout 뿐 아니라 파일에도 JSONL 로 남는지 검증.

`configure_logging`은 `aire.backend` 로거에 핸들러가 이미 있으면 즉시 반환하므로(중복 부착
방지), 각 테스트는 로거를 비운 상태에서 시작해 끝나면 원래 핸들러로 되돌려 다른 테스트에
영향을 주지 않는다.
"""

import json
import logging
from collections.abc import Iterator
from pathlib import Path

import pytest

from app.logging import configure_logging

_LOGGER_NAME = "aire.backend"


@pytest.fixture
def fresh_logger() -> Iterator[logging.Logger]:
    logger = logging.getLogger(_LOGGER_NAME)
    original_handlers = list(logger.handlers)
    original_level = logger.level
    logger.handlers.clear()
    try:
        yield logger
    finally:
        logger.handlers.clear()
        logger.handlers.extend(original_handlers)
        logger.setLevel(original_level)


def test_request_complete_is_written_to_file(fresh_logger: logging.Logger, tmp_path: Path) -> None:
    log_path = tmp_path / "requests.log"
    configure_logging(
        "INFO",
        access_log_enabled=True,
        access_log_path=log_path,
        access_log_max_bytes=10_485_760,
        access_log_backup_count=1,
    )

    fresh_logger.info(
        "request_complete",
        extra={
            "event": "request_complete",
            "request_id": "req-1",
            "method": "POST",
            "path": "/api/v1/chat",
            "status_code": 200,
            "duration_ms": 12.5,
        },
    )
    fresh_logger.info(
        "ws_message_complete",
        extra={
            "event": "ws_message_complete",
            "request_id": "req-2",
            "path": "/api/v1/chat",
            "outcome": "ok",
            "duration_ms": 3.0,
        },
    )

    lines = log_path.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 2
    first, second = (json.loads(line) for line in lines)
    assert first["event"] == "request_complete"
    assert first["request_id"] == "req-1"
    assert second["event"] == "ws_message_complete"
    assert second["outcome"] == "ok"


def test_non_request_events_are_not_written(fresh_logger: logging.Logger, tmp_path: Path) -> None:
    log_path = tmp_path / "requests.log"
    configure_logging(
        "INFO",
        access_log_enabled=True,
        access_log_path=log_path,
        access_log_max_bytes=10_485_760,
        access_log_backup_count=1,
    )

    fresh_logger.info("llm_step", extra={"event": "llm_step", "step": "route"})

    # RotatingFileHandler 는 생성 시점에 파일을 열어 두므로 존재 자체가 아니라 내용을 본다.
    assert log_path.read_text(encoding="utf-8") == ""


def test_conversation_text_never_reaches_the_file(
    fresh_logger: logging.Logger, tmp_path: Path
) -> None:
    log_path = tmp_path / "requests.log"
    configure_logging(
        "INFO",
        access_log_enabled=True,
        access_log_path=log_path,
        access_log_max_bytes=10_485_760,
        access_log_backup_count=1,
    )

    fresh_logger.info(
        "request_complete",
        extra={
            "event": "request_complete",
            "request_id": "req-3",
            "user_message": "플레이어가 한 말",
            "display_text": "마코가 한 말",
        },
    )

    payload = json.loads(log_path.read_text(encoding="utf-8").splitlines()[0])
    assert "user_message" not in payload
    assert "display_text" not in payload


def test_access_log_disabled_by_default(fresh_logger: logging.Logger, tmp_path: Path) -> None:
    log_path = tmp_path / "requests.log"
    configure_logging("INFO")

    fresh_logger.info(
        "request_complete",
        extra={"event": "request_complete", "request_id": "req-4"},
    )

    assert not log_path.exists()
