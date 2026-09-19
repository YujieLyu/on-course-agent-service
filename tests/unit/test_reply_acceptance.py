import asyncio
import threading
import unittest
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, Mock, patch

from azure.cosmos.exceptions import CosmosHttpResponseError
from fastapi.testclient import TestClient

from main import app
from src.domain.reply_sessions import (
    InvalidSessionActionError, SessionConflictError, SessionNotFoundError, StorageUnavailableError,
)
from src.models import SessionStatus
from src.repositories.historical_qna import CosmosQaMemoryRepository
from src.reply_sessions import ReplySessionStore
from src.services.historical_qna import HistoricalQnaPublisher
from src.services.reply_acceptance import accept_reply, recover_pending_acceptances
from tests.fakes import FakeSessionContainer, make_session_store


class AcceptanceTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.store = make_session_store()
        self.store.create('conv-1', 'Question', 'course-1')
        self.store.save_generation(self.store.load_snapshot('conv-1'), 'Reviewed draft')
        self.qa = FakeSessionContainer(partition_field='course_id')
        self.publisher = HistoricalQnaPublisher(
            CosmosQaMemoryRepository(self.qa), AsyncMock(return_value=[0.1]), 1,
        )

    async def test_claim_failure_cannot_publish_uncommitted_content(self):
        publish = AsyncMock()
        with patch.object(self.store.repository, 'replace', side_effect=StorageUnavailableError('offline')):
            with self.assertRaises(StorageUnavailableError):
                await accept_reply('conv-1', self.store, publish)
        publish.assert_not_awaited()
        self.assertEqual(self.store.get('conv-1').status, SessionStatus.AWAITING_REVIEW)

    async def test_publish_failure_survives_restart_and_preserves_the_acceptance_decision(self):
        with self.assertRaises(StorageUnavailableError):
            await accept_reply('conv-1', self.store, AsyncMock(side_effect=StorageUnavailableError('offline')))
        pending = self.store.load_snapshot('conv-1')
        self.assertEqual(pending.session.status, SessionStatus.ACCEPTING)
        self.assertEqual(pending.acceptance.accepted_response, 'Reviewed draft')
        restarted = ReplySessionStore(self.store.repository)
        accepted = await accept_reply('conv-1', restarted, self.publisher.publish)
        self.assertEqual(accepted.status, SessionStatus.ACCEPTED)
        self.assertEqual(restarted.load_snapshot('conv-1').acceptance, pending.acceptance)
        self.assertEqual(self.qa.documents['conv-1']['accepted_at'], pending.acceptance.accepted_at.isoformat().replace('+00:00', 'Z'))
        self.assertEqual(accepted.expires_at, pending.session.expires_at)

    async def test_finalization_failure_retries_without_rewriting_qa_or_acceptance_time(self):
        original_replace = self.store.repository.replace
        def fail_finalization(snapshot):
            if snapshot.session.status == SessionStatus.ACCEPTED:
                raise StorageUnavailableError('session write failed')
            return original_replace(snapshot)
        with patch.object(self.store.repository, 'replace', side_effect=fail_finalization):
            with self.assertRaises(StorageUnavailableError):
                await accept_reply('conv-1', self.store, self.publisher.publish)
        pending = self.store.load_snapshot('conv-1')
        self.assertEqual(pending.session.status, SessionStatus.ACCEPTING)
        previous_qna = self.qa.documents['conv-1'].copy()
        result = await accept_reply('conv-1', self.store, self.publisher.publish)
        self.assertEqual(result.status, SessionStatus.ACCEPTED)
        self.assertEqual(self.qa.documents['conv-1'], previous_qna)
        # Repeating a completed acceptance does not call the publisher again.
        publish = AsyncMock()
        self.assertEqual(await accept_reply('conv-1', self.store, publish), result)
        publish.assert_not_awaited()

    async def test_lost_write_acknowledgements_are_recoverable_at_every_step(self):
        for step in ('claim', 'qa', 'complete'):
            with self.subTest(step=step):
                store = make_session_store()
                store.create('conv-1', 'Question', 'course-1')
                store.save_generation(store.load_snapshot('conv-1'), 'Reviewed draft')
                qa = FakeSessionContainer(partition_field='course_id')
                publisher = HistoricalQnaPublisher(CosmosQaMemoryRepository(qa), AsyncMock(return_value=[0.1]), 1)
                original_replace = store.repository.replace
                original_create = qa.create_item
                def lost_session_ack(snapshot):
                    result = original_replace(snapshot)
                    target = SessionStatus.ACCEPTING if step == 'claim' else SessionStatus.ACCEPTED
                    if step != 'qa' and snapshot.session.status == target:
                        raise StorageUnavailableError('response lost after commit')
                    return result
                def lost_qa_ack(**kwargs):
                    original_create(**kwargs)
                    if step == 'qa':
                        raise CosmosHttpResponseError(status_code=503)
                with patch.object(store.repository, 'replace', side_effect=lost_session_ack), patch.object(
                    qa, 'create_item', side_effect=lost_qa_ack,
                ):
                    with self.assertRaises(StorageUnavailableError):
                        await accept_reply('conv-1', store, publisher.publish)
                self.assertIn(store.get('conv-1').status, (SessionStatus.ACCEPTING, SessionStatus.ACCEPTED))
                result = await accept_reply('conv-1', ReplySessionStore(store.repository), publisher.publish)
                self.assertEqual(result.status, SessionStatus.ACCEPTED)
                self.assertEqual(qa.documents['conv-1']['accepted_response'], 'Reviewed draft')
                self.assertEqual(len(qa.documents), 1)

    async def test_review_winning_the_claim_race_prevents_the_old_answer_from_being_published(self):
        entered = threading.Event()
        release = threading.Event()
        original_replace = self.store.repository.replace
        def delayed_claim(snapshot):
            if snapshot.session.status == SessionStatus.ACCEPTING:
                entered.set()
                if not release.wait(5):
                    raise TimeoutError('test claim gate')
            return original_replace(snapshot)
        publish = AsyncMock(wraps=self.publisher.publish)
        with patch.object(self.store.repository, 'replace', side_effect=delayed_claim):
            task = asyncio.create_task(accept_reply('conv-1', self.store, publish))
            try:
                self.assertTrue(await asyncio.to_thread(entered.wait, 5))
                self.store.request_revision('conv-1', 'Replace the draft')
                self.store.save_generation(self.store.load_snapshot('conv-1'), 'New reviewed draft')
            finally:
                release.set()
            with self.assertRaises(SessionConflictError):
                await task
        publish.assert_not_awaited()
        self.assertEqual(self.qa.documents, {})
        await accept_reply('conv-1', self.store, publish)
        self.assertEqual(self.qa.documents['conv-1']['accepted_response'], 'New reviewed draft')

    async def test_acceptance_lock_blocks_revision_and_concurrent_publishers_converge(self):
        entered = asyncio.Event()
        release = asyncio.Event()
        async def slow_publish(decision):
            entered.set()
            await release.wait()
            await self.publisher.publish(decision)
        slow = asyncio.create_task(accept_reply('conv-1', self.store, slow_publish))
        try:
            await asyncio.wait_for(entered.wait(), 5)
            snapshot = self.store.load_snapshot('conv-1')
            with self.assertRaises(InvalidSessionActionError):
                self.store.request_revision('conv-1', 'Change it')
            with self.assertRaises(InvalidSessionActionError):
                self.store.save_generation(snapshot, 'Unapproved draft')
            fast_result = await accept_reply('conv-1', self.store, self.publisher.publish)
            qa_after_fast = self.qa.documents['conv-1'].copy()
        finally:
            release.set()
        slow_result = await slow
        self.assertEqual(fast_result, slow_result)
        self.assertEqual(self.qa.documents['conv-1'], qa_after_fast)
        self.assertEqual(slow_result.status, SessionStatus.ACCEPTED)

    async def test_cancelled_publication_is_resumable(self):
        entered = asyncio.Event()
        async def paused_publish(_decision):
            entered.set()
            await asyncio.Event().wait()
        task = asyncio.create_task(accept_reply('conv-1', self.store, paused_publish))
        await asyncio.wait_for(entered.wait(), 5)
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task
        self.assertEqual(self.store.get('conv-1').status, SessionStatus.ACCEPTING)
        result = await accept_reply('conv-1', self.store, self.publisher.publish)
        self.assertEqual(result.status, SessionStatus.ACCEPTED)

    async def test_recovery_continues_after_one_failure_and_never_accepts_an_unapproved_draft(self):
        self.store.begin_acceptance(self.store.load_snapshot('conv-1'))
        for identifier in ('conv-2', 'unapproved'):
            self.store.create(identifier, 'Question', 'course-1')
            self.store.save_generation(self.store.load_snapshot(identifier), 'Reviewed draft')
        self.store.begin_acceptance(self.store.load_snapshot('conv-2'))
        async def publish(decision):
            if decision.conversation_id == 'conv-1':
                raise StorageUnavailableError('dependency failed')
            await self.publisher.publish(decision)
        with patch('src.services.reply_acceptance.logger.exception'):
            result = await recover_pending_acceptances(self.store.repository, self.store, publish)
        self.assertEqual((result.completed, result.failed), (1, ('conv-1',)))
        self.assertEqual(self.store.get('unapproved').status, SessionStatus.AWAITING_REVIEW)
        restarted = ReplySessionStore(self.store.repository)
        result = await recover_pending_acceptances(self.store.repository, restarted, self.publisher.publish)
        self.assertEqual((result.completed, result.failed), (1, ()))
        self.assertEqual(set(self.qa.documents), {'conv-1', 'conv-2'})

    async def test_pending_acceptance_does_not_extend_retention(self):
        now = datetime(2026, 9, 19, tzinfo=UTC)
        store = make_session_store(clock=lambda: now)
        created = store.create('expiring', 'Question', 'course-1')
        store.save_generation(store.load_snapshot('expiring'), 'Draft')
        now = created.expires_at - timedelta(seconds=1)
        store.begin_acceptance(store.load_snapshot('expiring'))
        now = created.expires_at
        publish = AsyncMock()
        with self.assertRaises(SessionNotFoundError):
            await accept_reply('expiring', store, publish)
        publish.assert_not_awaited()
        result = await recover_pending_acceptances(store.repository, store, publish)
        self.assertEqual(result.expired, 1)
        self.assertEqual(result.failed, ())

    async def test_recovery_rejects_an_unapproved_candidate_even_if_the_scanner_returns_it(self):
        source = Mock()
        source.pending_ids.return_value = ['conv-1']
        publish = AsyncMock()
        with patch('src.services.reply_acceptance.logger.exception'):
            result = await recover_pending_acceptances(source, self.store, publish)
        self.assertEqual(result.failed, ('conv-1',))
        publish.assert_not_awaited()
        self.assertEqual(self.store.get('conv-1').status, SessionStatus.AWAITING_REVIEW)


class AcceptanceApiTests(unittest.TestCase):
    def test_pending_acceptance_can_be_observed_and_retried_through_the_api(self):
        store = make_session_store()
        store.create('conv-api', 'Question', 'course-1')
        store.save_generation(store.load_snapshot('conv-api'), 'Draft')
        publish = AsyncMock(side_effect=[StorageUnavailableError('offline'), None])
        with patch('main.reply_sessions', store), patch('main.save_accepted_qna', new=publish), patch('main.logger.exception'):
            client = TestClient(app)
            base = '/reply-sessions/conv-api'
            self.assertEqual(client.post(f'{base}/accept').status_code, 503)
            pending = client.get(base)
            self.assertEqual(pending.json()['status'], 'accepting')
            self.assertNotIn('acceptance', pending.json())
            self.assertNotIn('version', pending.json())
            self.assertEqual(client.post(f'{base}/review', json={'feedback': 'Change it'}).status_code, 409)
            self.assertEqual(client.post(f'{base}/generate').status_code, 409)
            accepted = client.post(f'{base}/accept')
            self.assertEqual(accepted.status_code, 200)
            self.assertEqual(accepted.json()['status'], 'accepted')
            self.assertEqual(client.post(f'{base}/accept').json(), accepted.json())
        self.assertEqual(publish.await_count, 2)
        self.assertEqual(publish.await_args_list[0].args[0], publish.await_args_list[1].args[0])
