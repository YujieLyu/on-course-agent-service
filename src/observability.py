"""HTTP request correlation without recording request bodies or raw URLs."""

import re
from uuid import uuid4

from starlette.datastructures import Headers, MutableHeaders
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from src.logger import get_log_context, get_logger, log_context, log_operation

logger = get_logger(__name__)


class RequestLoggingMiddleware:
    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        supplied_id = Headers(scope=scope).get("x-request-id", "")
        request_id = (
            supplied_id if re.fullmatch(r"[A-Za-z0-9_.-]{1,128}", supplied_id)
            else uuid4().hex
        )
        with log_context(
            request_id=request_id, transport=get_log_context().get("transport", "http"),
        ):
            with log_operation(logger, "http.request", method=scope["method"]) as event:
                async def send_response(message: Message) -> None:
                    if message["type"] == "http.response.start":
                        event["status_code"] = message["status"]
                        MutableHeaders(scope=message)["X-Request-ID"] = request_id
                    await send(message)

                try:
                    await self.app(scope, receive, send_response)
                finally:
                    # The matched route is populated during routing; raw paths
                    # and query strings may contain private caller input.
                    event["route"] = getattr(scope.get("route"), "path", "<unmatched>")
