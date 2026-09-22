import asyncio
import unittest
from datetime import UTC, datetime
from typing import Any
from unittest.mock import AsyncMock, patch

from fastapi import FastAPI, HTTPException
from fastmcp import Client

from mcp_server import create_mcp_server
from src.models import ReplySession
from tests.fakes import make_session_service


class McpServerTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.requests: list[tuple[str, str, dict[str, Any] | None]] = []
        api = FastAPI()
        self.api = api

        def session(conversation_id: str, status: str) -> dict[str, Any]:
            return ReplySession(
                conversation_id=conversation_id,
                course_id="course-1",
                student_query="When is the assignment due?",
                status=status,
                created_at=datetime(2026, 9, 1, tzinfo=UTC),
                expires_at=datetime(2026, 10, 1, tzinfo=UTC),
            ).model_dump(mode="json")

        @api.post("/reply-sessions")
        async def create_session(payload: dict[str, Any]):
            self.requests.append(("POST", "/reply-sessions", payload))
            return session("conv-1", "pending_generation")

        @api.get("/reply-sessions/{conversation_id}")
        async def get_session(conversation_id: str):
            self.requests.append(
                ("GET", f"/reply-sessions/{conversation_id}", None)
            )
            if conversation_id == "missing":
                raise HTTPException(status_code=404, detail="Reply session not found")
            return session(conversation_id, "awaiting_review")

        @api.post("/reply-sessions/{conversation_id}/generate")
        async def generate_session(conversation_id: str):
            path = f"/reply-sessions/{conversation_id}/generate"
            self.requests.append(("POST", path, None))
            return session(conversation_id, "awaiting_review")

        @api.post("/reply-sessions/{conversation_id}/review")
        async def review_session(conversation_id: str, payload: dict[str, Any]):
            path = f"/reply-sessions/{conversation_id}/review"
            self.requests.append(("POST", path, payload))
            return session(conversation_id, "revision_requested")

        @api.post("/reply-sessions/{conversation_id}/accept")
        async def accept_session(conversation_id: str):
            path = f"/reply-sessions/{conversation_id}/accept"
            self.requests.append(("POST", path, None))
            return session(conversation_id, "accepted")

        self.server = create_mcp_server(api)

    async def test_exposes_expected_tools_with_safety_annotations(self):
        async with Client(self.server) as client:
            tools = {tool.name: tool for tool in await client.list_tools()}

        self.assertEqual(
            set(tools),
            {
                "create_reply_session",
                "get_reply_session",
                "generate_session_reply",
                "request_reply_revision",
                "accept_reply_session",
            },
        )
        self.assertTrue(tools["get_reply_session"].annotations.readOnlyHint)
        self.assertFalse(tools["accept_reply_session"].annotations.readOnlyHint)

    async def test_maps_session_workflow_to_fastapi(self):
        async with Client(self.server) as client:
            await client.call_tool(
                "create_reply_session",
                {
                    "question": "When is the assignment due?",
                    "course_id": "course-1",
                },
            )
            await client.call_tool(
                "get_reply_session",
                {"conversation_id": "conv-1"},
            )
            await client.call_tool(
                "generate_session_reply",
                {"conversation_id": "conv-1"},
            )
            await client.call_tool(
                "request_reply_revision",
                {"conversation_id": "conv-1", "feedback": "Make it shorter."},
            )
            await client.call_tool(
                "accept_reply_session",
                {"conversation_id": "conv-1"},
            )

        self.assertEqual(
            self.requests,
            [
                (
                    "POST",
                    "/reply-sessions",
                    {
                        "question": "When is the assignment due?",
                        "course_id": "course-1",
                        "user_id": None,
                    },
                ),
                ("GET", "/reply-sessions/conv-1", None),
                ("POST", "/reply-sessions/conv-1/generate", None),
                (
                    "POST",
                    "/reply-sessions/conv-1/review",
                    {"feedback": "Make it shorter."},
                ),
                ("POST", "/reply-sessions/conv-1/accept", None),
            ],
        )

    async def test_turns_api_errors_into_tool_errors(self):
        async with Client(self.server) as client:
            with self.assertRaisesRegex(
                Exception,
                "HTTP 404: Reply session not found",
            ):
                await client.call_tool(
                    "get_reply_session",
                    {"conversation_id": "missing"},
                )

    async def test_schemas_describe_revisable_sessions(self):
        async with Client(self.server) as client:
            tools = {tool.name: tool for tool in await client.list_tools()}
        create = tools["create_reply_session"]
        self.assertEqual(create.inputSchema["properties"]["question"]["minLength"], 1)
        self.assertEqual(create.inputSchema["properties"]["course_id"]["minLength"], 1)
        self.assertEqual(set(create.inputSchema["required"]), {"question", "course_id"})
        for tool in tools.values():
            self.assertIn("conversation_id", tool.outputSchema["required"])
            self.assertIn("revisions", tool.outputSchema["properties"])
        self.assertIn("pattern", tools["generate_session_reply"].inputSchema["properties"]["conversation_id"])

    async def test_invalid_session_input_is_rejected_before_api_execution(self):
        async with Client(self.server) as client:
            for payload in [
                {"question": "", "course_id": "course-1"},
                {"question": "Question", "course_id": ""},
                {"question": "Question"},
                {"question": None, "course_id": "course-1"},
            ]:
                with self.subTest(payload=payload):
                    result = await client.call_tool("create_reply_session", payload, raise_on_error=False)
                    self.assertTrue(result.is_error)
        self.assertEqual(self.requests, [])

    async def test_direct_answer_tool_is_unavailable(self):
        async with Client(self.server) as client:
            result = await client.call_tool("answer_student_question", {
                "question": "Question", "course_id": "course-1", "user_id": "student-1",
            }, raise_on_error=False)
        self.assertTrue(result.is_error)
        self.assertEqual(self.requests, [])

    async def test_session_ids_cannot_change_any_tool_route(self):
        async with Client(self.server) as client:
            for name in ["get_reply_session", "generate_session_reply", "request_reply_revision", "accept_reply_session"]:
                for conversation_id in ["conv-1/accept?", "conv-1%2Faccept%3F", "../reply", "conv-1?x", "conv-1#x", "conv-1\\x", "conv-1\n", ""]:
                    with self.subTest(tool=name, conversation_id=conversation_id):
                        args = {"conversation_id": conversation_id}
                        if name == "request_reply_revision":
                            args["feedback"] = "Shorter"
                        result = await client.call_tool(name, args, raise_on_error=False)
                        self.assertTrue(result.is_error)
        self.assertEqual(self.requests, [])

    async def test_injection_cannot_accept_a_real_session(self):
        from main import app

        store = make_session_service()
        store.create("conv-1", "Question", "course-1")
        store.save_generation(store.load_snapshot("conv-1"), "Draft")
        with patch("main.reply_sessions", store), patch("main.save_accepted_qna", new=AsyncMock()) as persist:
            async with Client(create_mcp_server(app)) as client:
                result = await client.call_tool("generate_session_reply", {"conversation_id": "conv-1/accept?"}, raise_on_error=False)
        self.assertTrue(result.is_error)
        self.assertEqual(store.get("conv-1").status, "awaiting_review")
        persist.assert_not_awaited()

    async def test_deadline_cancels_in_process_work_and_keeps_server_usable(self):
        api = FastAPI()
        cancelled = asyncio.Event()

        @api.post("/reply-sessions/{conversation_id}/generate")
        async def slow_answer():
            try:
                await asyncio.sleep(10)
            finally:
                cancelled.set()

        @api.get("/reply-sessions/{conversation_id}")
        async def get_session(conversation_id: str):
            return ReplySession(
                conversation_id=conversation_id, course_id="c1", student_query="Question",
                created_at=datetime(2026, 9, 1, tzinfo=UTC),
                expires_at=datetime(2026, 10, 1, tzinfo=UTC),
            )

        async with Client(create_mcp_server(api, timeout_seconds=0.02)) as client:
            result = await asyncio.wait_for(client.call_tool("generate_session_reply", {"conversation_id": "conv-1"}, raise_on_error=False), timeout=1)
            self.assertTrue(result.is_error)
            self.assertIn("deadline", result.content[0].text)
            self.assertTrue(cancelled.is_set())
            healthy = await client.call_tool("get_reply_session", {"conversation_id": "conv-1"})
            self.assertFalse(healthy.is_error)

    async def test_invalid_response_is_a_safe_tool_error(self):
        api = FastAPI()

        @api.post("/reply-sessions/{conversation_id}/generate")
        async def answer():
            return {"private_body": "must-not-be-exposed"}

        async with Client(create_mcp_server(api)) as client:
            result = await client.call_tool("generate_session_reply", {"conversation_id": "conv-1"}, raise_on_error=False)
        self.assertTrue(result.is_error)
        self.assertIn("invalid response", result.content[0].text)
        self.assertNotIn("must-not-be-exposed", result.content[0].text)

    async def test_caller_cancellation_is_not_reported_as_success(self):
        api = FastAPI()
        entered = asyncio.Event()
        cancelled = asyncio.Event()

        @api.post("/reply-sessions/{conversation_id}/generate")
        async def answer():
            entered.set()
            try:
                await asyncio.sleep(10)
            finally:
                cancelled.set()

        server = create_mcp_server(api)
        with self.assertLogs("mcp_server") as captured:
            task = asyncio.create_task(server.call_tool("generate_session_reply", {"conversation_id": "conv-1"}))
            try:
                await asyncio.wait_for(entered.wait(), timeout=1)
            finally:
                task.cancel()
                with self.assertRaises(asyncio.CancelledError):
                    await task
        self.assertTrue(cancelled.is_set())
        self.assertEqual(captured.records[-1].event_fields["outcome"], "cancelled")
        self.assertFalse(any(r.getMessage() == "mcp.tool.completed" for r in captured.records))

    async def test_error_body_is_not_exposed_to_client(self):
        api = FastAPI()

        @api.post("/reply-sessions/{conversation_id}/generate")
        async def answer():
            raise HTTPException(503, detail={"secret": "must-not-be-exposed"})

        async with Client(create_mcp_server(api)) as client:
            result = await client.call_tool("generate_session_reply", {"conversation_id": "conv-1"}, raise_on_error=False)
        self.assertTrue(result.is_error)
        self.assertIn("HTTP 503", result.content[0].text)
        self.assertIn("Request ID:", result.content[0].text)
        self.assertNotIn("must-not-be-exposed", result.content[0].text)

    def test_invalid_deadline_configuration_fails_at_startup(self):
        for timeout in [0, -1, float("inf"), float("nan")]:
            with self.subTest(timeout=timeout), self.assertRaises(ValueError):
                create_mcp_server(self.api, timeout_seconds=timeout)


if __name__ == "__main__":
    unittest.main()
