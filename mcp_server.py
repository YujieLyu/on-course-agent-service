"""Typed MCP adapter for the On Course Agent FastAPI application."""

import asyncio
import logging
import math
import os
from typing import Annotated, TypeVar
from uuid import uuid4

from fastapi import FastAPI
from fastmcp import FastMCP
from fastmcp.exceptions import ToolError
from fastmcp.exceptions import ValidationError as McpValidationError
from fastmcp.server.middleware import Middleware, MiddlewareContext, CallNext
from fastmcp.tools import ToolResult
import httpx
from mcp.types import CallToolRequestParams
from pydantic import BaseModel, Field, ValidationError

from main import app
from src.logger import get_log_context, get_logger, log_context, log_operation
from src.models import (
    CreateReplySessionRequest,
    ReplySessionResponse,
    ReviewReplySessionRequest,
)

logger = get_logger(__name__)
ResponseT = TypeVar("ResponseT", bound=BaseModel)
NonEmptyString = Annotated[str, Field(min_length=1)]
ConversationId = Annotated[str, Field(
    min_length=1,
    pattern=r"^[A-Za-z0-9_-]+$",
    description="The conversation_id returned by create_reply_session; pass it unchanged.",
)]


class SafeFrameworkLogFilter(logging.Filter):
    """Skip tool logs covered by middleware; sanitize other framework events."""

    def filter(self, record: logging.LogRecord) -> bool:
        if get_log_context().get("transport") == "mcp":
            return False
        # FastMCP validation warnings can include raw argument values.
        record.msg = (
            "mcp.framework.error"
            if record.levelno >= logging.ERROR else "mcp.framework.event"
        )
        record.args = ()
        return True


class ToolExecutionMiddleware(Middleware):
    def __init__(self, timeout_seconds: float) -> None:
        self.timeout_seconds = timeout_seconds

    async def on_call_tool(
        self,
        context: MiddlewareContext[CallToolRequestParams],
        call_next: CallNext[CallToolRequestParams, ToolResult],
    ) -> ToolResult:
        with log_context(request_id=uuid4().hex, transport="mcp"):
            # Do not put caller-provided arguments or unknown tool names in logs.
            name = context.message.name
            known_names = {
                "create_reply_session", "get_reply_session",
                "generate_session_reply", "request_reply_revision", "accept_reply_session",
            }
            with log_operation(
                logger, "mcp.tool", tool=name if name in known_names else "<unknown>",
                expected_errors=(McpValidationError,),
            ) as event:
                deadline = asyncio.timeout(self.timeout_seconds)
                try:
                    async with deadline:
                        return await call_next(context)
                except TimeoutError:
                    if not deadline.expired():
                        raise
                    event["outcome"] = "timed_out"
                    event["timeout_seconds"] = self.timeout_seconds
                    raise ToolError(
                        f"On Course tool exceeded its {self.timeout_seconds:g}s deadline. "
                        "Check session status before retrying a write. "
                        f"Request ID: {get_log_context()['request_id']}"
                    ) from None
                except ToolError as error:
                    if error.log_level < logging.ERROR:
                        event["outcome"] = "rejected"
                    raise


class OnCourseApi:
    """Serialize API models and turn HTTP failures into safe MCP tool errors."""

    def __init__(self, api_app: FastAPI) -> None:
        self.transport = httpx.ASGITransport(app=api_app)

    async def call(
        self,
        method: str,
        path: str,
        response_type: type[ResponseT],
        payload: BaseModel | None = None,
    ) -> ResponseT:
        request_id = get_log_context().get("request_id", uuid4().hex)
        try:
            async with httpx.AsyncClient(
                transport=self.transport,
                base_url="http://on-course.local",
                # Execution is bounded by ToolExecutionMiddleware, not HTTPX.
                timeout=None,
                headers={"X-Request-ID": request_id},
            ) as client:
                response = await client.request(
                    method, path,
                    **({"json": payload.model_dump(mode="json")} if payload is not None else {}),
                )
        except Exception:
            logger.exception("mcp.api.failed")
            raise ToolError(f"On Course API request failed. Request ID: {request_id}") from None

        if not response.is_success:
            # Error bodies can contain submitted data (especially HTTP 422).
            details = {
                404: "Reply session not found",
                409: "Reply session is not ready for this action",
                422: "Request does not match the On Course API schema",
                503: "The service could not complete the request",
            }
            level = logging.WARNING if response.status_code < 500 else logging.ERROR
            raise ToolError(
                f"On Course API returned HTTP {response.status_code}: "
                f"{details.get(response.status_code, 'Request failed')}. Request ID: {request_id}",
                log_level=level,
            )

        try:
            return response_type.model_validate_json(response.content)
        except ValidationError:
            logger.exception("mcp.api.invalid_response")
            raise ToolError(
                f"On Course API returned an invalid response. Request ID: {request_id}"
            ) from None


SERVER_INSTRUCTIONS = (
    "Answer every student question through a revisable reply session. "
    "Create a session and generate its first reply. Each request_reply_revision call "
    "generates and saves a revised reply directly; do not call generate_session_reply after review. "
    "Repeat review as needed, or accept the final reply after "
    "explicit teacher approval. Acceptance persists historical course Q&A. "
    "Use known course/user IDs and preserve returned conversation IDs. "
    "After a timeout, get the session status before retrying a write. "
    "If status is accepting, retry accept_reply_session to finish the saved decision; "
    "accepted sessions can be accepted again safely."
)


def create_mcp_server(api_app: FastAPI = app, *, timeout_seconds: float | None = None) -> FastMCP:
    """Create an MCP server backed by the in-process FastAPI application."""
    if timeout_seconds is None:
        timeout_seconds = float(os.getenv("MCP_TOOL_TIMEOUT_SECONDS", "120"))
    if not math.isfinite(timeout_seconds) or timeout_seconds <= 0:
        raise ValueError("MCP_TOOL_TIMEOUT_SECONDS must be a finite positive number")

    framework_logger = get_logger("fastmcp.server.server")
    if not any(isinstance(item, SafeFrameworkLogFilter) for item in framework_logger.filters):
        framework_logger.addFilter(SafeFrameworkLogFilter())

    mcp = FastMCP(
        name="On Course Agent", instructions=SERVER_INSTRUCTIONS,
        mask_error_details=True,
        middleware=[ToolExecutionMiddleware(timeout_seconds)],
    )
    api = OnCourseApi(api_app)

    @mcp.tool(
        annotations={"readOnlyHint": False, "destructiveHint": False},
    )
    async def create_reply_session(
        question: NonEmptyString,
        course_id: NonEmptyString,
        user_id: str | None = None,
    ) -> ReplySessionResponse:
        """Create a reply session that can be generated and reviewed."""
        return await api.call(
            "POST",
            "/reply-sessions",
            ReplySessionResponse,
            CreateReplySessionRequest(question=question, course_id=course_id, user_id=user_id),
        )

    @mcp.tool(
        annotations={"readOnlyHint": True, "openWorldHint": False},
    )
    async def get_reply_session(conversation_id: ConversationId) -> ReplySessionResponse:
        """Get the current status and revisions of a reply session."""
        return await api.call("GET", f"/reply-sessions/{conversation_id}", ReplySessionResponse)

    @mcp.tool(
        annotations={"readOnlyHint": False, "destructiveHint": False},
    )
    async def generate_session_reply(conversation_id: ConversationId) -> ReplySessionResponse:
        """Search all four sources and generate the initial reply."""
        return await api.call(
            "POST",
            f"/reply-sessions/{conversation_id}/generate",
            ReplySessionResponse,
        )

    @mcp.tool(
        annotations={"readOnlyHint": False, "destructiveHint": False},
    )
    async def request_reply_revision(
        conversation_id: ConversationId,
        feedback: NonEmptyString,
    ) -> ReplySessionResponse:
        """Generate and save a revised reply using teacher feedback; no generate call follows."""
        return await api.call(
            "POST",
            f"/reply-sessions/{conversation_id}/review",
            ReplySessionResponse,
            ReviewReplySessionRequest(feedback=feedback),
        )

    @mcp.tool(
        annotations={"readOnlyHint": False, "destructiveHint": False},
    )
    async def accept_reply_session(conversation_id: ConversationId) -> ReplySessionResponse:
        """Accept a reply, resume pending acceptance, or return an already accepted reply."""
        return await api.call(
            "POST",
            f"/reply-sessions/{conversation_id}/accept",
            ReplySessionResponse,
        )

    return mcp


mcp = create_mcp_server()


if __name__ == "__main__":
    mcp.run(transport="stdio", show_banner=False)
