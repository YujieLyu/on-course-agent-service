import os
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

from fastapi.testclient import TestClient

from main import app
from src.models import (
    IntentClassification,
    ReplyResponse,
    SearchResult,
    SearchResults,
    SearchSource,
    StudentIntent,
)
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
    return SimpleNamespace(responses=responses, embeddings=embeddings)


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
        self.client = TestClient(app)

    def tearDown(self):
        azure_ai_search._get_settings.cache_clear()
        public_website_search._get_settings.cache_clear()

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
        llm_client = fake_llm_client(classification, answer)
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
            response = self.client.post(
                "/reply",
                json={
                    "question": "What does Assignment One require?",
                    "user_id": "student-1",
                    "course_id": "course-1",
                    "chat_history": [
                        {
                            "role": "user",
                            "content": "I am reviewing Assignment One.",
                        }
                    ],
                },
            )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), answer.model_dump(mode="json"))
        self.assertEqual(llm_client.responses.parse.await_count, 2)
        self.assertEqual(llm_client.embeddings.create.await_count, 1)
        self.assertEqual(openai_constructor.call_count, 3)
        search_constructor.assert_called_once()
        search_client.search.assert_called_once()

        classifier_instructions = llm_client.responses.parse.await_args_list[0].kwargs[
            "instructions"
        ]
        answer_instructions = llm_client.responses.parse.await_args_list[1].kwargs[
            "instructions"
        ]
        self.assertIn("I am reviewing Assignment One.", classifier_instructions)
        self.assertIn("Assignment One requires a PDF report.", answer_instructions)
        self.assertIn("https://course.example.test/assignment-one", answer_instructions)

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
            response = self.client.post(
                "/reply",
                json={
                    "question": "Must I disclose generative AI use?",
                    "user_id": "student-1",
                    "course_id": "course-1",
                },
            )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), answer.model_dump(mode="json"))
        self.assertEqual(llm_client.responses.parse.await_count, 2)
        self.assertEqual(llm_client.embeddings.create.await_count, 2)
        mock_urlopen.assert_called_once()
        search_constructor.assert_not_called()

        answer_instructions = llm_client.responses.parse.await_args_list[1].kwargs[
            "instructions"
        ]
        self.assertIn("Students must disclose generative AI use", answer_instructions)
        self.assertIn("https://policy.example.test/ai-use", answer_instructions)

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
            response = self.client.post(
                "/reply",
                json={
                    "question": "Was my assignment submitted?",
                    "user_id": "student-1",
                    "course_id": "course-1",
                },
            )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), answer.model_dump(mode="json"))
        self.assertEqual(llm_client.responses.parse.await_count, 2)
        llm_client.embeddings.create.assert_not_awaited()
        search_constructor.assert_not_called()
        mock_urlopen.assert_not_called()

        answer_instructions = llm_client.responses.parse.await_args_list[1].kwargs[
            "instructions"
        ]
        self.assertIn("No retrieved context.", answer_instructions)

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
        llm_client = fake_llm_client(classification, answer)

        with patch.dict(os.environ, TEST_ENV, clear=False), patch(
            "src.workflow.llm.AsyncOpenAI",
            return_value=llm_client,
        ), patch(
            "src.tools.search_providers.azure_ai_search.SearchClient"
        ) as search_constructor, patch(
            "src.tools.search_providers.public_website_search.urlopen"
        ) as mock_urlopen:
            response = self.client.post(
                "/reply",
                json={
                    "question": "What about that one?",
                    "user_id": "student-1",
                    "course_id": "course-1",
                },
            )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), answer.model_dump(mode="json"))
        llm_client.embeddings.create.assert_not_awaited()
        search_constructor.assert_not_called()
        mock_urlopen.assert_not_called()

    def test_invalid_request_is_rejected_before_external_calls(self):
        with patch("src.workflow.llm.AsyncOpenAI") as openai_constructor, patch(
            "src.tools.search_providers.azure_ai_search.SearchClient"
        ) as search_constructor:
            response = self.client.post(
                "/reply",
                json={
                    "question": "What is due this week?",
                    "user_id": "student-1",
                },
            )

        self.assertEqual(response.status_code, 422)
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
            response = self.client.post(
                "/reply",
                json={
                    "question": "What does Assignment One require?",
                    "user_id": "student-1",
                    "course_id": "course-1",
                },
            )

        self.assertEqual(response.status_code, 503)
        self.assertEqual(
            response.json(),
            {
                "reply": "The service could not complete the request.",
                "status": "error",
                "citations": [],
            },
        )
        self.assertNotIn("search service unavailable", response.text)
        self.assertEqual(llm_client.responses.parse.await_count, 1)
        self.assertEqual(llm_client.embeddings.create.await_count, 1)
        search_client.search.assert_called_once()
        mock_log_exception.assert_called_once()


if __name__ == "__main__":
    unittest.main()
