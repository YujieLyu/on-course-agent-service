import json
import os
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

from fastapi.testclient import TestClient

from main import app
from src.models import (
    IntentClassification,
    ReplyDraft,
    SearchResult,
    SearchResults,
    SearchSource,
    StudentIntent,
)
from tests.fakes import FakeSessionContainer, make_session_service
from src.tools.search_providers import azure_ai_search, public_website_search


TEST_ENV = {
    "OPENAI_ENDPOINT": "https://openai.example.test/v1",
    "OPENAI_API_KEY": "test-openai-key",
    "AZURE_EMBEDDING_MODEL_DEPLOYMENT_NAME": "test-embedding-model",
    "AZURE_SEARCH_ENDPOINT": "https://search.example.test",
    "AZURE_SEARCH_KEY": "test-search-key",
    "AZURE_SEARCH_INDEX_NAME": "test-index",
    "AZURE_SEARCH_VECTOR_FIELD": "content_vector",
    "AZURE_SEARCH_TEXT_FIELD": "content",
    "AZURE_SEARCH_SOURCE_FIELD": "source",
    "AZURE_SEARCH_KEYWORD_FIELDS": "title,content",
    "AZURE_SEARCH_TOP_K": "3",
    "AZURE_SEARCH_SEMANTIC_CONFIG_NAME": "",
    "PUBLIC_WEBSITE_URLS": "https://policy.example.test/ai-use",
    "WEB_SEARCH_TOP_K": "3",
}


def parsed_response(value):
    return SimpleNamespace(output=[], output_parsed=value, output_text="")


def fake_llm_client(*structured_outputs):
    responses = SimpleNamespace(
        parse=AsyncMock(
            side_effect=[parsed_response(value) for value in structured_outputs]
        )
    )
    embeddings = SimpleNamespace(
        create=AsyncMock(
            return_value=SimpleNamespace(
                data=[SimpleNamespace(embedding=[1.0, 0.0, 0.0])]
            )
        )
    )
    return SimpleNamespace(
        responses=responses,
        embeddings=embeddings,
        conversations=SimpleNamespace(
            create=AsyncMock(return_value=SimpleNamespace(id="conv-reply-flow"))),
    )


class FakeHeaders:
    def get_content_charset(self):
        return "utf-8"


class FakeWebResponse:
    def __init__(self, html):
        self.headers = FakeHeaders()
        self._html = html

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        return False

    def read(self):
        return self._html.encode("utf-8")


class ReplyFlowIntegrationTests(unittest.TestCase):
    def setUp(self):
        azure_ai_search._get_settings.cache_clear()
        public_website_search._get_settings.cache_clear()
        self.store = make_session_service()
        store_patch = patch("main.reply_sessions", self.store)
        store_patch.start()
        self.addCleanup(store_patch.stop)
        self.client = TestClient(app)

    def tearDown(self):
        azure_ai_search._get_settings.cache_clear()
        public_website_search._get_settings.cache_clear()

    def generate_reply(self, payload):
        created = self.client.post("/reply-sessions", json=payload)
        self.assertEqual(created.status_code, 201)
        return self.client.post(
            f"/reply-sessions/{created.json()['conversation_id']}/generate")

    def assert_generated(self, response, expected_reply, citations=()):
        self.assertEqual(response.status_code, 200)
        session = response.json()
        self.assertEqual(session["conversation_id"], "conv-reply-flow")
        self.assertEqual(session["status"], "awaiting_review")
        self.assertEqual(session["revisions"], [{
            "revision_number": 1, "feedback": None, "revision_content": expected_reply,
        }])
        self.assertEqual(session["citations"], [citation.model_dump(mode="json") for citation in citations])
        self.assertEqual(self.client.get("/reply-sessions/conv-reply-flow").json(), session)

    def test_health_check(self):
        response = self.client.get("/health")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), "running well")

    def test_course_material_request_runs_the_complete_internal_flow(self):
        classification = IntentClassification(
            intent=StudentIntent.ASSESSMENT_REQUIREMENTS,
            search_sources=[SearchSource.COURSE_MATERIALS],
            search_query="assignment one requirements",
            needs_clarification=False,
            clarification_question=None,
        )
        citation = SearchResult(
            source=SearchSource.COURSE_MATERIALS,
            content="Assignment One requires a PDF report.",
            title="Assignment One",
            source_url="https://course.example.test/assignment-one",
        )
        answer = ReplyDraft(
            reply="Assignment One requires a PDF report.",
            status="answered",
            cited_source_ids=["source_1"],
        )
        llm_client = fake_llm_client(classification, answer)
        search_client = MagicMock()
        search_client.search.return_value = [
            {
                "source": "course_materials",
                "content": citation.content,
                "title": citation.title,
                "source_url": citation.source_url,
            },
            {"source": "course_materials", "content": "Lectures run on Mondays.", "title": "Timetable"},
        ]

        with patch.dict(os.environ, TEST_ENV, clear=False), patch(
            "src.workflow.llm.AsyncOpenAI",
            return_value=llm_client,
        ) as openai_constructor, patch(
            "src.tools.search_providers.azure_ai_search.SearchClient",
            return_value=search_client,
        ) as search_constructor:
            response = self.generate_reply(
                {
                    "question": "What does Assignment One require?",
                    "user_id": "student-1",
                    "course_id": "course-1",
                },
            )

        self.assertEqual(response.status_code, 200)
        self.assert_generated(response, answer.reply, [citation])
        self.assertEqual(len(self.store.load_snapshot("conv-reply-flow").session.retrieved_context), 2)
        self.assertEqual(llm_client.responses.parse.await_count, 2)
        self.assertEqual(llm_client.embeddings.create.await_count, 1)
        self.assertEqual(openai_constructor.call_count, 4)
        llm_client.conversations.create.assert_awaited_once()
        search_constructor.assert_called_once()
        search_client.search.assert_called_once()

        classifier_instructions = llm_client.responses.parse.await_args_list[0].kwargs[
            "instructions"
        ]
        answer_input = json.dumps(llm_client.responses.parse.await_args_list[1].kwargs["input"])
        self.assertIs(llm_client.responses.parse.await_args_list[1].kwargs["text_format"], ReplyDraft)
        self.assertIn("[source_1]", answer_input)
        self.assertIn("[source_2]", answer_input)
        self.assertIn("What does Assignment One require?", classifier_instructions)
        self.assertEqual(llm_client.responses.parse.await_args_list[1].kwargs["conversation"], "conv-reply-flow")
        self.assertIn("Assignment One requires a PDF report.", answer_input)
        self.assertIn("https://course.example.test/assignment-one", answer_input)

    def test_university_policy_request_runs_public_website_retrieval(self):
        classification = IntentClassification(
            intent=StudentIntent.ACADEMIC_INTEGRITY,
            search_sources=[SearchSource.UNIVERSITY_POLICIES],
            search_query="disclosing generative AI use",
            needs_clarification=False,
            clarification_question=None,
        )
        citation = SearchResult(
            source=SearchSource.UNIVERSITY_POLICIES,
            content="Students must disclose generative AI use in assessments.",
            title="AI Use Policy",
            source_url="https://policy.example.test/ai-use",
            excerpt="Students must disclose generative AI use in assessments.",
        )
        answer = ReplyDraft(
            reply="You must disclose generative AI use in assessments.",
            status="answered",
            cited_source_ids=["source_1"],
        )
        llm_client = fake_llm_client(classification, answer)
        html = (
            "<html><head><title>AI Use Policy</title></head>"
            "<body>Students must disclose generative AI use in assessments.</body>"
            "</html>"
        )

        with patch.dict(os.environ, TEST_ENV, clear=False), patch(
            "src.workflow.llm.AsyncOpenAI",
            return_value=llm_client,
        ), patch(
            "src.tools.search_providers.public_website_search.urlopen",
            return_value=FakeWebResponse(html),
        ) as mock_urlopen, patch(
            "src.tools.search_providers.azure_ai_search.SearchClient"
        ) as search_constructor:
            response = self.generate_reply(
                {
                    "question": "Must I disclose generative AI use?",
                    "user_id": "student-1",
                    "course_id": "course-1",
                },
            )

        self.assertEqual(response.status_code, 200)
        source_text = "AI Use Policy Students must disclose generative AI use in assessments."
        original_citation = citation.model_copy(update={"content": source_text, "excerpt": source_text})
        self.assert_generated(response, answer.reply, [original_citation])
        evidence = list(self.store.load_snapshot("conv-reply-flow").session.retrieved_context)
        self.assertEqual(evidence, [original_citation])
        self.assertEqual(llm_client.responses.parse.await_count, 2)
        self.assertEqual(llm_client.embeddings.create.await_count, 2)
        mock_urlopen.assert_called_once()
        search_constructor.assert_not_called()

        answer_input = json.dumps(llm_client.responses.parse.await_args_list[1].kwargs["input"])
        self.assertIn("Students must disclose generative AI use", answer_input)
        self.assertIn("https://policy.example.test/ai-use", answer_input)

    def test_empty_canvas_and_historical_sources_complete(self):
        classification = IntentClassification(
            intent=StudentIntent.ASSESSMENT_REQUIREMENTS,
            search_sources=[SearchSource.CANVAS, SearchSource.HISTORICAL_QNA],
            search_query="assignment two deadline",
            needs_clarification=False,
            clarification_question=None,
        )
        answer = ReplyDraft(
            reply="The deadline for Assignment Two is not available in the supplied course information.",
            status="not_found",
            cited_source_ids=[],
        )
        llm_client = fake_llm_client(classification, answer)

        with patch.dict(os.environ, TEST_ENV, clear=False), patch(
            "src.workflow.llm.AsyncOpenAI",
            return_value=llm_client,
        ), patch(
            "src.tools.search_providers.azure_ai_search.SearchClient"
        ) as search_constructor, patch(
            "src.tools.search_providers.public_website_search.urlopen"
        ) as mock_urlopen, patch(
            "src.tools.search_providers.historical_qna_search.search",
            new=AsyncMock(
                return_value=SearchResults(
                    query="assignment two deadline",
                    results=[],
                )
            ),
        ):
            response = self.generate_reply(
                {
                    "question": "When is Assignment Two due?",
                    "user_id": "student-1",
                    "course_id": "course-1",
                },
            )

        self.assertEqual(response.status_code, 200)
        self.assert_generated(response, answer.reply)
        self.assertEqual(llm_client.responses.parse.await_count, 2)
        llm_client.embeddings.create.assert_not_awaited()
        search_constructor.assert_not_called()
        mock_urlopen.assert_not_called()

        answer_input = json.dumps(llm_client.responses.parse.await_args_list[1].kwargs["input"])
        self.assertIn("No retrieved context.", answer_input)

    def test_clarification_response_completes_without_retrieval(self):
        classification = IntentClassification(
            intent=StudentIntent.ASSESSMENT_REQUIREMENTS,
            search_sources=[],
            search_query=None,
            needs_clarification=True,
            clarification_question="Which assessment do you mean?",
        )
        answer = ReplyDraft(
            reply="Which assessment do you mean?",
            status="needs_clarification",
            cited_source_ids=[],
        )
        llm_client = fake_llm_client(classification, answer)

        with patch.dict(os.environ, TEST_ENV, clear=False), patch(
            "src.workflow.llm.AsyncOpenAI",
            return_value=llm_client,
        ), patch(
            "src.tools.search_providers.azure_ai_search.SearchClient"
        ) as search_constructor, patch(
            "src.tools.search_providers.public_website_search.urlopen"
        ) as mock_urlopen:
            response = self.generate_reply(
                {
                    "question": "What about that one?",
                    "user_id": "student-1",
                    "course_id": "course-1",
                },
            )

        self.assertEqual(response.status_code, 200)
        self.assert_generated(response, answer.reply)
        llm_client.embeddings.create.assert_not_awaited()
        search_constructor.assert_not_called()
        mock_urlopen.assert_not_called()

    def test_partial_answer_is_preserved_with_retrieved_context(self):
        classification = IntentClassification(
            intent=StudentIntent.ASSESSMENT_REQUIREMENTS,
            search_sources=[SearchSource.COURSE_MATERIALS],
            search_query="assignment one format deadline", needs_clarification=False,
        )
        source = SearchResult(
            source=SearchSource.COURSE_MATERIALS,
            content="Assignment One requires a PDF report.",
            title="Assignment One",
        )
        draft = ReplyDraft(
            reply="Submit a PDF report. The deadline is not available in the supplied instructions.",
            status="answered", cited_source_ids=["source_1"],
        )
        llm = fake_llm_client(classification, draft)
        search_client = MagicMock()
        search_client.search.return_value = [source.model_dump(mode="json")]
        with patch.dict(os.environ, TEST_ENV), patch(
            "src.workflow.llm.AsyncOpenAI", return_value=llm,
        ), patch(
            "src.tools.search_providers.azure_ai_search.SearchClient", return_value=search_client,
        ):
            response = self.generate_reply({
                "question": "What format should I submit for Assignment One, and when is it due?",
                "user_id": "student-1", "course_id": "course-1",
            })
        self.assertEqual(response.status_code, 200)
        self.assert_generated(response, draft.reply, [source])
        self.assertEqual(llm.responses.parse.await_count, 2)
        self.assertEqual(list(self.store.load_snapshot("conv-reply-flow").session.retrieved_context), [source])

    def test_general_question_is_answered_without_retrieval_or_evidence_review(self):
        classification = IntentClassification(
            intent=StudentIntent.COURSE_CONTENT_EXPLANATION,
            search_sources=[], search_query=None, needs_clarification=False,
        )
        draft = ReplyDraft(
            reply="Recursion solves a problem by calling the same function on smaller inputs until a base case is reached.",
            status="answered", cited_source_ids=[],
        )
        llm = fake_llm_client(classification, draft)
        with patch.dict(os.environ, TEST_ENV), patch(
            "src.workflow.llm.AsyncOpenAI", return_value=llm,
        ), patch(
            "src.tools.search_providers.azure_ai_search.SearchClient",
        ) as search_constructor, patch(
            "src.tools.search_providers.public_website_search.urlopen",
        ) as website_search, patch(
            "src.tools.search_providers.historical_qna_search.search",
        ) as historical_search:
            response = self.generate_reply({
                "question": "What is recursion?", "user_id": "u1", "course_id": "c1",
            })
        self.assert_generated(response, draft.reply)
        self.assertEqual(llm.responses.parse.await_count, 2)
        llm.embeddings.create.assert_not_awaited()
        search_constructor.assert_not_called()
        website_search.assert_not_called()
        historical_search.assert_not_called()

    def test_empty_retrieval_does_not_block_general_explanation(self):
        classification = IntentClassification(
            intent=StudentIntent.COURSE_CONTENT_EXPLANATION,
            search_sources=[SearchSource.COURSE_MATERIALS],
            search_query="recursion example", needs_clarification=False,
        )
        draft = ReplyDraft(
            reply="A recursive function calls itself on a smaller input and stops at a base case.",
            status="answered", cited_source_ids=[],
        )
        llm = fake_llm_client(classification, draft)
        search_client = MagicMock()
        search_client.search.return_value = []
        with patch.dict(os.environ, TEST_ENV), patch(
            "src.workflow.llm.AsyncOpenAI", return_value=llm,
        ), patch(
            "src.tools.search_providers.azure_ai_search.SearchClient", return_value=search_client,
        ):
            response = self.generate_reply({
                "question": "Explain recursion with an example.", "user_id": "u1", "course_id": "c1",
            })
        self.assert_generated(response, draft.reply)
        self.assertEqual(llm.responses.parse.await_count, 2)
        search_client.search.assert_called_once()
        self.assertIn("No retrieved context.", json.dumps(llm.responses.parse.await_args.kwargs["input"]))

    def test_invalid_request_is_rejected_before_external_calls(self):
        with patch("src.workflow.llm.AsyncOpenAI") as openai_constructor, patch(
            "src.tools.search_providers.azure_ai_search.SearchClient"
        ) as search_constructor:
            response = self.client.post(
                "/reply-sessions",
                json={
                    "question": "What is due this week?",
                    "user_id": "student-1",
                },
            )

        self.assertEqual(response.status_code, 422)
        self.assertEqual(self.store.repository.snapshots, {})
        openai_constructor.assert_not_called()
        search_constructor.assert_not_called()

    def test_revisions_resolve_stable_ids_from_persisted_retrieval_after_restart(self):
        classification = IntentClassification(
            intent=StudentIntent.ASSESSMENT_REQUIREMENTS,
            search_sources=[SearchSource.COURSE_MATERIALS], needs_clarification=False,
        )
        sources = [
            SearchResult(
                source=SearchSource.COURSE_MATERIALS, content="The report is due Friday.",
                title="Deadline", source_url="https://example.edu/deadline", excerpt="due Friday",
            ),
            SearchResult(
                source=SearchSource.COURSE_MATERIALS, content="Submit a PDF report.",
                title="Format", source_url="https://example.edu/format", excerpt="PDF report",
            ),
        ]
        drafts = [
            ReplyDraft(reply="Submit a PDF report.", status="answered", cited_source_ids=["source_2"]),
            ReplyDraft(reply="Submit a PDF report by Friday.", status="answered", cited_source_ids=["source_1", "source_2", "source_99"]),
            ReplyDraft(reply="Plan time to proofread your work.", status="answered", cited_source_ids=[]),
        ]
        llm = fake_llm_client(classification, *drafts)
        container = FakeSessionContainer()
        search_client = MagicMock()
        search_client.search.return_value = [source.model_dump(mode="json") for source in sources]
        with patch.dict(os.environ, TEST_ENV), patch(
            "src.workflow.llm.AsyncOpenAI", return_value=llm,
        ), patch(
            "src.tools.search_providers.azure_ai_search.SearchClient", return_value=search_client,
        ), patch("main.reply_sessions", make_session_service(container)):
            initial = self.generate_reply({"question": "What should I submit?", "course_id": "course-1"})
            self.assert_generated(initial, drafts[0].reply, [sources[1]])
            session_id = initial.json()["conversation_id"]
            restarted = make_session_service(container)
            with patch("main.reply_sessions", restarted):
                for index, draft in enumerate(drafts[1:], start=1):
                    feedback = "Include the deadline." if index == 1 else "Give general proofreading advice."
                    reviewed = self.client.post(f"/reply-sessions/{session_id}/review", json={"feedback": feedback})
                    self.assertEqual(reviewed.status_code, 200)
                    revised = self.client.post(f"/reply-sessions/{session_id}/generate")
                    self.assertEqual(revised.status_code, 200)
                    self.assertEqual(revised.json()["revisions"][-1]["revision_content"], draft.reply)
                    self.assertEqual(revised.json()["citations"], [sources[1].model_dump(mode="json"), sources[0].model_dump(mode="json")])
                    self.assertNotIn("cited_source_ids", revised.json())
                    self.assertTrue(all("citations" not in revision for revision in revised.json()["revisions"]))
        self.assertEqual(restarted.get(session_id).retrieved_context, tuple(sources))
        self.assertEqual(len(restarted.get(session_id).revisions), 3)
        search_client.search.assert_called_once()
        self.assertEqual(llm.responses.parse.await_count, 4)
        for request in llm.responses.parse.await_args_list[1:]:
            registry = request.kwargs["input"][0]["content"]
            self.assertIn("[source_1] source: course_materials\ntitle: Deadline", registry)
            self.assertIn("[source_2] source: course_materials\ntitle: Format", registry)

    def test_search_failure_returns_a_structured_service_error(self):
        classification = IntentClassification(
            intent=StudentIntent.ASSESSMENT_REQUIREMENTS,
            search_sources=[SearchSource.COURSE_MATERIALS],
            search_query="assignment one requirements",
            needs_clarification=False,
            clarification_question=None,
        )
        llm_client = fake_llm_client(classification)
        search_client = MagicMock()
        search_client.search.side_effect = RuntimeError("search service unavailable")

        with patch.dict(os.environ, TEST_ENV, clear=False), patch(
            "src.workflow.llm.AsyncOpenAI",
            return_value=llm_client,
        ), patch(
            "src.tools.search_providers.azure_ai_search.SearchClient",
            return_value=search_client,
        ), patch("main.logger.exception") as mock_log_exception:
            response = self.generate_reply(
                {
                    "question": "What does Assignment One require?",
                    "user_id": "student-1",
                    "course_id": "course-1",
                },
            )

        self.assertEqual(response.status_code, 503)
        self.assertEqual(
            response.json(),
            {"detail": "The service could not generate a reply"},
        )
        self.assertNotIn("search service unavailable", response.text)
        self.assertEqual(llm_client.responses.parse.await_count, 1)
        self.assertEqual(llm_client.embeddings.create.await_count, 1)
        search_client.search.assert_called_once()
        mock_log_exception.assert_called_once()


if __name__ == "__main__":
    unittest.main()
