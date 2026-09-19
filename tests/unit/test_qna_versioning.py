import unittest
from unittest.mock import patch

from azure.cosmos.exceptions import CosmosHttpResponseError

from src.domain.reply_sessions import AcceptedQna, QaMemoryConflictError, StorageUnavailableError
from src.repositories.historical_qna import CosmosQaMemoryRepository
from tests.fakes import FakeSessionContainer
from tests.unit.test_historical_qna import accepted_reply


class QaMemoryVersioningTests(unittest.TestCase):
    def setUp(self):
        self.container = FakeSessionContainer(partition_field='course_id')
        self.repository = CosmosQaMemoryRepository(self.container)
        self.original = AcceptedQna(accepted_reply(), (0.1,))

    def test_same_revision_retry_does_not_rewrite_timestamps_or_vectors(self):
        self.repository.save(self.original)
        before = self.container.documents['conv-1'].copy()
        with patch.object(self.container, 'replace_item', wraps=self.container.replace_item) as replace:
            self.repository.save(AcceptedQna(self.original.reply, (0.9,)))
            replace.assert_not_called()
        self.assertEqual(self.container.documents['conv-1'], before)

    def test_newer_revision_is_conditional_and_older_revision_can_never_overwrite_it(self):
        self.repository.save(self.original)
        newer = AcceptedQna(accepted_reply(revision_number=2, accepted_response='Monday.'), (0.2,))
        self.repository.save(newer)
        with self.assertRaises(QaMemoryConflictError):
            self.repository.save(self.original)
        self.assertEqual(self.container.documents['conv-1']['accepted_response'], 'Monday.')

    def test_same_revision_with_different_content_is_rejected(self):
        self.repository.save(self.original)
        with self.assertRaises(QaMemoryConflictError):
            self.repository.save(AcceptedQna(accepted_reply(accepted_response='Different answer'), (0.2,)))
        self.assertEqual(self.container.documents['conv-1']['accepted_response'], 'Friday.')

    def test_replace_race_rereads_version_instead_of_overwriting_a_newer_winner(self):
        self.repository.save(self.original)
        original_replace = self.container.replace_item
        def race(**kwargs):
            winner = {**kwargs['body'], 'revision_number': 3, 'accepted_response': 'Newest draft'}
            original_replace(**{**kwargs, 'body': winner})
            raise CosmosHttpResponseError(status_code=412)
        with patch.object(self.container, 'replace_item', side_effect=race):
            with self.assertRaises(QaMemoryConflictError):
                self.repository.save(AcceptedQna(accepted_reply(revision_number=2), (0.1,)))
        self.assertEqual(self.container.documents['conv-1']['revision_number'], 3)

    def test_create_race_is_idempotent_for_the_same_decision(self):
        original_create = self.container.create_item
        def race(**kwargs):
            original_create(**kwargs)
            raise CosmosHttpResponseError(status_code=409)
        with patch.object(self.container, 'create_item', side_effect=race):
            self.repository.save(self.original)
        self.assertEqual(len(self.container.documents), 1)
        self.assertEqual(self.container.documents['conv-1']['revision_number'], 1)

    def test_persistent_contention_has_a_bounded_retry_budget(self):
        self.repository.save(self.original)
        with patch.object(self.container, 'replace_item', side_effect=CosmosHttpResponseError(status_code=412)) as write:
            with self.assertRaises(StorageUnavailableError):
                self.repository.save(AcceptedQna(accepted_reply(revision_number=2), (0.1,)))
        self.assertEqual(write.call_count, 3)
        self.assertEqual(self.container.documents['conv-1']['revision_number'], 1)
