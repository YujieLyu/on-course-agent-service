"""Deterministic HTTP fault cases that cannot be triggered by a public body."""

import itertools
import unittest
from unittest.mock import AsyncMock, patch

from fastapi.testclient import TestClient

from main import app
from src.domain.reply_sessions import QaMemoryConflictError, SessionConflictError, StorageUnavailableError
from src.models import ReplyResponse, SessionStatus
from tests.fakes import generation_result, make_session_service


class ApiFailureContractTests(unittest.TestCase):
    def setUp(self):
        self.store = make_session_service()
        self.ids = itertools.count(1)
        self.enterContext(patch('main.reply_sessions', self.store))
        self.create = self.enterContext(patch('main.create_conversation', new=AsyncMock(return_value='created')))
        reply = ReplyResponse(reply='A test draft.', status='answered', citations=[])
        self.generate = self.enterContext(patch('main.run_orchestration', new=AsyncMock(return_value=generation_result(reply))))
        self.revise = self.enterContext(patch('main.generate_session_revision', new=AsyncMock(return_value=reply)))
        self.publish = self.enterContext(patch('main.save_accepted_qna', new=AsyncMock()))
        self.enterContext(patch('main.logger.exception'))
        self.client = self.enterContext(TestClient(app))

    def session(self, status=SessionStatus.PENDING_GENERATION):
        session_id = f'fault_contract_{next(self.ids)}'
        self.store.create(session_id, 'When is Assignment 1 due?', 'test-course')
        if status != SessionStatus.PENDING_GENERATION:
            self.store.save_generation(self.store.load_snapshot(session_id), 'Original draft.')
        if status == SessionStatus.REVISION_REQUESTED:
            self.store.request_revision(session_id, 'Make it shorter.')
        elif status in (SessionStatus.ACCEPTING, SessionStatus.ACCEPTED):
            claimed = self.store.begin_acceptance(self.store.load_snapshot(session_id))
            if status == SessionStatus.ACCEPTED:
                self.store.complete_acceptance(claimed)
        return f'/reply-sessions/{session_id}'

    def assert_error(self, response, code, detail):
        self.assertEqual(response.status_code, code, response.text)
        self.assertEqual(response.json(), {'detail': detail})
        self.assertRegex(response.headers['X-Request-ID'], r'^[A-Za-z0-9_.-]{1,128}$')

    def test_creation_provider_and_storage_failures_return_sanitized_503(self):
        payload = {'question': 'Question', 'course_id': 'test-course'}
        with patch('main.create_conversation', new=AsyncMock(side_effect=RuntimeError('private provider failure'))):
            self.assert_error(self.client.post('/reply-sessions', json=payload), 503,
                              'The service could not create a session')
        with patch.object(self.store.repository, 'create', side_effect=StorageUnavailableError('private database failure')) as create:
            self.assert_error(self.client.post('/reply-sessions', json=payload), 503,
                              'The service could not create a session')
            create.assert_called_once()
        self.assertEqual(self.store.repository.snapshots, {})

    def test_unavailable_storage_returns_endpoint_specific_503(self):
        path = self.session()
        cases = [
            ('GET', '', None, 'The service could not retrieve the session'),
            ('POST', '/generate', None, 'The service could not generate a reply'),
            ('POST', '/review', {'feedback': 'Shorter.'}, 'The service could not save the feedback'),
            ('POST', '/accept', None, 'The service could not accept the reply'),
        ]
        with patch.object(self.store.repository, 'read', side_effect=StorageUnavailableError('private database failure')):
            for method, suffix, body, detail in cases:
                with self.subTest(suffix=suffix):
                    kwargs = {'json': body} if body is not None else {}
                    self.assert_error(self.client.request(method, path + suffix, **kwargs), 503, detail)
        self.generate.assert_not_awaited()
        self.revise.assert_not_awaited()
        self.publish.assert_not_awaited()

    def test_initial_and_revision_generation_failures_preserve_state_then_retry(self):
        for state, dependency in [(SessionStatus.PENDING_GENERATION, self.generate),
                                  (SessionStatus.REVISION_REQUESTED, self.revise)]:
            with self.subTest(state=state):
                path = self.session(state)
                before = self.client.get(path).json()
                dependency.side_effect = [RuntimeError('private generation failure'), dependency.return_value]
                self.assert_error(self.client.post(path + '/generate'), 503,
                                  'The service could not generate a reply')
                self.assertEqual(self.client.get(path).json(), before)
                retried = self.client.post(path + '/generate')
                self.assertEqual(retried.status_code, 200)
                self.assertEqual(retried.json()['status'], 'awaiting_review')
                self.assertEqual(retried.json()['revisions'][-1]['revision_content'], 'A test draft.')

    def test_failed_review_write_does_not_append_feedback(self):
        path = self.session(SessionStatus.AWAITING_REVIEW)
        before = self.client.get(path).json()
        with patch.object(self.store.repository, 'replace', side_effect=StorageUnavailableError('private write failure')):
            self.assert_error(self.client.post(path + '/review', json={'feedback': 'Shorter.'}), 503,
                              'The service could not save the feedback')
        self.assertEqual(self.client.get(path).json(), before)

    def test_conditional_write_conflicts_cannot_overwrite_session_or_publish(self):
        for state, suffix, body in [
            (SessionStatus.PENDING_GENERATION, '/generate', None),
            (SessionStatus.REVISION_REQUESTED, '/generate', None),
            (SessionStatus.AWAITING_REVIEW, '/review', {'feedback': 'Shorter.'}),
            (SessionStatus.AWAITING_REVIEW, '/accept', None),
        ]:
            with self.subTest(state=state, suffix=suffix):
                path = self.session(state)
                before = self.client.get(path).json()
                detail = 'Session changed; fetch it before retrying'
                with patch.object(self.store.repository, 'replace', side_effect=SessionConflictError(detail)):
                    kwargs = {'json': body} if body is not None else {}
                    self.assert_error(self.client.post(path + suffix, **kwargs), 409, detail)
                self.assertEqual(self.client.get(path).json(), before)
        self.publish.assert_not_awaited()

    def test_failed_publication_locks_review_and_generation_until_accept_retry(self):
        path = self.session(SessionStatus.AWAITING_REVIEW)
        before = self.client.get(path).json()
        self.publish.side_effect = [RuntimeError('private QA failure'), None]
        self.assert_error(self.client.post(path + '/accept'), 503, 'The service could not accept the reply')
        self.assertEqual(self.client.get(path).json(), {**before, 'status': 'accepting'})
        self.assert_error(self.client.post(path + '/generate'), 409, 'Session is not ready for generation')
        self.assert_error(self.client.post(path + '/review', json={'feedback': 'Too late.'}),
                          409, 'Session is not awaiting review')
        accepted = self.client.post(path + '/accept')
        self.assertEqual(accepted.status_code, 200)
        self.assertEqual(accepted.json(), {**before, 'status': 'accepted'})
        self.assertEqual(self.publish.await_count, 2)
        repeated = self.client.post(path + '/accept')
        self.assertEqual(repeated.status_code, 200)
        self.assertEqual(repeated.json(), accepted.json())
        self.assertEqual(self.publish.await_count, 2)

    def test_qa_memory_version_conflict_keeps_acceptance_pending(self):
        path = self.session(SessionStatus.AWAITING_REVIEW)
        detail = 'A newer accepted revision already exists in QA Memory'
        self.publish.side_effect = QaMemoryConflictError(detail)
        self.assert_error(self.client.post(path + '/accept'), 409, detail)
        self.assertEqual(self.client.get(path).json()['status'], 'accepting')

    def test_acceptance_claim_and_finalization_outages_remain_recoverable(self):
        for failed_state, expected_state in [(SessionStatus.ACCEPTING, 'awaiting_review'),
                                             (SessionStatus.ACCEPTED, 'accepting')]:
            with self.subTest(failed_state=failed_state):
                path = self.session(SessionStatus.AWAITING_REVIEW)
                before = self.client.get(path).json()
                original_replace = self.store.repository.replace

                def fail_selected_write(snapshot):
                    if snapshot.session.status == failed_state:
                        raise StorageUnavailableError('private write failure')
                    return original_replace(snapshot)

                self.publish.reset_mock()
                with patch.object(self.store.repository, 'replace', side_effect=fail_selected_write):
                    self.assert_error(self.client.post(path + '/accept'), 503, 'The service could not accept the reply')
                self.assertEqual(self.client.get(path).json(), {**before, 'status': expected_state})
                self.assertEqual(self.publish.await_count, 0 if failed_state == SessionStatus.ACCEPTING else 1)
                retried = self.client.post(path + '/accept')
                self.assertEqual(retried.status_code, 200)
                self.assertEqual(retried.json(), {**before, 'status': 'accepted'})

    def test_expiry_at_exact_deadline_rejects_every_session_operation(self):
        path = self.session(SessionStatus.AWAITING_REVIEW)
        session_id = path.rsplit('/', 1)[1]
        self.store.clock = lambda: self.store.repository.read(session_id).session.expires_at
        for method, suffix, body in [('GET', '', None), ('POST', '/generate', None),
                                     ('POST', '/review', {'feedback': 'Shorter.'}), ('POST', '/accept', None)]:
            with self.subTest(suffix=suffix):
                kwargs = {'json': body} if body is not None else {}
                self.assert_error(self.client.request(method, path + suffix, **kwargs), 404, 'Reply session not found')
        self.generate.assert_not_awaited()
        self.revise.assert_not_awaited()
        self.publish.assert_not_awaited()


if __name__ == '__main__':
    unittest.main()
