import unittest
from unittest.mock import AsyncMock, patch

from fastapi.testclient import TestClient

from main import app
from src.models import ReplyResponse
from src.reply_sessions import ReplySessionStore


class ReplyEndpointTests(unittest.TestCase):
    def test_accepts_minimal_reply_request(self):
        with patch(
            "main.run_orchestration",
            new=AsyncMock(
                return_value=ReplyResponse(reply="ok", status="answered", citations=[])
            ),
        ):
            response = TestClient(app).post(
                "/reply",
                json={
                    "question": "What is due this week?",
                    "user_id": "u1",
                    "course_id": "c1",
                },
            )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["reply"], "ok")

    def test_accepts_chat_history_without_created_at(self):
        with patch(
            "main.run_orchestration",
            new=AsyncMock(
                return_value=ReplyResponse(reply="ok", status="answered", citations=[])
            ),
        ):
            response = TestClient(app).post(
                "/reply",
                json={
                    "question": "What is due this week?",
                    "user_id": "u1",
                    "course_id": "c1",
                    "chat_history": [{"role": "user", "content": "hello"}],
                },
            )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["reply"], "ok")

    def test_returns_structured_error_when_orchestration_fails(self):
        with patch(
            "main.run_orchestration",
            new=AsyncMock(side_effect=RuntimeError("upstream failure")),
        ), patch("main.logger.exception") as mock_log_exception:
            response = TestClient(app).post(
                "/reply",
                json={
                    "question": "What is due this week?",
                    "user_id": "u1",
                    "course_id": "c1",
                },
            )

        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.json()["status"], "error")
        mock_log_exception.assert_called_once()


class ReplySessionEndpointTests(unittest.TestCase):
    def test_complete_review_flow_uses_openai_conversation(self):
        initial_orchestration = AsyncMock(
            return_value=ReplyResponse(
                reply="Initial draft", status="answered", citations=[]
            )
        )
        revision_generation = AsyncMock(
            return_value=ReplyResponse(
                reply="Revised draft", status="answered", citations=[]
            )
        )
        with patch("main.reply_sessions", ReplySessionStore()), patch(
            "src.reply_sessions.create_conversation",
            new=AsyncMock(return_value="conv_1"),
        ), patch("main.run_orchestration", new=initial_orchestration), patch(
            "src.agents.orchestrator.run_answer",
            new=revision_generation,
        ):
            client = TestClient(app)
            created = client.post(
                "/reply-sessions", json={"question": "What is due?"}
            )
            generated = client.post("/reply-sessions/conv_1/generate")
            reviewed = client.post(
                "/reply-sessions/conv_1/review",
                json={"feedback": "Make it friendlier."},
            )
            revised = client.post("/reply-sessions/conv_1/generate")
            accepted = client.post("/reply-sessions/conv_1/accept")

        self.assertEqual(created.json()["status"], "pending_generation")
        self.assertEqual(generated.json()["status"], "awaiting_review")
        self.assertEqual(reviewed.json()["status"], "revision_requested")
        self.assertEqual(reviewed.json()["revisions"][1]["revision_number"], 2)
        self.assertEqual(revised.json()["revisions"][1]["revision_content"], "Revised draft")
        self.assertEqual(accepted.json()["status"], "accepted")
        initial_orchestration.assert_awaited_once()
        revision_generation.assert_awaited_once()
        revision_call = revision_generation.await_args
        self.assertEqual(revision_call.kwargs["conversation_id"], "conv_1")
        self.assertTrue(revision_call.kwargs["is_revision"])
        self.assertIn("Make it friendlier.", revision_call.kwargs["session_context"])


if __name__ == "__main__":
    unittest.main()
