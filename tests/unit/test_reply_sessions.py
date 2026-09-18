import json
import unittest
from unittest.mock import AsyncMock, patch

from src.agents.reply_agent import run_answer
from src.agents.orchestrator import generate_session_revision
from src.agents.evidence_guard import EvidenceReference, EvidenceReview
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
            session = await store.create("What is due?", "course-1", "student-1")

        self.assertEqual(session.status, SessionStatus.PENDING_GENERATION)
        self.assertEqual(session.course_id, "course-1")
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
            await store.create("What is due?", "course-1")

        with self.assertRaises(InvalidSessionActionError):
            store.request_revision("conv_1", "Change it.")

    async def test_revisions_reuse_original_evidence_and_only_teacher_feedback_as_new_evidence(self):
        store = ReplySessionStore()
        classification = IntentClassification(
            intent=StudentIntent.ASSESSMENT_REQUIREMENTS,
            search_sources=[SearchSource.COURSE_MATERIALS], needs_clarification=False,
        )
        original = SearchResult(
            source=SearchSource.COURSE_MATERIALS, content="Pseudocode may be required in the exam.",
        )
        with patch("src.reply_sessions.create_conversation", new=AsyncMock(return_value="local_1")):
            await store.create("Must we write code in the exam?", "course-1")
        store.save_evidence_context("local_1", classification, [original])
        original.content = "Mutated after retrieval"
        _, snapshot = store.get_evidence_context("local_1")
        snapshot[0].content = "Mutated by caller"
        store.save_generation("local_1", "There is no exam.")
        confirmation = "The exam does not require handwritten code. Pseudocode may be required."
        store.request_revision("local_1", confirmation)
        draft = ReplyResponse(reply=confirmation, status="answered", citations=[])
        answer_request = AsyncMock(return_value=draft)
        review_request = AsyncMock(return_value=EvidenceReview(
            claims_supported=True, answers_question=True, has_factual_claims=True,
            evidence=[EvidenceReference(evidence_id="teacher_feedback_1", quote=confirmation)],
        ))
        with patch("src.agents.reply_agent.request_structured_output", new=answer_request), patch(
            "src.agents.evidence_guard.request_structured_output", new=review_request,
        ), patch("src.agents.orchestrator.search") as search:
            revised = await generate_session_revision("local_1", store)
        search.assert_not_called()
        self.assertEqual(revised.revisions[-1].revision_content, confirmation)
        payload = json.loads(review_request.await_args.kwargs["input"])
        self.assertEqual(payload["retrieved_sources"][0]["content"], "Pseudocode may be required in the exam.")
        self.assertEqual(payload["teacher_feedback"], [{
            "evidence_id": "teacher_feedback_1", "content": confirmation,
        }])
        self.assertNotIn("There is no exam.", review_request.await_args.kwargs["input"])
        self.assertIn("Pseudocode may be required in the exam.", answer_request.await_args.kwargs["input"][0]["content"])
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
            return_value=ReplyResponse(reply="Submit a PDF report.", status="answered", citations=[result])
        )

        with patch(
            "src.agents.reply_agent.request_structured_output",
            new=request,
        ), patch(
            "src.agents.evidence_guard.request_structured_output",
            new=AsyncMock(return_value=EvidenceReview(
                claims_supported=True, answers_question=True, has_factual_claims=True,
                evidence=[EvidenceReference(evidence_id="source_1", quote=result.content)],
            )),
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
