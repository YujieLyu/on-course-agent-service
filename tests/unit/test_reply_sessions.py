import unittest
from unittest.mock import AsyncMock, patch

from src.agents.reply_agent import run_answer
from src.models import (
    ClientType,
    IntentClassification,
    ReplyResponse,
    SearchResult,
    SearchSource,
    SessionStatus,
    StudentIntent,
)
from src.reply_sessions import InvalidSessionActionError, ReplySessionStore


class ReplySessionStoreTests(unittest.IsolatedAsyncioTestCase):
    async def test_revision_numbers_and_status_transitions(self):
        store = ReplySessionStore()
        with patch(
            "src.reply_sessions.create_conversation",
            new=AsyncMock(return_value="conv_1"),
        ):
            session = await store.create("What is due?")

        self.assertEqual(session.status, SessionStatus.PENDING_GENERATION)
        self.assertEqual(session.revisions, [])

        session = store.save_generation("conv_1", "The report is due Friday.")
        self.assertEqual(session.status, SessionStatus.AWAITING_REVIEW)
        self.assertEqual(session.revisions[0].revision_number, 1)

        session = store.request_revision("conv_1", "Make it friendlier.")
        self.assertEqual(session.status, SessionStatus.REVISION_REQUESTED)
        self.assertEqual(session.revisions[1].revision_number, 2)
        self.assertIsNone(session.revisions[1].revision_content)

        session = store.save_generation("conv_1", "Please submit it by Friday.")
        self.assertEqual(session.status, SessionStatus.AWAITING_REVIEW)
        self.assertEqual(
            session.revisions[1].revision_content,
            "Please submit it by Friday.",
        )

        session = store.accept("conv_1")
        self.assertEqual(session.status, SessionStatus.ACCEPTED)

    async def test_invalid_action_is_rejected(self):
        store = ReplySessionStore()
        with patch(
            "src.reply_sessions.create_conversation",
            new=AsyncMock(return_value="conv_1"),
        ):
            await store.create("What is due?")

        with self.assertRaises(InvalidSessionActionError):
            store.request_revision("conv_1", "Change it.")

    async def test_initial_generation_stores_intent_and_search_in_developer_input(self):
        classification = IntentClassification(
            intent=StudentIntent.ASSESSMENT_REQUIREMENTS,
            search_sources=[SearchSource.COURSE_MATERIALS],
            search_query="assignment requirements",
            needs_clarification=False,
        )
        result = SearchResult(
            source=SearchSource.COURSE_MATERIALS,
            content="Submit a PDF report.",
            title="Assignment One",
        )
        request = AsyncMock(
            return_value=ReplyResponse(reply="Submit a PDF report.", status="answered", citations=[result])
        )

        with patch(
            "src.agents.reply_agent.request_structured_output",
            new=request,
        ):
            await run_answer(
                "What is required?",
                ClientType.OPENAI,
                "test-model",
                retrieved_context=[result],
                session_context='{"status":"pending_generation"}',
                conversation_id="conv_1",
                intent_context=classification,
            )

        response_input = request.await_args.kwargs["input"]
        self.assertEqual(response_input[0]["role"], "developer")
        self.assertIn("assessment_requirements", response_input[0]["content"])
        self.assertIn("Submit a PDF report.", response_input[0]["content"])
        self.assertEqual(response_input[1]["role"], "user")
        self.assertIn("What is required?", response_input[1]["content"])
        self.assertEqual(request.await_args.kwargs["conversation_id"], "conv_1")


if __name__ == "__main__":
    unittest.main()
