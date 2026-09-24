import unittest
from datetime import UTC, datetime, timedelta

from pydantic import ValidationError

from src.domain.reply_sessions import InvalidSessionActionError, ReplySession, SessionNotFoundError
from src.models import ReplySession as ReplySessionResponse, SearchResult


class ReplySessionDomainTests(unittest.TestCase):
    def setUp(self):
        self.now = datetime(2026, 9, 22, tzinfo=UTC)
        self.pending = ReplySession.create('session-1', 'Question', 'course-1', now=self.now)
        self.source = SearchResult(source='course_materials', content='Submit a PDF.')

    def test_completed_revisions_accumulate_sources_and_preserve_original_context(self):
        first = self.pending.record_generation('Submit a PDF.', self.now, citations=[self.source], retrieved_context=[self.source])
        other = SearchResult(source='university_policies', content='Declare AI use.')
        second = first.record_revision('Include the policy', 'Submit a PDF and declare AI use.', self.now, citations=[self.source, other])
        third = second.record_revision('Shorter', 'PDF; declare AI use.', self.now)
        self.assertEqual([revision.revision_number for revision in third.revisions], [1, 2, 3])
        self.assertEqual(third.citations, (self.source, other))
        self.assertEqual(third.retrieved_context, (self.source,))
        self.assertEqual(third.status, 'awaiting_review')
        self.assertEqual(third.expires_at, self.now + timedelta(days=30))
        self.assertEqual(len(first.revisions), 1)
        self.assertEqual(self.pending.revisions, ())
        with self.assertRaises(ValidationError):
            third.revisions[0].revision_content = 'Changed'

    def test_invalid_transitions_and_expiry_are_rejected(self):
        draft = self.pending.record_generation('Draft', self.now)
        accepted = draft.begin_acceptance(self.now).complete_acceptance(self.now)
        for operation in [
            lambda: self.pending.record_revision('Feedback', 'Revision', self.now),
            lambda: draft.record_generation('Duplicate', self.now),
            lambda: accepted.record_revision('Feedback', 'Revision', self.now),
            lambda: self.pending.begin_acceptance(self.now),
        ]:
            with self.assertRaises(InvalidSessionActionError):
                operation()
        for operation in [
            lambda: self.pending.record_generation('Draft', self.pending.expires_at),
            lambda: draft.record_revision('Feedback', 'Revision', draft.expires_at),
            lambda: draft.begin_acceptance(draft.expires_at),
        ]:
            with self.assertRaises(SessionNotFoundError):
                operation()

    def test_public_projection_hides_internal_context_and_requires_timestamps(self):
        draft = self.pending.record_generation('Draft', self.now, citations=[self.source], retrieved_context=[self.source])
        response = ReplySessionResponse.model_validate(draft.begin_acceptance(self.now))
        self.assertNotIn('retrieved_context', response.model_dump())
        self.assertNotIn('acceptance', response.model_dump())
        response.citations.clear()
        self.assertEqual(draft.citations, (self.source,))
        with self.assertRaises(ValidationError):
            ReplySessionResponse.model_validate({'conversation_id': '1', 'course_id': 'c1', 'student_query': 'Question'})

    def test_empty_draft_cannot_be_accepted(self):
        with self.assertRaises(InvalidSessionActionError):
            self.pending.record_generation('', self.now).begin_acceptance(self.now)
