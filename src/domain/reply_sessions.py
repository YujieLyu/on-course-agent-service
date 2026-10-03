"""Immutable session aggregate, business rules, and persistence ports."""

from collections.abc import Awaitable, Callable, Iterable
from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import StrEnum
from typing import Literal, Protocol

from pydantic import BaseModel, ConfigDict, Field

from src.domain.evidence import SearchResult, merge_citations
from src.domain.retention import SESSION_RETENTION


class SessionNotFoundError(KeyError):
    pass


class InvalidSessionActionError(ValueError):
    pass


class SessionConflictError(InvalidSessionActionError):
    pass


class StorageUnavailableError(RuntimeError):
    pass


class QaMemoryConflictError(ValueError):
    """An accepted revision would overwrite newer or inconsistent QA content."""


class AcceptedReply(BaseModel):
    """Immutable acceptance decision, persisted before publishing QA Memory."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    conversation_id: str
    course_id: str
    student_query: str
    revision_number: int = Field(ge=1)
    accepted_response: str = Field(min_length=1)
    accepted_at: datetime


class SessionStatus(StrEnum):
    PENDING_GENERATION = "pending_generation"
    AWAITING_REVIEW = "awaiting_review"
    ACCEPTING = "accepting"
    ACCEPTED = "accepted"


ReplyStatus = Literal["answered", "needs_clarification", "not_found", "escalated", "error"]


class Revision(BaseModel):
    """Identity is (conversation_id, revision_number); null metadata is unknown."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    revision_number: int = Field(ge=1)
    feedback: str | None = None
    revision_content: str
    outcome: ReplyStatus | None = None
    evidence: tuple[SearchResult, ...] | None = None
    citations: tuple[SearchResult, ...] | None = None
    completed_at: datetime | None = None


class ReplySession(BaseModel):
    """Own session invariants; operations return new values and perform no I/O."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    conversation_id: str
    course_id: str = Field(min_length=1)
    user_id: str | None = None
    student_query: str = Field(min_length=1)
    created_at: datetime
    expires_at: datetime
    status: SessionStatus = SessionStatus.PENDING_GENERATION
    revisions: tuple[Revision, ...] = ()
    citations: tuple[SearchResult, ...] = ()
    retrieved_context: tuple[SearchResult, ...] = ()
    acceptance: AcceptedReply | None = None

    @property
    def current_citations(self) -> tuple[SearchResult, ...] | None:
        # Legacy session citations accumulated across versions, so cannot certify
        # which sources the last revision actually used.
        return self.revisions[-1].citations if self.revisions else ()

    @property
    def revision_context(self) -> tuple[SearchResult, ...]:
        if self.revisions and self.revisions[-1].evidence is not None:
            return self.revisions[-1].evidence
        # Legacy records do preserve original retrieval, but not per-version use.
        return self.retrieved_context

    @classmethod
    def create(
        cls, conversation_id: str, student_query: str, course_id: str,
        *, now: datetime, user_id: str | None = None,
    ) -> "ReplySession":
        return cls(
            conversation_id=conversation_id, student_query=student_query,
            course_id=course_id, user_id=user_id, created_at=now,
            expires_at=now + SESSION_RETENTION,
        )

    def remaining_lifetime(self, now: datetime) -> timedelta:
        remaining = self.expires_at - now
        if remaining <= timedelta(0):
            raise SessionNotFoundError(self.conversation_id)
        return remaining

    def require_generation(self, now: datetime) -> None:
        self._require_status(
            now, (SessionStatus.PENDING_GENERATION,),
            "Session is not ready for generation",
        )

    def require_review(self, now: datetime) -> None:
        self._require_status(now, (SessionStatus.AWAITING_REVIEW,), "Session is not awaiting review")

    def record_generation(
        self, content: str, now: datetime, *,
        citations: Iterable[SearchResult] = (),
        retrieved_context: Iterable[SearchResult] = (),
        outcome: ReplyStatus | None = None,
    ) -> "ReplySession":
        self.require_generation(now)
        evidence = tuple(retrieved_context)
        revision = Revision(
            revision_number=1, revision_content=content, outcome=outcome,
            evidence=evidence, citations=merge_citations(citations), completed_at=now,
        )
        return self.model_copy(update={
            "status": SessionStatus.AWAITING_REVIEW,
            "revisions": (revision,),
            "citations": revision.citations,
            "retrieved_context": evidence,
        })

    def record_revision(
        self, feedback: str, content: str, now: datetime, *,
        citations: Iterable[SearchResult] = (),
        outcome: ReplyStatus | None = None,
    ) -> "ReplySession":
        self.require_review(now)
        revision = Revision(
            revision_number=len(self.revisions) + 1,
            feedback=feedback, revision_content=content,
            outcome=outcome, evidence=self.revision_context,
            citations=merge_citations(citations), completed_at=now,
        )
        return self.model_copy(update={
            "revisions": (*self.revisions, revision),
            "citations": revision.citations,
        })

    def begin_acceptance(self, now: datetime) -> "ReplySession":
        self._require_status(now, (SessionStatus.AWAITING_REVIEW,), "Session is not awaiting review")
        revision = self.revisions[-1] if self.revisions else None
        if revision is None or not revision.revision_content:
            raise InvalidSessionActionError("Session has no generated reply to accept")
        acceptance = AcceptedReply(
            conversation_id=self.conversation_id, course_id=self.course_id,
            student_query=self.student_query, revision_number=revision.revision_number,
            accepted_response=revision.revision_content, accepted_at=now,
        )
        return self.model_copy(update={"status": SessionStatus.ACCEPTING, "acceptance": acceptance})

    def pending_acceptance(self, now: datetime) -> AcceptedReply:
        self._require_status(now, (SessionStatus.ACCEPTING,), "Session has no pending acceptance")
        if self.acceptance is None:
            raise InvalidSessionActionError("Session has no pending acceptance")
        return self.acceptance

    def complete_acceptance(self, now: datetime) -> "ReplySession":
        self.pending_acceptance(now)
        return self.model_copy(update={"status": SessionStatus.ACCEPTED})

    def _require_status(
        self, now: datetime, allowed: tuple[SessionStatus, ...], message: str,
    ) -> None:
        self.remaining_lifetime(now)
        if self.status not in allowed:
            raise InvalidSessionActionError(message)


@dataclass(frozen=True)
class SessionSnapshot:
    session: ReplySession
    version: str


@dataclass(frozen=True)
class AcceptedQna:
    reply: AcceptedReply
    question_vector: tuple[float, ...]


class SessionRepository(Protocol):
    def create(self, session: ReplySession) -> SessionSnapshot: ...

    def read(self, conversation_id: str) -> SessionSnapshot: ...

    def replace(self, snapshot: SessionSnapshot) -> SessionSnapshot:
        """Replace only if snapshot.version is still current, or raise a conflict."""
        ...


class PendingAcceptanceSource(Protocol):
    def pending_ids(self, limit: int) -> Iterable[str]: ...


class QaMemoryRepository(Protocol):
    def save(self, item: AcceptedQna) -> None:
        """Publish idempotently; never overwrite a newer or conflicting revision."""
        ...


Clock = Callable[[], datetime]
AcceptedReplyPublisher = Callable[[AcceptedReply], Awaitable[None]]
