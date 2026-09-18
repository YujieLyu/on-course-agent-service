import json
import unittest
from unittest.mock import AsyncMock, patch

from src.agents.evidence_guard import (
    EvidenceReference,
    EvidenceReview,
    enforce_evidence_guard,
    insufficient_evidence_reply,
)
from src.models import ChatMessage, ReplyResponse, SearchResult, SearchSource


class EvidenceGuardTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.source = SearchResult(
            source=SearchSource.COURSE_MATERIALS,
            content="The exam does not require handwritten code. Pseudocode may be required.",
            title="Exam instructions",
        )
        self.draft = ReplyResponse(
            reply="You do not need to write code by hand. You may be asked for pseudocode.",
            status="answered", citations=[self.source],
        )
        self.review = EvidenceReview(
            claims_supported=True, answers_question=True, has_factual_claims=True,
            evidence=[EvidenceReference(evidence_id="source_1", quote=self.source.content)],
        )

    async def check(self, **overrides):
        args = dict(
            query="Will I need to write code by hand or use pseudocode in the exam?",
            response=self.draft, client_type="openai", model="test-model",
            retrieved_context=[self.source], chat_history=[], teacher_feedback=[],
        )
        args.update(overrides)
        return await enforce_evidence_guard(**args)

    async def test_no_evidence_blocks_an_answer_without_another_model_call(self):
        with patch("src.agents.evidence_guard.request_structured_output") as request:
            response = await self.check(retrieved_context=[])
        self.assertEqual(response, insufficient_evidence_reply())
        request.assert_not_called()

    async def test_not_found_cannot_smuggle_an_unsupported_conclusion(self):
        draft = self.draft.model_copy(update={
            "reply": "I cannot confirm the details, so there is no exam.", "status": "not_found",
        })
        with patch("src.agents.evidence_guard.request_structured_output") as request:
            response = await self.check(response=draft)
        self.assertEqual(response, insufficient_evidence_reply())
        request.assert_not_called()

    async def test_unsupported_claims_are_removed_regardless_of_status(self):
        review = self.review.model_copy(update={"claims_supported": False})
        for status in ["answered", "needs_clarification", "escalated", "error"]:
            with self.subTest(status=status), patch(
                "src.agents.evidence_guard.request_structured_output",
                new=AsyncMock(return_value=review),
            ):
                response = await self.check(response=self.draft.model_copy(update={"status": status}))
                self.assertEqual(response, insufficient_evidence_reply())

    async def test_supported_but_incomplete_answer_is_not_marked_answered(self):
        review = self.review.model_copy(update={"answers_question": False})
        with patch(
            "src.agents.evidence_guard.request_structured_output", new=AsyncMock(return_value=review),
        ):
            response = await self.check()
        self.assertEqual(response, insufficient_evidence_reply())

    async def test_approval_without_supporting_quotes_fails_closed(self):
        for has_claims in [True, False]:
            review = self.review.model_copy(update={"evidence": [], "has_factual_claims": has_claims})
            with self.subTest(has_claims=has_claims), patch(
                "src.agents.evidence_guard.request_structured_output", new=AsyncMock(return_value=review),
            ):
                response = await self.check()
                self.assertEqual(response, insufficient_evidence_reply())

    async def test_fabricated_sources_quotes_and_blank_quotes_fail_closed(self):
        references = [
            EvidenceReference(evidence_id="source_99", quote=self.source.content),
            EvidenceReference(evidence_id="source_1", quote="There is no exam."),
            EvidenceReference(evidence_id="source_1", quote="   "),
        ]
        for reference in references:
            review = self.review.model_copy(update={"evidence": [reference]})
            with self.subTest(reference=reference), patch(
                "src.agents.evidence_guard.request_structured_output", new=AsyncMock(return_value=review),
            ):
                response = await self.check()
                self.assertEqual(response, insufficient_evidence_reply())

    async def test_supported_reply_uses_original_citations_and_isolated_review(self):
        forged = self.source.model_copy(update={"source_url": "https://invented.example/"})
        draft = self.draft.model_copy(update={"citations": [forged]})
        review = self.review.model_copy(update={"evidence": [EvidenceReference(
            evidence_id="source_1", quote=self.source.content.replace(". ", ".\n"),
        )]})
        request = AsyncMock(return_value=review)
        with patch("src.agents.evidence_guard.request_structured_output", new=request):
            response = await self.check(response=draft)
        self.assertEqual(response.reply, draft.reply)
        self.assertEqual(response.citations, [self.source])
        self.assertIsNone(request.await_args.kwargs["conversation_id"])

    async def test_clarification_without_factual_claims_does_not_need_evidence(self):
        draft = ReplyResponse(reply="Which assessment do you mean?", status="needs_clarification", citations=[])
        review = EvidenceReview(claims_supported=True, answers_question=False, has_factual_claims=False, evidence=[])
        with patch("src.agents.evidence_guard.request_structured_output", new=AsyncMock(return_value=review)):
            response = await self.check(response=draft, retrieved_context=[])
        self.assertEqual(response, draft)

    async def test_out_of_scope_routing_does_not_exempt_factual_claims(self):
        review = self.review.model_copy(update={"evidence": []})
        with patch("src.agents.evidence_guard.request_structured_output", new=AsyncMock(return_value=review)):
            response = await self.check(retrieved_context=[], allow_unreferenced_answer=True)
        self.assertEqual(response, insufficient_evidence_reply())

    async def test_explicit_teacher_confirmation_can_support_a_revision(self):
        review = self.review.model_copy(update={"evidence": [EvidenceReference(
            evidence_id="teacher_feedback_1", quote=self.source.content,
        )]})
        request = AsyncMock(return_value=review)
        with patch("src.agents.evidence_guard.request_structured_output", new=request):
            response = await self.check(
                retrieved_context=[], teacher_feedback=[self.source.content],
                chat_history=[ChatMessage(role="assistant", content="There is no exam.")],
            )
        self.assertEqual(response.status, "answered")
        self.assertEqual(response.citations, [])
        payload = json.loads(request.await_args.kwargs["input"])
        self.assertEqual(payload["retrieved_sources"], [])
        self.assertEqual(payload["teacher_feedback"][0]["content"], self.source.content)

    async def test_review_dependency_failure_never_releases_the_draft(self):
        with patch(
            "src.agents.evidence_guard.request_structured_output",
            new=AsyncMock(side_effect=RuntimeError("review unavailable")),
        ):
            with self.assertRaises(RuntimeError):
                await self.check()


if __name__ == "__main__":
    unittest.main()
