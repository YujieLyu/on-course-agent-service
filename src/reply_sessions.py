from copy import deepcopy

from src.models import ReplySession, Revision, SessionStatus
from src.workflow.llm import create_conversation


class SessionNotFoundError(KeyError):
    pass


class InvalidSessionActionError(ValueError):
    pass


class ReplySessionStore:
    """Minimal process-local session storage for the MVP."""

    def __init__(self) -> None:
        self._sessions: dict[str, ReplySession] = {}

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
        return deepcopy(session)

    def get(self, conversation_id: str) -> ReplySession:
        try:
            return deepcopy(self._sessions[conversation_id])
        except KeyError as error:
            raise SessionNotFoundError(conversation_id) from error

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
        return deepcopy(session)

    def save_generation(self, conversation_id: str, content: str) -> ReplySession:
        session = self._get_mutable(conversation_id)
        if session.status == SessionStatus.PENDING_GENERATION:
            session.revisions.append(
                Revision(revision_number=1, revision_content=content)
            )
        elif session.status == SessionStatus.REVISION_REQUESTED:
            session.revisions[-1].revision_content = content
        else:
            raise InvalidSessionActionError("Session is not ready for generation")
        session.status = SessionStatus.AWAITING_REVIEW
        return deepcopy(session)

    def accept(self, conversation_id: str) -> ReplySession:
        session = self._get_mutable(conversation_id)
        if session.status != SessionStatus.AWAITING_REVIEW:
            raise InvalidSessionActionError("Session is not awaiting review")
        session.status = SessionStatus.ACCEPTED
        return deepcopy(session)

    def _get_mutable(self, conversation_id: str) -> ReplySession:
        try:
            return self._sessions[conversation_id]
        except KeyError as error:
            raise SessionNotFoundError(conversation_id) from error
