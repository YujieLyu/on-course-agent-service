"""Session application operations with one persistence and logging boundary."""

import asyncio
from collections.abc import Iterable

from src.agents.reply_agent import run_answer
from src.config import get_model_name
from src.domain.evidence import SearchResult
from src.domain.reply_sessions import Clock, ReplySession, SessionRepository, SessionSnapshot
from src.domain.retention import utc_now
from src.logger import get_logger
from src.tools.search import search

logger = get_logger(__name__)


class ReplySessionService:
    def __init__(self, repository: SessionRepository, *, clock: Clock = utc_now) -> None:
        self.repository = repository
        self.clock = clock

    def create(
        self, conversation_id: str, student_query: str, course_id: str, user_id: str | None = None,
    ) -> ReplySession:
        session = ReplySession.create(
            conversation_id, student_query, course_id, user_id=user_id, now=self.clock(),
        )
        return self._commit(None, session).session

    def load_snapshot(self, conversation_id: str) -> SessionSnapshot:
        snapshot = self.repository.read(conversation_id)
        snapshot.session.remaining_lifetime(self.clock())
        return snapshot

    def get(self, conversation_id: str) -> ReplySession:
        return self.load_snapshot(conversation_id).session

    async def generate(self, conversation_id: str) -> ReplySession:
        snapshot = await asyncio.to_thread(self.load_snapshot, conversation_id)
        session = snapshot.session
        session.require_generation(self.clock())
        evidence = (await search(session.student_query, session.course_id)).results
        response = await run_answer(session.student_query, get_model_name("REPLY_MODEL_NAME"), evidence)
        return await asyncio.to_thread(
            self.save_generation, snapshot, response.reply,
            citations=response.citations, retrieved_context=evidence,
        )

    async def review(self, conversation_id: str, feedback: str) -> ReplySession:
        snapshot = await asyncio.to_thread(self.load_snapshot, conversation_id)
        session = snapshot.session
        session.require_review(self.clock())
        response = await run_answer(
            session.student_query, get_model_name("REPLY_MODEL_NAME"), session.retrieved_context,
            previous_reply=session.revisions[-1].revision_content,
            feedback_history=[revision.feedback for revision in session.revisions if revision.feedback] + [feedback],
        )
        return await asyncio.to_thread(
            self.save_revision, snapshot, feedback, response.reply, citations=response.citations,
        )

    def save_generation(
        self, snapshot: SessionSnapshot, content: str, *,
        citations: Iterable[SearchResult] = (),
        retrieved_context: Iterable[SearchResult] = (),
    ) -> ReplySession:
        candidate = snapshot.session.record_generation(
            content, self.clock(), citations=citations, retrieved_context=retrieved_context,
        )
        return self._commit(snapshot, candidate).session

    def save_revision(
        self, snapshot: SessionSnapshot, feedback: str, content: str, *,
        citations: Iterable[SearchResult] = (),
    ) -> ReplySession:
        candidate = snapshot.session.record_revision(feedback, content, self.clock(), citations=citations)
        return self._commit(snapshot, candidate).session

    def begin_acceptance(self, snapshot: SessionSnapshot) -> SessionSnapshot:
        return self._commit(snapshot, snapshot.session.begin_acceptance(self.clock()))

    def complete_acceptance(self, snapshot: SessionSnapshot) -> ReplySession:
        return self._commit(snapshot, snapshot.session.complete_acceptance(self.clock())).session

    def _commit(self, original: SessionSnapshot | None, candidate: ReplySession) -> SessionSnapshot:
        # Recheck at the write boundary: generation/publication may cross expiry.
        candidate.remaining_lifetime(self.clock())
        saved = (
            self.repository.create(candidate) if original is None
            else self.repository.replace(SessionSnapshot(candidate, original.version))
        )
        logger.info("session.transition", extra={"event_fields": {
            "conversation_id": saved.session.conversation_id,
            "course_id": saved.session.course_id,
            "previous_status": original.session.status if original is not None else None,
            "session_status": saved.session.status,
            "revision_number": len(saved.session.revisions),
        }})
        return saved
