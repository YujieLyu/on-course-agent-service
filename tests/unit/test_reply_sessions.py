import json
import unittest
from unittest.mock import AsyncMock, patch

from pydantic import ValidationError

from src.agents.reply_agent import run_answer
from src.agents.orchestrator import generate_session_revision
from src.models import (
    ClientType,
    IntentClassification,
    ReplyDraft,
    SearchResult,
    SearchSource,
    StudentIntent,
)
from tests.fakes import make_session_service


class ReplySessionServiceTests(unittest.IsolatedAsyncioTestCase):
    async def test_revisions_reuse_original_context_and_teacher_feedback_without_search(self):
        store = make_session_service()
        classification = IntentClassification(
            intent=StudentIntent.ASSESSMENT_REQUIREMENTS,
            search_sources=[SearchSource.COURSE_MATERIALS], needs_clarification=False,
        )
        original = SearchResult(
            source=SearchSource.COURSE_MATERIALS, content="Pseudocode may be required in the exam.",
        )
        store.create("local_1", "Must we write code in the exam?", "course-1")
        store.save_generation(store.load_snapshot("local_1"), "There is no exam.", evidence=(classification, [original]))
        with self.assertRaises(ValidationError):
            original.content = "Mutated after retrieval"
        snapshot = store.load_snapshot("local_1").session.retrieved_context
        with self.assertRaises(ValidationError):
            snapshot[0].content = "Mutated by caller"
        confirmation = "The exam does not require handwritten code. Pseudocode may be required."
        store.request_revision("local_1", confirmation)
        draft = ReplyDraft(reply=confirmation, status="answered", cited_source_ids=["source_1"])
        answer_request = AsyncMock(return_value=draft)
        with patch("src.agents.reply_agent.request_structured_output", new=answer_request), patch(
            "src.agents.orchestrator.search",
        ) as search:
            revised = await generate_session_revision(store.load_snapshot("local_1").session)
        search.assert_not_called()
        self.assertEqual(revised.reply, confirmation)
        self.assertEqual(revised.citations, [original])
        answer_request.assert_awaited_once()
        messages = answer_request.await_args.kwargs["input"]
        self.assertIn("Pseudocode may be required in the exam.", messages[0]["content"])
        self.assertIn("[source_1]", messages[0]["content"])
        self.assertNotIn("There is no exam.", messages[0]["content"])
        session = json.loads(messages[1]["content"].split("Reply session:\n", 1)[1].split("\n\nTask:", 1)[0])
        self.assertEqual(session["student_query"], "Must we write code in the exam?")
        self.assertEqual(session["revisions"][-1]["feedback"], confirmation)
        self.assertEqual(session["revisions"][-2]["revision_content"], "There is no exam.")
        self.assertIsNone(answer_request.await_args.kwargs["conversation_id"])

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
            return_value=ReplyDraft(reply="Submit a PDF report.", status="answered", cited_source_ids=["source_1"])
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
