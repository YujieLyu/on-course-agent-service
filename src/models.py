from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from src.domain.evidence import SearchResult, SearchSource
from src.domain.reply_sessions import Revision, SessionStatus


class StrictModel(BaseModel):
    """Base model used for LLM structured outputs."""

    model_config = ConfigDict(extra="forbid")


class SearchResults(StrictModel):
    query: str = Field(min_length=1)
    results: list[SearchResult]


ReplyStatus = Literal["answered", "needs_clarification", "not_found", "escalated", "error"]


class ReplyDraft(StrictModel):
    """Model output selects retrieved source IDs; source objects are resolved by the service."""

    reply: str
    status: ReplyStatus
    cited_source_ids: list[str] = Field(
        description="IDs of retrieved sources used in this answer, such as source_1; empty when none are used."
    )


class ReplyResponse(StrictModel):
    """Generated answer with original source objects resolved from model-selected IDs."""

    reply: str
    status: ReplyStatus
    citations: list[SearchResult]


class HTTPErrorResponse(StrictModel):
    """Error body returned by HTTPException for reply-session operations."""

    detail: str


class ReplySession(StrictModel):
    """Public response DTO; domain state and persistence metadata stay internal."""

    model_config = ConfigDict(from_attributes=True)

    conversation_id: str
    course_id: str = Field(min_length=1)
    user_id: str | None = None
    student_query: str = Field(min_length=1)
    status: SessionStatus = SessionStatus.PENDING_GENERATION
    revisions: list[Revision] = Field(default_factory=list)
    citations: list[SearchResult] = Field(
        default_factory=list, description="Deduplicated sources cited across all generated replies in this session."
    )
    created_at: datetime
    expires_at: datetime


class CreateReplySessionRequest(StrictModel):
    question: str = Field(min_length=1)
    course_id: str = Field(min_length=1)
    user_id: str | None = None


class ReviewReplySessionRequest(StrictModel):
    feedback: str = Field(min_length=1)
