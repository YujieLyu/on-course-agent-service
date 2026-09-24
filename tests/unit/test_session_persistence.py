import threading
import unittest
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, Mock, patch

from azure.cosmos.exceptions import CosmosHttpResponseError

from scripts import provision_cosmos_sessions
from src.config import REPLY_SESSION_RETENTION_SECONDS, load_cosmos_session_settings
from src.domain.reply_sessions import SessionConflictError, SessionNotFoundError, StorageUnavailableError
from src.models import ReplyResponse, SearchResult, SearchResults
from src.repositories.reply_sessions import validate_session_container
from tests.fakes import FakeSessionContainer, make_session_service


class DurableSessionTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.now = datetime(2026, 9, 22, tzinfo=UTC)
        self.container = FakeSessionContainer()
        self.store = make_session_service(self.container, clock=lambda: self.now)
        self.created = self.store.create('session-1', 'Question', 'course-1')
        self.source = SearchResult(source='course_materials', content='Submit a PDF.')

    async def test_restart_restores_revisions_sources_and_fixed_expiry(self):
        self.store.save_generation(self.store.load_snapshot('session-1'), 'Submit a PDF.',
                                   citations=[self.source], retrieved_context=[self.source])
        restarted = make_session_service(self.container, clock=lambda: self.now)
        self.now += timedelta(days=1)
        revised = restarted.save_revision(restarted.load_snapshot('session-1'), 'Shorter', 'PDF.', citations=[self.source])
        restored = self.store.get('session-1')
        self.assertEqual(restored, revised)
        self.assertEqual(restored.retrieved_context, (self.source,))
        self.assertEqual(restored.citations, (self.source,))
        self.assertEqual(restored.revisions[-1].feedback, 'Shorter')
        self.assertEqual(restored.expires_at, self.created.expires_at)
        self.assertEqual(self.container.documents['session-1']['ttl'], 29 * 86400)
        self.assertNotIn('intent_context', self.container.documents['session-1'])

    async def test_generation_and_review_each_use_one_read_and_one_atomic_write_off_loop(self):
        loop_thread = threading.get_ident()
        for reviewing in (False, True):
            threads = []
            original_read, original_write = self.container.read_item, self.container.replace_item
            def read(**kwargs):
                threads.append(threading.get_ident())
                return original_read(**kwargs)
            def write(**kwargs):
                threads.append(threading.get_ident())
                return original_write(**kwargs)
            with patch.object(self.container, 'read_item', side_effect=read) as reads, patch.object(
                self.container, 'replace_item', side_effect=write,
            ) as writes, patch('src.services.reply_sessions.search', new=AsyncMock(return_value=SearchResults(query='Question', results=[self.source]))) as search, patch(
                'src.services.reply_sessions.run_answer', new=AsyncMock(return_value=ReplyResponse(reply='PDF.', status='answered', citations=[self.source])),
            ):
                if reviewing:
                    result = await self.store.review('session-1', 'Shorter')
                    search.assert_not_awaited()
                else:
                    result = await self.store.generate('session-1')
                    search.assert_awaited_once_with('Question', 'course-1')
                self.assertEqual(result.citations, (self.source,))
                self.assertEqual(reads.call_count, 1)
                self.assertEqual(writes.call_count, 1)
                self.assertNotIn(loop_thread, threads)

    async def test_failed_initial_write_keeps_answer_and_sources_unsaved(self):
        with patch.object(self.container, 'replace_item', side_effect=CosmosHttpResponseError(status_code=503)):
            with self.assertRaises(StorageUnavailableError):
                self.store.save_generation(self.store.load_snapshot('session-1'), 'Draft',
                                           citations=[self.source], retrieved_context=[self.source])
        self.assertEqual(self.store.get('session-1'), self.created)

    async def test_stale_review_cannot_overwrite_a_newer_revision_or_acceptance(self):
        self.store.save_generation(self.store.load_snapshot('session-1'), 'Initial')
        stale = self.store.load_snapshot('session-1')
        self.store.save_revision(stale, 'First feedback', 'Winner')
        with self.assertRaises(SessionConflictError):
            self.store.save_revision(stale, 'Stale feedback', 'Stale reply')
        stale = self.store.load_snapshot('session-1')
        self.store.begin_acceptance(stale)
        with self.assertRaises(SessionConflictError):
            self.store.save_revision(stale, 'Late feedback', 'Late reply')
        self.assertEqual(self.store.get('session-1').revisions[-1].revision_content, 'Winner')

    async def test_expiry_during_generation_prevents_saving(self):
        snapshot = self.store.load_snapshot('session-1')
        self.now = self.created.expires_at
        with patch.object(self.container, 'replace_item', wraps=self.container.replace_item) as write:
            with self.assertRaises(SessionNotFoundError):
                self.store.save_generation(snapshot, 'Late reply')
            write.assert_not_called()

    async def test_expiry_between_transition_and_commit_prevents_writing(self):
        snapshot = self.store.load_snapshot('session-1')
        clock = iter([self.now, self.created.expires_at])
        self.store.clock = lambda: next(clock)
        with patch.object(self.container, 'replace_item', wraps=self.container.replace_item) as write:
            with self.assertRaises(SessionNotFoundError):
                self.store.save_generation(snapshot, 'Late reply')
            write.assert_not_called()


class SessionProvisioningTests(unittest.TestCase):
    def test_provisions_only_session_container_with_expected_ttl_and_partition(self):
        settings = {'endpoint': 'https://example.test', 'key': 'test-key', 'database_name': 'existing-db', 'container_name': 'reply_sessions'}
        client = Mock()
        database = client.get_database_client.return_value
        container = database.create_container_if_not_exists.return_value
        container.read.return_value = {'partitionKey': {'paths': ['/id']}, 'defaultTtl': REPLY_SESSION_RETENTION_SECONDS}
        with patch.object(provision_cosmos_sessions, 'load_cosmos_session_settings', return_value=settings), patch.object(
            provision_cosmos_sessions, 'CosmosClient',
        ) as constructor:
            constructor.return_value.__enter__.return_value = client
            provision_cosmos_sessions.main()
        client.get_database_client.assert_called_once_with('existing-db')
        args = database.create_container_if_not_exists.call_args.kwargs
        self.assertEqual(args['id'], 'reply_sessions')
        self.assertEqual(args['partition_key']['paths'], ['/id'])
        self.assertEqual(args['default_ttl'], 2592000)
        self.assertNotIn('vector_embedding_policy', args)
        client.create_database_if_not_exists.assert_not_called()

    def test_invalid_existing_container_cannot_silently_disable_retention(self):
        for properties in [
            {'partitionKey': {'paths': ['/course_id']}, 'defaultTtl': 2592000},
            {'partitionKey': {'paths': ['/id']}},
            {'partitionKey': {'paths': ['/id']}, 'defaultTtl': -1},
        ]:
            with self.subTest(properties=properties), self.assertRaises(RuntimeError):
                validate_session_container(properties)

    def test_session_container_cannot_be_the_historical_qna_container(self):
        with patch.dict('os.environ', {'AZURE_COSMOS_REPLY_SESSIONS_CONTAINER': 'qa_memory', 'AZURE_COSMOS_QA_MEMORY_CONTAINER': 'qa_memory'}):
            with self.assertRaises(ValueError):
                load_cosmos_session_settings()
