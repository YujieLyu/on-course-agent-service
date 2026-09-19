import asyncio
import io
import json
import logging
import os
import unittest
from unittest.mock import AsyncMock, patch

from fastapi.testclient import TestClient
from fastmcp import Client

from main import app
from mcp_server import create_mcp_server
from src.logger import EventFormatter, get_log_context, get_logger, log_context, log_operation
from tests.fakes import make_session_store


class LoggingTests(unittest.IsolatedAsyncioTestCase):
    async def test_concurrent_requests_and_threads_keep_separate_context(self):
        logger = get_logger("tests.context")

        async def request(request_id):
            with log_context(request_id=request_id):
                await asyncio.sleep(0)
                logger.info("async.event")
                await asyncio.to_thread(logger.info, "thread.event")

        with self.assertLogs(logger) as captured:
            await asyncio.gather(request("first"), request("second"))
        pairs = [(r.getMessage(), r.event_fields["request_id"]) for r in captured.records]
        self.assertCountEqual(pairs, [(event, request_id) for event in ["async.event", "thread.event"] for request_id in ["first", "second"]])
        self.assertEqual(get_log_context(), {})

    async def test_cancelled_operation_is_logged_and_context_is_reset(self):
        logger = get_logger("tests.cancellation")
        with self.assertLogs(logger) as captured:
            with self.assertRaises(asyncio.CancelledError):
                with log_context(request_id="cancelled-request"):
                    with log_operation(logger, "test.operation"):
                        raise asyncio.CancelledError()
        last = captured.records[-1]
        self.assertEqual(last.getMessage(), "test.operation.cancelled")
        self.assertEqual(last.event_fields["outcome"], "cancelled")
        self.assertIn("duration_ms", last.event_fields)
        self.assertEqual(get_log_context(), {})

    async def test_mcp_and_api_logs_share_request_id_without_payloads(self):
        private = "private-student-question-do-not-log"
        with patch("main.reply_sessions", make_session_store()), patch(
            "main.create_conversation", new=AsyncMock(return_value="conv-logs"),
        ), self.assertLogs("mcp_server") as mcp_logs, self.assertLogs("src.observability") as api_logs:
            async with Client(create_mcp_server(app)) as client:
                await client.call_tool("create_reply_session", {
                    "question": private, "course_id": "course-1", "user_id": "private-user-id",
                })
        mcp_id = mcp_logs.records[0].event_fields["request_id"]
        self.assertEqual({r.event_fields["request_id"] for r in api_logs.records}, {mcp_id})
        self.assertEqual(api_logs.records[-1].event_fields["route"], "/reply-sessions")
        self.assertEqual(api_logs.records[-1].event_fields["transport"], "mcp")
        logs = "\n".join(EventFormatter().format(r) for r in [*mcp_logs.records, *api_logs.records])
        self.assertNotIn(private, logs)
        self.assertNotIn("private-user-id", logs)

    async def test_mcp_rejection_keeps_status_and_outcome_without_duplicate_logs(self):
        with (
            patch("main.reply_sessions", make_session_store()),
            self.assertLogs("mcp_server") as mcp_logs,
            self.assertLogs("src.observability") as api_logs,
            self.assertNoLogs("fastmcp.server.server", level="DEBUG"),
        ):
            async with Client(create_mcp_server(app)) as client:
                result = await client.call_tool(
                    "get_reply_session", {"conversation_id": "missing"},
                    raise_on_error=False,
                )

        self.assertTrue(result.is_error)
        self.assertEqual(
            [r.getMessage() for r in mcp_logs.records],
            ["mcp.tool.started", "mcp.tool.failed"],
        )
        failure = mcp_logs.records[-1]
        self.assertEqual(failure.levelno, logging.WARNING)
        self.assertEqual(failure.event_fields["outcome"], "rejected")
        self.assertEqual(api_logs.records[-1].event_fields["status_code"], 404)
        self.assertEqual(
            {r.event_fields["request_id"] for r in api_logs.records},
            {failure.event_fields["request_id"]},
        )

    async def test_mcp_validation_is_logged_once_without_exposing_arguments(self):
        server = create_mcp_server(app)
        with (
            self.assertLogs("mcp_server") as captured,
            self.assertNoLogs("fastmcp.server.server", level="DEBUG"),
        ):
            async with Client(server) as client:
                result = await client.call_tool("create_reply_session", {
                    "question": {"private-field": "private-content"},
                    "course_id": "c1", "user_id": "private-user-id",
                }, raise_on_error=False)
        self.assertTrue(result.is_error)
        self.assertEqual(
            [r.getMessage() for r in captured.records],
            ["mcp.tool.started", "mcp.tool.failed"],
        )
        self.assertEqual(captured.records[-1].event_fields["outcome"], "rejected")
        logs = "\n".join(EventFormatter().format(r) for r in captured.records)
        self.assertNotIn("private-content", logs)
        self.assertNotIn("private-user-id", logs)
        self.assertNotIn("private-field", logs)


class LogFormattingTests(unittest.TestCase):
    def test_json_and_text_error_logs_keep_stack_locations_without_exception_text(self):
        logger = get_logger("tests.formatting")
        with self.assertLogs(logger) as captured:
            try:
                raise ValueError("secret-provider-body-and-api-key")
            except ValueError:
                logger.exception("provider.failed")
        for log_format in ["json", "text", "%(levelname)s %(message)s"]:
            with self.subTest(log_format=log_format), patch.dict(os.environ, {"LOG_FORMAT": log_format}):
                formatted = EventFormatter().format(captured.records[0])
                self.assertNotIn("secret-provider-body-and-api-key", formatted)
                self.assertIn("ValueError", formatted)
                self.assertIn("test_json_and_text_error_logs", formatted)
                if log_format == "json":
                    self.assertEqual(json.loads(formatted)["event"], "provider.failed")

    def test_logger_writes_to_stderr_once(self):
        stderr, stdout = io.StringIO(), io.StringIO()
        with patch("sys.stderr", stderr), patch("sys.stdout", stdout):
            logger = get_logger("tests.stderr.unique")
            self.assertIs(get_logger("tests.stderr.unique"), logger)
            logger.info("one.event")
        self.assertEqual(stdout.getvalue(), "")
        self.assertEqual(len(stderr.getvalue().splitlines()), 1)


class HttpLoggingTests(unittest.TestCase):
    def test_request_id_is_returned_and_validation_failure_is_observable(self):
        with self.assertLogs("src.observability") as captured:
            response = TestClient(app).post("/reply-sessions?secret=private-query", json={"question": "private-question"}, headers={"X-Request-ID": "request-test-123"})
        self.assertEqual(response.status_code, 422)
        self.assertEqual(response.headers["x-request-id"], "request-test-123")
        fields = captured.records[-1].event_fields
        self.assertEqual(fields["status_code"], 422)
        self.assertEqual(fields["outcome"], "failed")
        self.assertIn("duration_ms", fields)
        logs = "\n".join(EventFormatter().format(r) for r in captured.records)
        self.assertNotIn("private-query", logs)
        self.assertNotIn("private-question", logs)

    def test_invalid_request_id_and_unmatched_path_are_not_logged(self):
        with self.assertLogs("src.observability") as captured:
            response = TestClient(app).get("/private-unmatched-path", headers={"X-Request-ID": "invalid private header"})
        self.assertRegex(response.headers["x-request-id"], r"^[a-f0-9]{32}$")
        self.assertEqual(captured.records[-1].event_fields["route"], "<unmatched>")
        logs = "\n".join(EventFormatter().format(r) for r in captured.records)
        self.assertNotIn("private-unmatched-path", logs)
        self.assertNotIn("invalid private header", logs)
