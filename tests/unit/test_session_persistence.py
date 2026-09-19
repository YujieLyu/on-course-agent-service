import threading
import unittest
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, Mock, patch

from azure.cosmos.exceptions import CosmosHttpResponseError
from fastapi.testclient import TestClient

from main import app, generate_session_reply
from scripts import provision_cosmos_sessions
from src.config import REPLY_SESSION_RETENTION_SECONDS, load_cosmos_session_settings
from src.domain.reply_sessions import SessionConflictError, SessionNotFoundError, StorageUnavailableError
from src.models import IntentClassification, ReplyResponse, SearchResult, SearchSource, StudentIntent
from src.repositories.reply_sessions import validate_session_container
from tests.fakes import FakeSessionContainer, generation_result, make_session_store


class DurableSessionTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.now = datetime(2026, 9, 19, tzinfo=UTC)
        self.container = FakeSessionContainer()
        self.store = make_session_store(self.container, clock=lambda: self.now)
        self.session = self.store.create('local_durable', 'What is due?', 'course-1', 'student-1')

    async def test_another_store_restores_all_revisions_and_evidence(self):
        intent = IntentClassification(
            intent=StudentIntent.ASSESSMENT_REQUIREMENTS, search_sources=[SearchSource.COURSE_MATERIALS],
            needs_clarification=False,
        )
        source = SearchResult(source=SearchSource.COURSE_MATERIALS, content='The report is due Friday.')
        self.store.save_generation(self.store.load_snapshot('local_durable'), 'Friday.', evidence=(intent, [source]))
        restarted = make_session_store(self.container, clock=lambda: self.now)
        snapshot = restarted.load_snapshot('local_durable')
        self.assertEqual((snapshot.intent_context, snapshot.retrieved_context), (intent, (source,)))
        restarted.request_revision('local_durable', 'Include the assessment name.')
        another_worker = make_session_store(self.container, clock=lambda: self.now)
        another_worker.save_generation(another_worker.load_snapshot('local_durable'), 'The report is due Friday.')
        self.assertEqual(self.store.get('local_durable'), another_worker.get('local_durable'))
        self.assertEqual(len(self.store.get('local_durable').revisions), 2)
        self.assertEqual(self.store.get('local_durable').revisions[-1].feedback, 'Include the assessment name.')
        self.assertNotIn('_etag', snapshot.session.model_dump())
        self.assertFalse(hasattr(snapshot.session, '_etag'))

    async def test_fixed_expiry_is_not_extended_by_revision_or_acceptance(self):
        self.assertEqual(self.session.expires_at - self.session.created_at, timedelta(days=30))
        for elapsed_days, operation in [
            (10, lambda: self.store.save_generation(self.store.load_snapshot('local_durable'), 'Draft')),
            (20, lambda: self.store.request_revision('local_durable', 'Shorter')),
            (21, lambda: self.store.save_generation(self.store.load_snapshot('local_durable'), 'Short draft')),
            (28, lambda: self.store.begin_acceptance(self.store.load_snapshot('local_durable'))),
            (29, lambda: self.store.complete_acceptance(self.store.load_snapshot('local_durable'))),
        ]:
            self.now = self.session.created_at + timedelta(days=elapsed_days)
            operation()
            saved = self.store.get('local_durable')
            self.assertEqual(saved.expires_at, self.session.expires_at)
            self.assertEqual(self.container.documents['local_durable']['ttl'], (30 - elapsed_days) * 86400)

    async def test_expired_snapshot_cannot_be_saved_even_without_another_read(self):
        snapshot = self.store.load_snapshot('local_durable')
        self.now = self.session.expires_at
        for operation in [
            lambda: self.store.get('local_durable'),
            lambda: self.store.load_snapshot('local_durable'),
            lambda: self.store.save_generation(snapshot, 'Late draft'),
            lambda: self.store.request_revision('local_durable', 'Late feedback'),
        ]:
            with self.assertRaises(SessionNotFoundError):
                operation()
        self.assertEqual(self.container.documents['local_durable']['revisions'], [])

    async def test_failed_write_preserves_snapshot_and_previous_draft(self):
        snapshot = self.store.load_snapshot('local_durable')
        before = self.store.get('local_durable')
        with patch.object(self.container, 'replace_item', side_effect=CosmosHttpResponseError(status_code=503)):
            with self.assertRaises(StorageUnavailableError):
                self.store.save_generation(snapshot, 'Draft')
        self.assertEqual(snapshot.session, before)
        self.assertEqual(self.store.get('local_durable'), before)

    async def test_initial_answer_and_evidence_are_saved_in_the_same_write(self):
        generation = generation_result(ReplyResponse(reply='Draft', status='answered', citations=[]))
        with patch.object(self.container, 'replace_item', side_effect=CosmosHttpResponseError(status_code=503)):
            with self.assertRaises(StorageUnavailableError):
                self.store.save_generation(
                    self.store.load_snapshot('local_durable'), generation.response.reply,
                    evidence=(generation.intent, generation.retrieved_context),
                )
        restored = self.store.load_snapshot('local_durable')
        self.assertEqual(restored.session.revisions, [])
        self.assertEqual((restored.intent_context, restored.retrieved_context), (None, ()))

    async def test_stale_generation_cannot_overwrite_a_new_revision_without_rereading(self):
        stale = self.store.load_snapshot('local_durable')
        self.store.save_generation(stale, 'First winner')
        self.store.request_revision('local_durable', 'Revise this winner')
        with patch.object(self.container, 'read_item', wraps=self.container.read_item) as read:
            with self.assertRaises(SessionConflictError):
                self.store.save_generation(stale, 'Stale result')
            read.assert_not_called()
        self.assertIsNone(self.store.get('local_durable').revisions[-1].revision_content)

    async def test_conditional_write_conflicts_are_not_silently_retried(self):
        self.store.save_generation(self.store.load_snapshot('local_durable'), 'Draft')
        with patch.object(self.container, 'replace_item', side_effect=CosmosHttpResponseError(status_code=412)) as write:
            with self.assertRaises(SessionConflictError):
                self.store.request_revision('local_durable', 'Feedback')
            write.assert_called_once()
        self.assertEqual(self.store.get('local_durable').status, 'awaiting_review')

    async def test_initial_generation_and_revision_each_read_once_and_write_once_off_event_loop(self):
        event_loop_thread = threading.get_ident()
        for is_revision in (False, True):
            if is_revision:
                self.store.request_revision('local_durable', 'Shorter')
            threads = []
            original_read = self.container.read_item
            original_replace = self.container.replace_item
            def read(**kwargs):
                threads.append(threading.get_ident())
                return original_read(**kwargs)
            def write(**kwargs):
                threads.append(threading.get_ident())
                return original_replace(**kwargs)
            response = ReplyResponse(reply='Draft', status='answered', citations=[])
            with patch('main.reply_sessions', self.store), patch(
                'main.run_orchestration', new=AsyncMock(return_value=generation_result(response)),
            ), patch('src.agents.orchestrator.run_answer', new=AsyncMock(return_value=response)), patch.object(
                self.container, 'read_item', side_effect=read,
            ) as reads, patch.object(self.container, 'replace_item', side_effect=write) as writes:
                result = await generate_session_reply('local_durable')
                self.assertEqual(result.status, 'awaiting_review')
                self.assertEqual(reads.call_count, 1)
                self.assertEqual(writes.call_count, 1)
                self.assertNotIn(event_loop_thread, threads)


class SessionPersistenceApiTests(unittest.TestCase):
    def test_unknown_and_expired_sessions_return_404(self):
        now = datetime(2026, 9, 19, tzinfo=UTC)
        store = make_session_store(clock=lambda: now)
        session = store.create('local_expired', 'Question', 'c1')
        now = session.expires_at
        with patch('main.reply_sessions', store):
            client = TestClient(app)
            for method, path, body in [
                ('GET', '/reply-sessions/local_expired', None),
                ('GET', '/reply-sessions/missing', None),
                ('POST', '/reply-sessions/local_expired/generate', None),
                ('POST', '/reply-sessions/local_expired/review', {'feedback': 'Shorter'}),
                ('POST', '/reply-sessions/local_expired/accept', None),
            ]:
                result = client.request(method, path, **({'json': body} if body else {}))
                self.assertEqual(result.status_code, 404)

    def test_database_outage_returns_503_for_reads_and_feedback(self):
        store = make_session_store()
        with patch('main.reply_sessions', store), patch.object(
            store.repository, 'read', side_effect=StorageUnavailableError('private database failure'),
        ), patch('main.logger.exception'):
            client = TestClient(app)
            for result in [
                client.get('/reply-sessions/conv_1'),
                client.post('/reply-sessions/conv_1/review', json={'feedback': 'Shorter'}),
            ]:
                self.assertEqual(result.status_code, 503)
                self.assertNotIn('private database failure', result.text)


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
