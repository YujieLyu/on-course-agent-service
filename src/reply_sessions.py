"""Session state transitions over versioned snapshots."""

from dataclasses import replace

from src.domain.reply_sessions import (
    AcceptedReply,
    Clock,
    InvalidSessionActionError,
    SessionNotFoundError,
    SessionRepository,
    SessionSnapshot,
)
from src.domain.retention import SESSION_RETENTION, utc_now
from src.logger import get_logger
from src.models import IntentClassification, ReplySession, Revision, SearchResult, SessionStatus

logger = get_logger(__name__)


def _log_transition(session: ReplySession, previous_status: SessionStatus | None) -> None:
    logger.info("session.transition", extra={"event_fields": {
        "conversation_id": session.conversation_id,
        "course_id": session.course_id,
        "previous_status": previous_status,
        "session_status": session.status,
        "revision_number": len(session.revisions),
    }})


class ReplySessionStore:
    def __init__(self, repository: SessionRepository, *, clock: Clock = utc_now) -> None:
        self.repository = repository
        self.clock = clock

    def create(
        self, conversation_id: str, student_query: str, course_id: str, user_id: str | None = None,
    ) -> ReplySession:
        created_at = self.clock()
        session = ReplySession(
            conversation_id=conversation_id, course_id=course_id, user_id=user_id,
            student_query=student_query, created_at=created_at,
            expires_at=created_at + SESSION_RETENTION,
        )
        saved = self.repository.create(session)
        _log_transition(saved.session, None)
        return saved.session

    def load_snapshot(self, conversation_id: str) -> SessionSnapshot:
        snapshot = self.repository.read(conversation_id)
        self._ensure_alive(snapshot.session)
        return snapshot

    def get(self, conversation_id: str) -> ReplySession:
        return self.load_snapshot(conversation_id).session

    def request_revision(self, conversation_id: str, feedback: str) -> ReplySession:
        snapshot = self.load_snapshot(conversation_id)
        session = snapshot.session.model_copy(deep=True)
        if session.status != SessionStatus.AWAITING_REVIEW:
            raise InvalidSessionActionError("Session is not awaiting review")
        session.revisions.append(Revision(revision_number=len(session.revisions) + 1, feedback=feedback))
        session.status = SessionStatus.REVISION_REQUESTED
        return self._save(snapshot, replace(snapshot, session=session)).session

    def save_generation(
        self, snapshot: SessionSnapshot, content: str, *,
        evidence: tuple[IntentClassification, list[SearchResult]] | None = None,
    ) -> ReplySession:
        session = snapshot.session.model_copy(deep=True)
        candidate = replace(snapshot, session=session)
        if session.status == SessionStatus.PENDING_GENERATION:
            session.revisions.append(Revision(revision_number=1, revision_content=content))
            if evidence is not None:
                intent, results = evidence
                candidate = replace(candidate, intent_context=intent, retrieved_context=tuple(results))
        elif session.status == SessionStatus.REVISION_REQUESTED:
            session.revisions[-1].revision_content = content
        else:
            raise InvalidSessionActionError("Session is not ready for generation")
        session.status = SessionStatus.AWAITING_REVIEW
        return self._save(snapshot, candidate).session

    def begin_acceptance(self, snapshot: SessionSnapshot) -> SessionSnapshot:
        session = snapshot.session.model_copy(deep=True)
        if session.status != SessionStatus.AWAITING_REVIEW:
            raise InvalidSessionActionError("Session is not awaiting review")
        if not session.revisions:
            raise InvalidSessionActionError("Session has no generated reply to accept")
        revision = session.revisions[-1]
        content = revision.revision_content
        if not content:
            raise InvalidSessionActionError("Session has no generated reply to accept")
        acceptance = AcceptedReply(
            conversation_id=session.conversation_id, course_id=session.course_id,
            student_query=session.student_query, revision_number=revision.revision_number,
            accepted_response=content, accepted_at=self.clock(),
        )
        session.status = SessionStatus.ACCEPTING
        return self._save(snapshot, replace(snapshot, session=session, acceptance=acceptance))

    def complete_acceptance(self, snapshot: SessionSnapshot) -> ReplySession:
        if snapshot.session.status != SessionStatus.ACCEPTING or snapshot.acceptance is None:
            raise InvalidSessionActionError("Session has no pending acceptance")
        session = snapshot.session.model_copy(deep=True)
        session.status = SessionStatus.ACCEPTED
        return self._save(snapshot, replace(snapshot, session=session)).session

    def _ensure_alive(self, session: ReplySession) -> None:
        if session.expires_at <= self.clock():
            raise SessionNotFoundError(session.conversation_id)

    def _save(self, original: SessionSnapshot, candidate: SessionSnapshot) -> SessionSnapshot:
        self._ensure_alive(candidate.session)
        saved = self.repository.replace(candidate)
        _log_transition(saved.session, original.session.status)
        return saved
