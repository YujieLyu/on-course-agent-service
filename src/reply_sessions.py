from copy import deepcopy

from src.logger import get_logger
from src.models import IntentClassification, ReplySession, Revision, SearchResult, SessionStatus
from src.workflow.llm import create_conversation

logger = get_logger(__name__)


def _log_transition(session: ReplySession, previous_status: SessionStatus | None) -> None:
    logger.info("session.transition", extra={"event_fields": {
        "conversation_id": session.conversation_id,
        "course_id": session.course_id,
        "previous_status": previous_status,
        "session_status": session.status,
        "revision_number": len(session.revisions),
    }})


class SessionNotFoundError(KeyError):
    pass


class InvalidSessionActionError(ValueError):
    pass


class ReplySessionStore:
    """Minimal process-local session storage for the MVP."""

    def __init__(self) -> None:
        self._sessions: dict[str, ReplySession] = {}
        self._evidence_contexts: dict[
            str, tuple[IntentClassification, list[SearchResult]]
        ] = {}

    async def create(
        self,
        student_query: str,
        course_id: str,
        user_id: str | None = None,
    ) -> ReplySession:
        conversation_id = await create_conversation()
        session = ReplySession(
            conversation_id=conversation_id,
            course_id=course_id,
            user_id=user_id,
            student_query=student_query,
        )
        self._sessions[conversation_id] = session
        _log_transition(session, None)
        return deepcopy(session)

    def get(self, conversation_id: str) -> ReplySession:
        try:
            return deepcopy(self._sessions[conversation_id])
        except KeyError as error:
            raise SessionNotFoundError(conversation_id) from error

    def save_evidence_context(
        self,
        conversation_id: str,
        intent: IntentClassification,
        results: list[SearchResult],
    ) -> None:
        """Keep original evidence for independent checks of later revisions."""
        self._get_mutable(conversation_id)
        self._evidence_contexts[conversation_id] = deepcopy((intent, results))

    def get_evidence_context(
        self, conversation_id: str,
    ) -> tuple[IntentClassification | None, list[SearchResult]]:
        self._get_mutable(conversation_id)
        return deepcopy(self._evidence_contexts.get(conversation_id, (None, [])))

    def request_revision(self, conversation_id: str, feedback: str) -> ReplySession:
        session = self._get_mutable(conversation_id)
        if session.status != SessionStatus.AWAITING_REVIEW:
            raise InvalidSessionActionError("Session is not awaiting review")
        session.revisions.append(
            Revision(
                revision_number=len(session.revisions) + 1,
                feedback=feedback,
            )
        )
        session.status = SessionStatus.REVISION_REQUESTED
        _log_transition(session, SessionStatus.AWAITING_REVIEW)
        return deepcopy(session)

    def save_generation(self, conversation_id: str, content: str) -> ReplySession:
        session = self._get_mutable(conversation_id)
        previous_status = session.status
        if session.status == SessionStatus.PENDING_GENERATION:
            session.revisions.append(
                Revision(revision_number=1, revision_content=content)
            )
        elif session.status == SessionStatus.REVISION_REQUESTED:
            session.revisions[-1].revision_content = content
        else:
            raise InvalidSessionActionError("Session is not ready for generation")
        session.status = SessionStatus.AWAITING_REVIEW
        _log_transition(session, previous_status)
        return deepcopy(session)

    def accept(self, conversation_id: str) -> ReplySession:
        session = self._get_mutable(conversation_id)
        if session.status != SessionStatus.AWAITING_REVIEW:
            raise InvalidSessionActionError("Session is not awaiting review")
        session.status = SessionStatus.ACCEPTED
        _log_transition(session, SessionStatus.AWAITING_REVIEW)
        return deepcopy(session)

    def _get_mutable(self, conversation_id: str) -> ReplySession:
        try:
            return self._sessions[conversation_id]
        except KeyError as error:
            raise SessionNotFoundError(conversation_id) from error
