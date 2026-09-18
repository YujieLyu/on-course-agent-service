import unittest
from unittest.mock import AsyncMock, patch

from fastapi.testclient import TestClient

from main import app
from src.models import ReplyResponse
from src.reply_sessions import ReplySessionStore


class RemovedReplyEndpointTests(unittest.TestCase):
    def test_direct_reply_is_unavailable_without_running_generation(self):
        with patch("main.run_orchestration", new=AsyncMock()) as generate:
            response = TestClient(app).post(
                "/reply",
                json={"question": "What is due?", "user_id": "u1", "course_id": "c1"},
            )
        self.assertEqual(response.status_code, 404)
        generate.assert_not_awaited()
        self.assertNotIn("/reply", app.openapi()["paths"])


class ReplySessionEndpointTests(unittest.TestCase):
    def test_every_generated_reply_can_be_revised_multiple_times(self):
        for reply_status in ["answered", "needs_clarification", "not_found", "escalated", "error"]:
            with self.subTest(reply_status=reply_status), patch(
                "main.reply_sessions", ReplySessionStore(),
            ), patch(
                "src.reply_sessions.create_conversation", new=AsyncMock(return_value="conv_1"),
            ), patch(
                "main.run_orchestration",
                new=AsyncMock(return_value=ReplyResponse(reply="Initial draft", status=reply_status, citations=[])),
            ) as initial, patch(
                "src.agents.orchestrator.run_answer",
                new=AsyncMock(side_effect=[
                    ReplyResponse(reply="Second draft", status="answered", citations=[]),
                    ReplyResponse(reply="Third draft", status="answered", citations=[]),
                ]),
            ) as revise:
                client = TestClient(app)
                created = client.post("/reply-sessions", json={"question": "Question", "course_id": "c1"})
                self.assertEqual(created.status_code, 201)
                session_path = f"/reply-sessions/{created.json()['conversation_id']}"
                generated = client.post(f"{session_path}/generate")
                self.assertEqual(generated.status_code, 200)
                self.assertEqual(generated.json()["status"], "awaiting_review")
                for feedback in ["Make it shorter.", "Explain the deadline."]:
                    reviewed = client.post(f"{session_path}/review", json={"feedback": feedback})
                    self.assertEqual(reviewed.status_code, 200)
                    self.assertEqual(reviewed.json()["status"], "revision_requested")
                    generated = client.post(f"{session_path}/generate")
                    self.assertEqual(generated.status_code, 200)
                    self.assertEqual(generated.json()["status"], "awaiting_review")
                revisions = generated.json()["revisions"]
                self.assertEqual([item["revision_content"] for item in revisions], ["Initial draft", "Second draft", "Third draft"])
                self.assertEqual([item["revision_number"] for item in revisions], [1, 2, 3])
                self.assertEqual([item["feedback"] for item in revisions], [None, "Make it shorter.", "Explain the deadline."])
                self.assertEqual(client.get(session_path).json(), generated.json())
                initial.assert_awaited_once()
                self.assertEqual(revise.await_count, 2)
                self.assertEqual(revise.await_args.kwargs["teacher_feedback"], ["Make it shorter.", "Explain the deadline."])

    def test_failed_generation_preserves_state_and_can_be_retried(self):
        for is_revision in [False, True]:
            generation_target = "src.agents.orchestrator.run_answer" if is_revision else "main.run_orchestration"
            store = ReplySessionStore()
            with self.subTest(is_revision=is_revision), patch("main.reply_sessions", store), patch(
                "src.reply_sessions.create_conversation", new=AsyncMock(return_value="conv_1"),
            ), patch(generation_target, new=AsyncMock(side_effect=[
                RuntimeError("dependency unavailable"),
                ReplyResponse(reply="Generated draft", status="answered", citations=[]),
            ])), patch("main.logger.exception"):
                client = TestClient(app)
                client.post("/reply-sessions", json={"question": "Question", "course_id": "c1"})
                if is_revision:
                    store.save_generation("conv_1", "Initial draft")
                    client.post("/reply-sessions/conv_1/review", json={"feedback": "Shorter"})
                before = client.get("/reply-sessions/conv_1").json()
                failed = client.post("/reply-sessions/conv_1/generate")
                self.assertEqual(failed.status_code, 503)
                self.assertEqual(failed.json(), {"detail": "The service could not generate a reply"})
                self.assertEqual(client.get("/reply-sessions/conv_1").json(), before)
                retried = client.post("/reply-sessions/conv_1/generate")
                self.assertEqual(retried.status_code, 200)
                self.assertEqual(retried.json()["status"], "awaiting_review")
                self.assertEqual(retried.json()["revisions"][-1]["revision_content"], "Generated draft")

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
        ), patch(
            "main.save_accepted_qna",
            new=AsyncMock(),
        ):
            client = TestClient(app)
            created = client.post(
                "/reply-sessions",
                json={
                    "question": "What is due?",
                    "course_id": "course-1",
                    "user_id": "student-1",
                },
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

    def test_failed_cosmos_write_does_not_mark_session_accepted(self):
        store = ReplySessionStore()

        async def arrange_session():
            with patch(
                "src.reply_sessions.create_conversation",
                new=AsyncMock(return_value="conv_1"),
            ):
                await store.create("What is due?", "course-1")
            store.save_generation("conv_1", "Friday.")

        import asyncio

        asyncio.run(arrange_session())
        with patch("main.reply_sessions", store), patch(
            "main.save_accepted_qna",
            new=AsyncMock(side_effect=RuntimeError("Cosmos unavailable")),
        ), patch("main.logger.exception"):
            response = TestClient(app).post("/reply-sessions/conv_1/accept")

        self.assertEqual(response.status_code, 503)
        self.assertEqual(
            store.get("conv_1").status.value,
            "awaiting_review",
        )


if __name__ == "__main__":
    unittest.main()
