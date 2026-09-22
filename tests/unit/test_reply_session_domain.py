import unittest
from datetime import UTC, datetime, timedelta

from pydantic import ValidationError

from src.domain.evidence import IntentClassification, SearchResult, SearchSource, StudentIntent
from src.domain.reply_sessions import InvalidSessionActionError, ReplySession, SessionNotFoundError, SessionStatus
from src.models import ReplySession as ReplySessionResponse


class ReplySessionDomainTests(unittest.TestCase):
    def setUp(self):
        self.now = datetime(2026, 9, 21, tzinfo=UTC)
        self.pending = ReplySession.create('conv-1', 'What is due?', 'course-1', now=self.now)
        self.draft = self.pending.record_generation('Friday.', self.now)
        self.requested = self.draft.request_revision('Make it friendlier.', self.now)
        self.revised = self.requested.record_generation('Please submit it by Friday.', self.now)
        self.accepting = self.revised.begin_acceptance(self.now)
        self.accepted = self.accepting.complete_acceptance(self.now)

    def test_transitions_preserve_previous_values_and_fixed_lifetime(self):
        self.assertEqual(self.pending.revisions, ())
        self.assertEqual(self.draft.revisions[0].revision_content, 'Friday.')
        self.assertEqual([r.revision_number for r in self.requested.revisions], [1, 2])
        self.assertIsNone(self.requested.revisions[-1].revision_content)
        self.assertEqual(self.revised.revisions[-1].feedback, 'Make it friendlier.')
        self.assertEqual(self.revised.revisions[-1].revision_content, 'Please submit it by Friday.')
        self.assertEqual(self.accepting.acceptance.revision_number, 2)
        self.assertEqual(self.accepting.acceptance.accepted_response, self.revised.revisions[-1].revision_content)
        self.assertEqual(self.accepted.acceptance, self.accepting.acceptance)
        for session, status in (
            (self.pending, SessionStatus.PENDING_GENERATION),
            (self.draft, SessionStatus.AWAITING_REVIEW),
            (self.requested, SessionStatus.REVISION_REQUESTED),
            (self.revised, SessionStatus.AWAITING_REVIEW),
            (self.accepting, SessionStatus.ACCEPTING),
            (self.accepted, SessionStatus.ACCEPTED),
        ):
            with self.subTest(status=status):
                self.assertEqual(session.status, status)
                self.assertEqual(session.created_at, self.now)
                self.assertEqual(session.expires_at, self.now + timedelta(days=30))

    def test_invalid_transitions_are_rejected_by_the_domain_without_storage(self):
        operations = (
            (lambda s: s.request_revision('Change it', self.now), {SessionStatus.AWAITING_REVIEW}),
            (lambda s: s.record_generation('Draft', self.now), {
                SessionStatus.PENDING_GENERATION, SessionStatus.REVISION_REQUESTED,
            }),
            (lambda s: s.begin_acceptance(self.now), {SessionStatus.AWAITING_REVIEW}),
            (lambda s: s.complete_acceptance(self.now), {SessionStatus.ACCEPTING}),
            (lambda s: s.pending_acceptance(self.now), {SessionStatus.ACCEPTING}),
        )
        for operation, allowed in operations:
            for session in (self.pending, self.draft, self.requested, self.accepting, self.accepted):
                if session.status not in allowed:
                    with self.subTest(operation=operation, status=session.status):
                        with self.assertRaises(InvalidSessionActionError):
                            operation(session)

    def test_every_business_operation_rejects_expired_sessions(self):
        expired_at = self.pending.expires_at
        for operation in (
            lambda: self.pending.remaining_lifetime(expired_at),
            lambda: self.pending.generation_is_revision(expired_at),
            lambda: self.pending.record_generation('Late', expired_at),
            lambda: self.requested.record_generation('Late', expired_at),
            lambda: self.draft.request_revision('Late', expired_at),
            lambda: self.draft.begin_acceptance(expired_at),
            lambda: self.accepting.pending_acceptance(expired_at),
            lambda: self.accepting.complete_acceptance(expired_at),
        ):
            with self.assertRaises(SessionNotFoundError):
                operation()

    def test_empty_draft_cannot_be_accepted(self):
        empty = self.pending.record_generation('', self.now)
        with self.assertRaisesRegex(InvalidSessionActionError, 'no generated reply'):
            empty.begin_acceptance(self.now)

    def test_nested_values_cannot_mutate_an_existing_snapshot(self):
        intent = IntentClassification(
            intent=StudentIntent.ASSESSMENT_REQUIREMENTS,
            search_sources=[SearchSource.COURSE_MATERIALS], needs_clarification=False,
        )
        source = SearchResult(source=SearchSource.COURSE_MATERIALS, content='Due Friday.')
        sources = [source]
        draft = self.pending.record_generation('Friday.', self.now, citations=sources, evidence=(intent, sources))
        sources.clear()
        self.assertEqual(draft.retrieved_context, (source,))
        self.assertEqual(draft.citations, (source,))
        for value, field, changed in (
            (draft, 'status', SessionStatus.ACCEPTED),
            (draft.revisions[0], 'revision_content', 'Unreviewed'),
            (draft, 'citations', ()),
            (source, 'content', 'Changed source'),
            (intent, 'needs_clarification', True),
            (self.accepting.acceptance, 'accepted_response', 'Unreviewed'),
        ):
            with self.subTest(field=field), self.assertRaises(ValidationError):
                setattr(value, field, changed)
        with self.assertRaises(AttributeError):
            draft.revisions.append(draft.revisions[0])
        with self.assertRaises(AttributeError):
            draft.citations.append(source)
        with self.assertRaises(AttributeError):
            intent.search_sources.append(SearchSource.CANVAS)

    def test_revision_preserves_original_evidence(self):
        intent = IntentClassification(intent=StudentIntent.OUT_OF_SCOPE, search_sources=[], needs_clarification=False)
        source = SearchResult(source=SearchSource.COURSE_MATERIALS, content='Due Friday.')
        draft = self.pending.record_generation('Friday.', self.now, evidence=(intent, [source]))
        requested = draft.request_revision('Shorter', self.now)
        revised = requested.record_generation('Fri.', self.now, evidence=(intent, []))
        self.assertEqual(revised.retrieved_context, (source,))
        self.assertEqual(revised.intent_context, intent)
        self.assertEqual(requested.revisions[-1].revision_content, None)

    def test_session_citations_accumulate_without_duplicate_passages(self):
        first_source = SearchResult(
            source=SearchSource.COURSE_MATERIALS, content='Due Friday.', source_url='https://course.test/assignment',
        )
        next_source = first_source.model_copy(update={'content': 'Submit through the portal.'})
        first = self.pending.record_generation('Friday.', self.now, citations=[first_source, first_source])
        self.assertEqual(first.citations, (first_source,))
        requested = first.request_revision('Explain submission.', self.now)
        self.assertEqual(requested.citations, first.citations)
        duplicate = first_source.model_copy(update={'title': 'Assignment', 'excerpt': 'Due Friday.'})
        revised = requested.record_generation('Use the portal.', self.now, citations=[duplicate, next_source])
        self.assertEqual(revised.citations, (first_source, next_source))
        uncited = revised.request_revision('Give general study advice.', self.now).record_generation(
            'Make a study plan.', self.now,
        )
        self.assertEqual(uncited.citations, (first_source, next_source))
        self.assertEqual(first.citations, (first_source,))
        self.assertTrue(all('citations' not in revision.model_dump() for revision in uncited.revisions))

    def test_response_projects_only_public_fields_and_cannot_mutate_the_domain(self):
        source = SearchResult(source=SearchSource.COURSE_MATERIALS, content='Due Friday.')
        session = self.accepting.model_copy(update={'citations': (source,)})
        response = ReplySessionResponse.model_validate(session)
        self.assertEqual(set(response.model_dump()), {
            'conversation_id', 'course_id', 'user_id', 'student_query', 'status',
            'revisions', 'citations', 'created_at', 'expires_at',
        })
        self.assertEqual(response.created_at, self.accepting.created_at)
        self.assertEqual(response.expires_at, self.accepting.expires_at)
        response.revisions.clear()
        response.citations.clear()
        self.assertEqual(session.citations, (source,))
        response.status = SessionStatus.PENDING_GENERATION
        self.assertEqual(len(self.accepting.revisions), 2)
        self.assertEqual(self.accepting.status, SessionStatus.ACCEPTING)
        self.assertIn('"revisions":[]', response.model_dump_json())

    def test_response_does_not_invent_missing_persisted_timestamps(self):
        payload = ReplySessionResponse.model_validate(self.draft).model_dump()
        for field in ('created_at', 'expires_at'):
            with self.subTest(field=field), self.assertRaises(ValidationError):
                ReplySessionResponse.model_validate({k: v for k, v in payload.items() if k != field})
