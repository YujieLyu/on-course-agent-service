import unittest
from unittest.mock import AsyncMock, patch

from pydantic import ValidationError

from src.agents.reply_agent import run_answer
from src.models import ReplyDraft, SearchResult, SearchSource


class ReplyAgentCitationTests(unittest.IsolatedAsyncioTestCase):
    async def test_selected_ids_return_original_source_objects_for_every_provider(self):
        unused = SearchResult(source=SearchSource.COURSE_MATERIALS, content="Lectures run on Mondays.")
        for source_type in SearchSource:
            with self.subTest(source=source_type):
                original = SearchResult(
                    source=source_type, content="Submit a PDF report, including the appendix.",
                    title="Submission requirements", source_url="https://example.edu/assignment?version=2",
                    excerpt="including the appendix",
                )
                request = AsyncMock(return_value=ReplyDraft(
                    reply="Submit a PDF report with the appendix.", status="answered",
                    cited_source_ids=["source_2"],
                ))
                with patch("src.agents.reply_agent.request_structured_output", new=request):
                    response = await run_answer(
                        "What should I submit?", "test-model",
                        retrieved_context=[unused, original],
                    )
                self.assertEqual(response.citations, [original])
                self.assertIs(response.citations[0], original)
                self.assertIs(request.await_args.kwargs["output_type"], ReplyDraft)
                self.assertIn(f"[source_2] source: {source_type}", request.await_args.kwargs["input"][0]["content"])
                self.assertNotIn("cited_source_ids", response.model_dump())

    async def test_duplicate_and_unknown_ids_do_not_replace_the_answer_or_invent_sources(self):
        source = SearchResult(source=SearchSource.COURSE_MATERIALS, content="Submit a PDF report.")
        draft = ReplyDraft(
            reply="Submit a PDF report. The deadline is not available.", status="answered",
            cited_source_ids=["source_999", "source_1", "source_1", "teacher_feedback_1"],
        )
        with patch("src.agents.reply_agent.request_structured_output", new=AsyncMock(return_value=draft)), patch(
            "src.agents.reply_agent.logger.warning",
        ) as warning:
            response = await run_answer(
                "What format and deadline?", "test-model",
                retrieved_context=[source],
            )
        self.assertEqual(response.reply, draft.reply)
        self.assertEqual(response.status, draft.status)
        self.assertEqual(response.citations, [source])
        warning.assert_called_once_with("reply.citations.unknown_sources", extra={
            "event_fields": {"unknown_source_count": 2},
        })

    async def test_no_retrieval_cannot_produce_citations_from_previous_reply(self):
        draft = ReplyDraft(reply="Recursion calls a function on smaller inputs.", status="answered", cited_source_ids=["source_1"])
        with patch("src.agents.reply_agent.request_structured_output", new=AsyncMock(return_value=draft)), patch(
            "src.agents.reply_agent.logger.warning",
        ):
            response = await run_answer(
                "What is recursion?", "test-model",
                previous_reply="Old reply citing source_1", feedback_history=["Explain recursion."],
            )
        self.assertEqual(response.reply, draft.reply)
        self.assertEqual(response.status, "answered")
        self.assertEqual(response.citations, [])

    def test_model_output_cannot_supply_citation_objects_or_source_metadata(self):
        invented = {"source": "course_materials", "content": "Invented rule", "source_url": "https://example.edu/invented"}
        for payload in (
            {"cited_source_ids": ["source_1"], "citations": [invented]},
            {"cited_source_ids": [invented]},
        ):
            with self.subTest(payload=payload), self.assertRaises(ValidationError):
                ReplyDraft.model_validate({"reply": "A reply", "status": "answered", **payload})


if __name__ == "__main__":
    unittest.main()
