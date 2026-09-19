import json
import os
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

from fastapi.testclient import TestClient

from main import app
from src.agents.evidence_guard import EvidenceReference, EvidenceReview, insufficient_evidence_reply
from src.models import (
    IntentClassification,
    ReplyResponse,
    SearchResult,
    SearchResults,
    SearchSource,
    StudentIntent,
)
from tests.fakes import make_session_store
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


def supported_review(quote=None, *, answers_question=True):
    return EvidenceReview(
        claims_supported=True,
        answers_question=answers_question,
        has_factual_claims=quote is not None,
        evidence=[EvidenceReference(evidence_id="source_1", quote=quote)] if quote else [],
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
        self.store = make_session_store()
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

    def assert_generated(self, response, expected_reply):
        self.assertEqual(response.status_code, 200)
        session = response.json()
        self.assertEqual(session["conversation_id"], "conv-reply-flow")
        self.assertEqual(session["status"], "awaiting_review")
        self.assertEqual(session["revisions"], [{
            "revision_number": 1, "feedback": None, "revision_content": expected_reply,
        }])
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
        answer = ReplyResponse(
            reply="Assignment One requires a PDF report.",
            status="answered",
            citations=[citation],
        )
        llm_client = fake_llm_client(classification, answer, supported_review(citation.content))
        search_client = MagicMock()
        search_client.search.return_value = [
            {
                "source": "course_materials",
                "content": citation.content,
                "title": citation.title,
                "source_url": citation.source_url,
            }
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
        self.assert_generated(response, answer.reply)
        self.assertEqual(llm_client.responses.parse.await_count, 3)
        self.assertEqual(llm_client.embeddings.create.await_count, 1)
        self.assertEqual(openai_constructor.call_count, 5)
        llm_client.conversations.create.assert_awaited_once()
        search_constructor.assert_called_once()
        search_client.search.assert_called_once()

        classifier_instructions = llm_client.responses.parse.await_args_list[0].kwargs[
            "instructions"
        ]
        answer_input = json.dumps(llm_client.responses.parse.await_args_list[1].kwargs["input"])
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
        answer = ReplyResponse(
            reply="You must disclose generative AI use in assessments.",
            status="answered",
            citations=[citation],
        )
        llm_client = fake_llm_client(classification, answer, supported_review(citation.content))
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
        self.assert_generated(response, answer.reply)
        evidence = list(self.store.load_snapshot("conv-reply-flow").retrieved_context)
        self.assertEqual(evidence, [citation.model_copy(update={
            "content": source_text, "excerpt": source_text,
        })])
        self.assertEqual(llm_client.responses.parse.await_count, 3)
        self.assertEqual(llm_client.embeddings.create.await_count, 2)
        mock_urlopen.assert_called_once()
        search_constructor.assert_not_called()

        answer_input = json.dumps(llm_client.responses.parse.await_args_list[1].kwargs["input"])
        self.assertIn("Students must disclose generative AI use", answer_input)
        self.assertIn("https://policy.example.test/ai-use", answer_input)

    def test_empty_canvas_and_historical_sources_complete(self):
        classification = IntentClassification(
            intent=StudentIntent.PERSONAL_STATUS,
            search_sources=[SearchSource.CANVAS, SearchSource.HISTORICAL_QNA],
            search_query="student submission status",
            needs_clarification=False,
            clarification_question=None,
        )
        answer = ReplyResponse(
            reply="I cannot access that information yet.",
            status="not_found",
            citations=[],
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
                    query="student submission status",
                    results=[],
                )
            ),
        ):
            response = self.generate_reply(
                {
                    "question": "Was my assignment submitted?",
                    "user_id": "student-1",
                    "course_id": "course-1",
                },
            )

        self.assertEqual(response.status_code, 200)
        self.assert_generated(response, insufficient_evidence_reply().reply)
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
        answer = ReplyResponse(
            reply="Which assessment do you mean?",
            status="needs_clarification",
            citations=[],
        )
        llm_client = fake_llm_client(
            classification, answer, supported_review(answers_question=False),
        )

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

    def test_exam_claim_is_blocked_when_assessment_overview_does_not_support_it(self):
        classification = IntentClassification(
            intent=StudentIntent.ASSESSMENT_REQUIREMENTS,
            search_sources=[SearchSource.COURSE_MATERIALS],
            search_query="exam handwritten code pseudocode", needs_clarification=False,
        )
        unrelated = SearchResult(
            source=SearchSource.COURSE_MATERIALS,
            content="Projects/Assignments (85%). Take-home exercises (15%).",
            title="Course Assessment",
        )
        draft = ReplyResponse(
            reply="There is no closed-book exam, so there is no handwritten code test.",
            status="answered", citations=[unrelated],
        )
        rejected = EvidenceReview(
            claims_supported=False, answers_question=False, has_factual_claims=True, evidence=[],
        )
        llm = fake_llm_client(classification, draft, rejected)
        search_client = MagicMock()
        search_client.search.return_value = [unrelated.model_dump(mode="json")]
        with patch.dict(os.environ, TEST_ENV), patch(
            "src.workflow.llm.AsyncOpenAI", return_value=llm,
        ), patch(
            "src.tools.search_providers.azure_ai_search.SearchClient", return_value=search_client,
        ):
            response = self.generate_reply({
                "question": "Is there a handwriting code test in the closed-book exam? Could we use pseudocode?",
                "user_id": "student-1", "course_id": "course-1",
            })
        self.assertEqual(response.status_code, 200)
        self.assert_generated(response, insufficient_evidence_reply().reply)
        self.assertEqual(llm.responses.parse.await_count, 3)
        review_request = llm.responses.parse.await_args.kwargs
        self.assertIn(unrelated.content, review_request["input"])
        self.assertIn(draft.reply, review_request["input"])
        self.assertNotIn("conversation", review_request)

    def test_evidence_review_failure_returns_service_error_instead_of_draft(self):
        classification = IntentClassification(
            intent=StudentIntent.ASSESSMENT_REQUIREMENTS,
            search_sources=[SearchSource.COURSE_MATERIALS], needs_clarification=False,
        )
        source = SearchResult(source=SearchSource.COURSE_MATERIALS, content="Submit a PDF.")
        draft = ReplyResponse(reply="Submit a PDF.", status="answered", citations=[source])
        llm = fake_llm_client(classification, draft)
        llm.responses.parse.side_effect = [
            parsed_response(classification), parsed_response(draft), RuntimeError("guard unavailable"),
        ]
        search_client = MagicMock()
        search_client.search.return_value = [source.model_dump(mode="json")]
        with patch.dict(os.environ, TEST_ENV), patch(
            "src.workflow.llm.AsyncOpenAI", return_value=llm,
        ), patch(
            "src.tools.search_providers.azure_ai_search.SearchClient", return_value=search_client,
        ), patch("main.logger.exception"):
            response = self.generate_reply({
                "question": "What should I submit?", "user_id": "u1", "course_id": "c1",
            })
        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.json(), {"detail": "The service could not generate a reply"})
        self.assertEqual(self.store.get("conv-reply-flow").status, "pending_generation")
        self.assertEqual(self.store.get("conv-reply-flow").revisions, [])
        self.assertNotIn(draft.reply, response.text)

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
