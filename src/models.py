from datetime import datetime

from pydantic import AliasChoices, BaseModel, ConfigDict, Field

from src.domain.evidence import SearchResult, SearchSource
from src.domain.reply_sessions import ReplyStatus, SessionStatus


class StrictModel(BaseModel):
    """Base model used for LLM structured outputs."""

    model_config = ConfigDict(extra="forbid")


class SearchResults(StrictModel):
    query: str = Field(min_length=1)
    results: list[SearchResult]


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


class RevisionResponse(StrictModel):
    """Public version metadata; full evidence stays in the session repository."""

    model_config = ConfigDict(from_attributes=True)

    revision_number: int = Field(ge=1, description="Together with conversation_id, identifies this revision.")
    feedback: str | None = None
    revision_content: str
    outcome: ReplyStatus | None = Field(default=None, description="Actual generation result; null for unknown legacy outcomes.")
    citations: list[SearchResult] | None = Field(default=None, description="Sources cited by this version; [] means none, null means unknown legacy provenance.")
    completed_at: datetime | None = Field(default=None, description="Time the completed revision was recorded; null when not recorded by a legacy writer.")


class ReplySessionResponse(StrictModel):
    """Public response DTO; domain state and persistence metadata stay internal."""

    model_config = ConfigDict(from_attributes=True)

    conversation_id: str
    course_id: str = Field(min_length=1)
    user_id: str | None = None
    student_query: str = Field(min_length=1)
    status: SessionStatus = SessionStatus.PENDING_GENERATION
    revisions: list[RevisionResponse] = Field(default_factory=list)
    citations: list[SearchResult] | None = Field(
        default_factory=list,
        validation_alias=AliasChoices("current_citations", "citations"),
        description="Sources cited by the current revision; [] means none, null means unknown legacy provenance.",
    )
    created_at: datetime
    expires_at: datetime


class CreateReplySessionRequest(StrictModel):
    question: str = Field(min_length=1)
    course_id: str = Field(min_length=1)
    user_id: str | None = None


class ReviewReplySessionRequest(StrictModel):
    feedback: str = Field(min_length=1)
