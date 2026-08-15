import json
import logging
from dataclasses import asdict
from datetime import UTC, datetime
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Any

from app.brain.contract import ResponseProvenance


class JsonFormatter(logging.Formatter):
    _fields = (
        "event",
        "request_id",
        "method",
        "path",
        "status_code",
        # WebSocket 은 상태 코드가 없다. `ws_message_complete` 의 성패는 이 필드에만 있으므로
        # 여기 없으면 프레임 실패가 로그에서 통째로 사라진다.
        "outcome",
        "duration_ms",
        "error_type",
        "step",
        "surface",
        "top_intent",
        "query_mode",
        "selected_route",
        "repository_match",
        "fact_ids",
        "configured_provider",
        "effective_provider",
        "provider_call_succeeded",
        "provider_fallback_used",
        "final_fallback_reason",
        "final_response_source",
        "model_version",
        "prompt_version",
        "sanitizer_succeeded",
        "provider_calls",
    )
    _nullable_provenance_fields = frozenset(
        {
            "top_intent",
            "query_mode",
            "effective_provider",
            "provider_call_succeeded",
            "final_fallback_reason",
            "sanitizer_succeeded",
        }
    )

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "timestamp": datetime.now(UTC).isoformat(),
            "level": record.levelname,
            "message": record.getMessage(),
        }
        for field in self._fields:
            value = getattr(record, field, None)
            if value is not None or (
                payload.get("event") == "response_provenance"
                and field in self._nullable_provenance_fields
            ):
                payload[field] = value
        return json.dumps(payload, ensure_ascii=False, separators=(",", ":"))


class _RequestEventFilter(logging.Filter):
    """요청 완료 이벤트(`request_complete`/`ws_message_complete`)만 통과시킨다.

    코딩 에이전트가 조회할 파일에는 요청 이력만 필요하다 — 브레인 스텝 타이밍 같은 나머지
    구조화 로그까지 섞으면 stdout 을 그대로 복제하는 것과 다를 게 없다.
    """

    _EVENTS = frozenset({"request_complete", "ws_message_complete", "response_provenance"})

    def filter(self, record: logging.LogRecord) -> bool:
        return getattr(record, "event", None) in self._EVENTS


def configure_logging(
    level: str,
    *,
    access_log_enabled: bool = False,
    access_log_path: Path | None = None,
    access_log_max_bytes: int = 10_485_760,
    access_log_backup_count: int = 5,
) -> None:
    logger = logging.getLogger("aire.backend")
    logger.setLevel(level)
    logger.propagate = False
    if logger.handlers:
        return

    handler = logging.StreamHandler()
    handler.setFormatter(JsonFormatter())
    logger.addHandler(handler)

    if access_log_enabled and access_log_path is not None:
        access_log_path.parent.mkdir(parents=True, exist_ok=True)
        file_handler = RotatingFileHandler(
            access_log_path,
            maxBytes=access_log_max_bytes,
            backupCount=access_log_backup_count,
            encoding="utf-8",
        )
        file_handler.setFormatter(JsonFormatter())
        file_handler.addFilter(_RequestEventFilter())
        logger.addHandler(file_handler)


def log_response_provenance(provenance: ResponseProvenance) -> None:
    """허용된 bounded metadata만 기록하며 logging 장애를 응답 경로로 전파하지 않는다."""

    try:
        logging.getLogger("aire.backend").info(
            "response_provenance",
            extra={"event": "response_provenance", **asdict(provenance)},
        )
    except Exception:
        return
