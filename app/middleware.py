import asyncio
import json
import logging
import re
from contextvars import ContextVar
from time import perf_counter
from uuid import uuid4

from starlette.datastructures import Headers, MutableHeaders
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from app.errors import RequestBodyTooLargeError

REQUEST_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
REQUEST_ID_HEADER = "X-Request-ID"
request_id_context: ContextVar[str] = ContextVar(
    "request_id",
    default="request-unavailable",
)
logger = logging.getLogger("aire.backend")


def get_request_id() -> str:
    return request_id_context.get()


class RequestContextMiddleware:
    def __init__(
        self,
        app: ASGIApp,
        *,
        max_body_bytes: int,
        timeout_seconds: float,
    ) -> None:
        self._app = app
        self._max_body_bytes = max_body_bytes
        self._timeout_seconds = timeout_seconds

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self._app(scope, receive, send)
            return

        headers = Headers(scope=scope)
        supplied_request_id = headers.get(REQUEST_ID_HEADER)
        request_id = (
            supplied_request_id
            if supplied_request_id is not None and REQUEST_ID_PATTERN.fullmatch(supplied_request_id)
            else str(uuid4())
        )
        token = request_id_context.set(request_id)
        started_at = perf_counter()
        status_code = 500
        response_started = False
        received_bytes = 0

        async def limited_receive() -> Message:
            nonlocal received_bytes
            message = await receive()
            if message["type"] == "http.request":
                received_bytes += len(message.get("body", b""))
                if received_bytes > self._max_body_bytes:
                    raise RequestBodyTooLargeError
            return message

        async def tracked_send(message: Message) -> None:
            nonlocal response_started, status_code
            if message["type"] == "http.response.start":
                response_started = True
                status_code = message["status"]
                MutableHeaders(scope=message)[REQUEST_ID_HEADER] = get_request_id()
            await send(message)

        try:
            content_length = headers.get("content-length")
            try:
                declared_length = int(content_length) if content_length is not None else 0
            except ValueError:
                declared_length = -1
            if declared_length < 0:
                await self._send_error(
                    scope,
                    receive,
                    tracked_send,
                    status_code=400,
                    code="InvalidRequest",
                    message="Content-Length is invalid.",
                    retryable=False,
                )
                return
            if declared_length > self._max_body_bytes:
                await self._send_error(
                    scope,
                    receive,
                    tracked_send,
                    status_code=413,
                    code="RequestTooLarge",
                    message="Request body exceeds the configured size limit.",
                    retryable=False,
                )
                return

            if supplied_request_id is not None and not REQUEST_ID_PATTERN.fullmatch(
                supplied_request_id
            ):
                await self._send_error(
                    scope,
                    receive,
                    tracked_send,
                    status_code=400,
                    code="InvalidRequest",
                    message="X-Request-ID is invalid.",
                    retryable=False,
                )
                return

            try:
                async with asyncio.timeout(self._timeout_seconds):
                    await self._app(scope, limited_receive, tracked_send)
            except TimeoutError:
                if response_started:
                    raise
                await self._send_error(
                    scope,
                    receive,
                    tracked_send,
                    status_code=504,
                    code="RequestTimeout",
                    message="Request processing exceeded the configured timeout.",
                    retryable=True,
                )
        finally:
            logger.info(
                "request_complete",
                extra={
                    "event": "request_complete",
                    "request_id": get_request_id(),
                    "method": scope.get("method"),
                    "path": scope.get("path"),
                    "status_code": status_code,
                    "duration_ms": round((perf_counter() - started_at) * 1000, 3),
                },
            )
            request_id_context.reset(token)

    @staticmethod
    async def _send_error(
        scope: Scope,
        receive: Receive,
        send: Send,
        *,
        status_code: int,
        code: str,
        message: str,
        retryable: bool,
    ) -> None:
        body = json.dumps(
            {
                "request_id": get_request_id(),
                "error": {
                    "code": code,
                    "message": message,
                    "retryable": retryable,
                    "details": {},
                },
            },
            separators=(",", ":"),
        ).encode("utf-8")
        await send(
            {
                "type": "http.response.start",
                "status": status_code,
                "headers": [
                    (b"content-type", b"application/json"),
                    (b"content-length", str(len(body)).encode("ascii")),
                ],
            }
        )
        await send({"type": "http.response.body", "body": body})
