import json
import os
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, Mock, patch

from fastapi.testclient import TestClient

from main import app
from src.models import ReplyDraft
from src.tools.search_providers import azure_ai_search, public_website_search
from tests.fakes import FakeSessionContainer, make_session_service


TEST_ENV = {
    'OPENAI_ENDPOINT': 'https://openai.example.test/v1', 'OPENAI_API_KEY': 'test-key',
    'REPLY_MODEL_NAME': 'test-model', 'AZURE_EMBEDDING_MODEL_DEPLOYMENT_NAME': 'test-embedding',
    'AZURE_SEARCH_ENDPOINT': 'https://search.example.test', 'AZURE_SEARCH_KEY': 'test-key',
    'AZURE_SEARCH_INDEX_NAME': 'test-index', 'AZURE_SEARCH_VECTOR_FIELD': 'content_vector',
    'AZURE_SEARCH_TEXT_FIELD': 'content', 'AZURE_SEARCH_SOURCE_FIELD': 'source',
    'AZURE_SEARCH_KEYWORD_FIELDS': 'title,content', 'AZURE_SEARCH_TOP_K': '3',
    'AZURE_SEARCH_SEMANTIC_CONFIG_NAME': '', 'PUBLIC_WEBSITE_URLS': 'https://policy.example.test/ai',
    'WEB_SEARCH_TOP_K': '3', 'AZURE_COSMOS_ENDPOINT': 'https://cosmos.example.test',
    'AZURE_COSMOS_KEY': 'test-key', 'AZURE_COSMOS_DATABASE_NAME': 'test-db',
    'AZURE_COSMOS_EMBEDDING_DIMENSIONS': '3',
}


def parsed_response(draft):
    return SimpleNamespace(output=[], output_parsed=draft, output_text='')


def fake_llm_client(*drafts):
    return SimpleNamespace(
        responses=SimpleNamespace(parse=AsyncMock(side_effect=[parsed_response(draft) for draft in drafts])),
        embeddings=SimpleNamespace(create=AsyncMock(return_value=SimpleNamespace(data=[SimpleNamespace(embedding=[1.0, 0.0, 0.0])]))),
    )


class FakeWebResponse:
    headers = SimpleNamespace(get_content_charset=lambda: 'utf-8')
    def __enter__(self):
        return self
    def __exit__(self, *args):
        return False
    def read(self):
        return b'<html><head><title>AI Policy</title></head><body>Declare generative AI use.</body></html>'


class ReplyFlowIntegrationTests(unittest.TestCase):
    def setUp(self):
        azure_ai_search._get_settings.cache_clear()
        public_website_search._get_settings.cache_clear()
        self.addCleanup(azure_ai_search._get_settings.cache_clear)
        self.addCleanup(public_website_search._get_settings.cache_clear)
        self.container = FakeSessionContainer()
        self.store = make_session_service(self.container)
        self.qa = FakeSessionContainer(partition_field='course_id')
        self.qa_client = Mock(wraps=self.qa)
        self.qa_client.query_items = Mock(return_value=[{'student_query': 'How should I submit?', 'accepted_response': 'Use the portal.'}])
        self.llm = fake_llm_client()
        self.course_search = MagicMock()
        self.course_search.search.return_value = [{
            'source': 'course_materials', 'content': 'Submit a PDF report.',
            'title': 'Assignment', 'source_url': 'https://course.example.test/assignment',
        }]
        self.web = Mock(return_value=FakeWebResponse())
        for patcher in [
            patch.dict(os.environ, TEST_ENV), patch('main.reply_sessions', self.store),
            patch('src.workflow.llm.AsyncOpenAI', return_value=self.llm),
            patch('src.tools.search_providers.azure_ai_search.SearchClient', return_value=self.course_search),
            patch('src.tools.search_providers.public_website_search.urlopen', self.web),
            patch('src.repositories.historical_qna._get_container', return_value=self.qa_client),
        ]:
            patcher.start()
            self.addCleanup(patcher.stop)
        self.client = TestClient(app)

    def create(self, question='What should I submit?'):
        response = self.client.post('/reply-sessions', json={'question': question, 'course_id': 'course-1'})
        self.assertEqual(response.status_code, 201)
        return '/reply-sessions/' + response.json()['conversation_id']

    def test_generate_review_after_restart_and_accept_publishes_latest_reply(self):
        drafts = [
            ReplyDraft(reply='Submit a PDF and declare AI use.', status='answered', cited_source_ids=['source_1', 'source_2']),
            ReplyDraft(reply='Submit the PDF through the portal and declare AI use.', status='answered', cited_source_ids=['source_3', 'source_1', 'source_999']),
            ReplyDraft(reply='Upload the PDF; declare AI use.', status='answered', cited_source_ids=[]),
        ]
        self.llm.responses.parse.side_effect = [parsed_response(draft) for draft in drafts]
        path = self.create()
        self.llm.responses.parse.assert_not_awaited()
        first = self.client.post(path + '/generate')
        self.assertEqual(first.status_code, 200)
        identifier = first.json()['conversation_id']
        sources = self.store.get(identifier).retrieved_context
        self.assertEqual(len(sources), 3)  # Canvas is an empty adapter until connected.
        self.assertEqual(first.json()['citations'], [item.model_dump(mode='json') for item in sources[:2]])
        restarted = make_session_service(self.container)
        with patch('main.reply_sessions', restarted):
            for feedback, draft in zip(['Mention the portal.', 'Make it shorter.'], drafts[1:]):
                revised = self.client.post(path + '/review', json={'feedback': feedback})
                self.assertEqual(revised.status_code, 200)
                self.assertEqual(revised.json()['status'], 'awaiting_review')
                self.assertEqual(revised.json()['revisions'][-1]['revision_content'], draft.reply)
                self.assertEqual(revised.json()['revisions'][-1]['feedback'], feedback)
                self.assertEqual(revised.json()['citations'], [item.model_dump(mode='json') for item in sources])
            accepted = self.client.post(path + '/accept')
            self.assertEqual(accepted.status_code, 200)
            self.assertEqual(accepted.json()['status'], 'accepted')
            self.assertEqual(self.client.post(path + '/accept').json(), accepted.json())
            self.assertEqual(self.client.get(path).json(), accepted.json())
        self.assertEqual(self.qa.documents[identifier]['accepted_response'], drafts[-1].reply)
        self.assertEqual(self.qa.documents[identifier]['revision_number'], 3)
        self.qa_client.create_item.assert_called_once()
        self.course_search.search.assert_called_once()
        self.web.assert_called_once()
        self.qa_client.query_items.assert_called_once()
        self.assertEqual(self.qa_client.query_items.call_args.kwargs['partition_key'], 'course-1')
        self.assertEqual(self.llm.responses.parse.await_count, 3)
        for request in self.llm.responses.parse.await_args_list:
            self.assertFalse(request.kwargs['store'])
            self.assertNotIn('conversation', request.kwargs)
            self.assertIn('[source_1]', request.kwargs['input'][0]['content'])
            self.assertIn('[source_3]', request.kwargs['input'][0]['content'])
        final_input = json.loads(self.llm.responses.parse.await_args.kwargs['input'][1]['content'])
        self.assertEqual(final_input['previous_reply'], drafts[1].reply)
        self.assertEqual(final_input['feedback_history'], ['Mention the portal.', 'Make it shorter.'])
        self.assertNotIn('retrieved_context', accepted.json())
        self.assertNotIn('cited_source_ids', accepted.json())

    def test_empty_retrieval_still_returns_a_general_answer(self):
        self.course_search.search.return_value = []
        self.qa_client.query_items.return_value = []
        with patch.object(public_website_search, '_get_settings', return_value={'public_website_urls': [], 'top_k': 3}):
            draft = ReplyDraft(reply='Recursion calls a function on smaller inputs until a base case.', status='answered', cited_source_ids=[])
            self.llm.responses.parse.side_effect = [parsed_response(draft)]
            response = self.client.post(self.create('What is recursion?') + '/generate')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['revisions'][0]['revision_content'], draft.reply)
        self.assertEqual(response.json()['citations'], [])
        self.assertEqual(self.llm.responses.parse.await_count, 1)

    def test_source_failure_keeps_other_sources_and_the_partial_answer(self):
        self.course_search.search.side_effect = RuntimeError('search unavailable')
        draft = ReplyDraft(reply='Declare AI use. The submission format is unavailable.', status='answered', cited_source_ids=['source_1'])
        self.llm.responses.parse.side_effect = [parsed_response(draft)]
        response = self.client.post(self.create() + '/generate')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['revisions'][0]['revision_content'], draft.reply)
        self.assertEqual(response.json()['citations'][0]['source'], 'university_policies')
        self.assertEqual(response.json()['citations'][0]['source_url'], 'https://policy.example.test/ai')
        self.assertEqual(self.llm.responses.parse.await_count, 1)
