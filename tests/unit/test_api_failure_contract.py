import unittest
from unittest.mock import AsyncMock, patch

from fastapi.testclient import TestClient

from main import app
from src.domain.reply_sessions import SessionConflictError, StorageUnavailableError
from src.models import ReplyResponse, SearchResults
from tests.fakes import make_session_service


class ApiFailureContractTests(unittest.TestCase):
    def setUp(self):
        self.store = make_session_service()
        self.answer = AsyncMock(return_value=ReplyResponse(reply='A test draft.', status='answered', citations=[]))
        self.search = AsyncMock(return_value=SearchResults(query='Question', results=[]))
        self.publish = AsyncMock()
        for target, value in [
            ('main.reply_sessions', self.store), ('main.save_accepted_qna', self.publish),
            ('src.services.reply_sessions.run_answer', self.answer), ('src.services.reply_sessions.search', self.search),
        ]:
            patcher = patch(target, value)
            patcher.start()
            self.addCleanup(patcher.stop)
        self.client = TestClient(app)
        self.counter = 0

    def session(self, generated=False):
        self.counter += 1
        identifier = f'test-{self.counter}'
        self.store.create(identifier, 'Question', 'test-course')
        if generated:
            self.store.save_generation(self.store.load_snapshot(identifier), 'Previous draft')
        return f'/reply-sessions/{identifier}'

    def assert_error(self, response, code, detail):
        self.assertEqual(response.status_code, code, response.text)
        self.assertEqual(response.json(), {'detail': detail})
        self.assertIn('X-Request-ID', response.headers)

    def test_creation_uses_local_ids_and_only_needs_storage(self):
        with patch('src.workflow.llm.create_openai_client') as llm:
            created = self.client.post('/reply-sessions', json={'question': 'Question', 'course_id': 'course-1'})
        self.assertEqual(created.status_code, 201)
        self.assertRegex(created.json()['conversation_id'], r'^[a-f0-9]{32}$')
        self.assertEqual(created.json()['status'], 'pending_generation')
        llm.assert_not_called()
        with patch.object(self.store.repository, 'create', side_effect=StorageUnavailableError('private database failure')):
            self.assert_error(self.client.post('/reply-sessions', json={'question': 'Question', 'course_id': 'c1'}),
                              503, 'The service could not create a session')

    def test_invalid_input_and_unknown_sessions_do_not_call_dependencies(self):
        for body in [{}, {'question': '', 'course_id': 'c1'}, {'question': 'Question', 'course_id': None}]:
            self.assertEqual(self.client.post('/reply-sessions', json=body).status_code, 422)
        for suffix in ['/generate', '/review', '/accept']:
            body = {'feedback': 'Shorter'} if suffix == '/review' else None
            self.assert_error(self.client.post('/reply-sessions/missing' + suffix, json=body), 404, 'Reply session not found')
        self.answer.assert_not_awaited()
        self.search.assert_not_awaited()
        self.publish.assert_not_awaited()

    def test_unavailable_storage_returns_safe_endpoint_errors(self):
        path = self.session()
        with patch.object(self.store.repository, 'read', side_effect=StorageUnavailableError('private database failure')):
            for method, suffix, body, detail in [
                ('GET', '', None, 'The service could not retrieve the session'),
                ('POST', '/generate', None, 'The service could not generate a reply'),
                ('POST', '/review', {'feedback': 'Shorter'}, 'The service could not revise the reply'),
                ('POST', '/accept', None, 'The service could not accept the reply'),
            ]:
                with self.subTest(suffix=suffix):
                    self.assert_error(self.client.request(method, path + suffix, json=body), 503, detail)
        self.answer.assert_not_awaited()
        self.publish.assert_not_awaited()

    def test_generation_and_review_failures_preserve_state_and_allow_retry(self):
        for reviewing in (False, True):
            with self.subTest(reviewing=reviewing):
                path = self.session(generated=reviewing)
                suffix = '/review' if reviewing else '/generate'
                body = {'feedback': 'Shorter'} if reviewing else None
                before = self.client.get(path).json()
                self.answer.side_effect = [RuntimeError('private model failure'), self.answer.return_value]
                failed = self.client.post(path + suffix, json=body)
                self.assertEqual(failed.status_code, 503)
                self.assertNotIn('private', failed.text)
                self.assertEqual(self.client.get(path).json(), before)
                retried = self.client.post(path + suffix, json=body)
                self.assertEqual(retried.status_code, 200)
                self.assertEqual(retried.json()['status'], 'awaiting_review')
                self.assertEqual(len(retried.json()['revisions']), 2 if reviewing else 1)
                if reviewing:
                    self.assertEqual(retried.json()['revisions'][-1]['feedback'], 'Shorter')

    def test_failed_or_conflicting_write_does_not_append_partial_revision(self):
        for error, code in [(StorageUnavailableError('private write failure'), 503), (SessionConflictError('Session changed'), 409)]:
            for reviewing in (False, True):
                with self.subTest(code=code, reviewing=reviewing):
                    path = self.session(generated=reviewing)
                    before = self.client.get(path).json()
                    with patch.object(self.store.repository, 'replace', side_effect=error):
                        response = self.client.post(path + ('/review' if reviewing else '/generate'),
                                                    json={'feedback': 'Shorter'} if reviewing else None)
                    self.assertEqual(response.status_code, code)
                    self.assertEqual(self.client.get(path).json(), before)

    def test_accept_claim_conflict_never_publishes(self):
        path = self.session(generated=True)
        with patch.object(self.store.repository, 'replace', side_effect=SessionConflictError('Session changed')):
            self.assert_error(self.client.post(path + '/accept'), 409, 'Session changed')
        self.publish.assert_not_awaited()

    def test_invalid_states_reject_generation_and_review_before_calling_llm(self):
        path = self.session()
        self.assert_error(self.client.post(path + '/review', json={'feedback': 'Shorter'}), 409, 'Session is not awaiting review')
        identifier = path.rsplit('/', 1)[1]
        self.store.save_generation(self.store.load_snapshot(identifier), 'Draft')
        self.assert_error(self.client.post(path + '/generate'), 409, 'Session is not ready for generation')
        self.client.post(path + '/accept')
        self.assert_error(self.client.post(path + '/review', json={'feedback': 'Shorter'}), 409, 'Session is not awaiting review')
        self.answer.assert_not_awaited()
        self.search.assert_not_awaited()

    def test_expired_sessions_reject_all_operations(self):
        path = self.session(generated=True)
        identifier = path.rsplit('/', 1)[1]
        self.store.clock = lambda: self.store.repository.read(identifier).session.expires_at
        for method, suffix, body in [('GET', '', None), ('POST', '/generate', None),
                                     ('POST', '/review', {'feedback': 'Shorter'}), ('POST', '/accept', None)]:
            self.assert_error(self.client.request(method, path + suffix, json=body), 404, 'Reply session not found')
        self.answer.assert_not_awaited()
        self.publish.assert_not_awaited()
