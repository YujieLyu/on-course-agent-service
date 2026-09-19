"""Session snapshots and persistence ports; no SDK or HTTP dependencies."""

from collections.abc import Awaitable, Callable, Iterable
from dataclasses import dataclass
from datetime import datetime
from typing import Protocol

from pydantic import ConfigDict, Field

from src.models import IntentClassification, ReplySession, SearchResult, StrictModel


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


class AcceptedReply(StrictModel):
    """Immutable acceptance decision, persisted before publishing QA Memory."""

    model_config = ConfigDict(frozen=True)

    conversation_id: str
    course_id: str
    student_query: str
    revision_number: int = Field(ge=1)
    accepted_response: str = Field(min_length=1)
    accepted_at: datetime


@dataclass(frozen=True)
class SessionSnapshot:
    session: ReplySession
    version: str
    intent_context: IntentClassification | None = None
    retrieved_context: tuple[SearchResult, ...] = ()
    acceptance: AcceptedReply | None = None


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
