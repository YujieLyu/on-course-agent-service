from datetime import datetime
from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from src.domain.retention import SESSION_RETENTION, utc_now


class StrictModel(BaseModel):
    """Base model used for LLM structured outputs."""

    model_config = ConfigDict(extra="forbid")

class ClientType(StrEnum):
    OPENAI="openai"
    ANTHROPIC="anthropic"

class StudentIntent(StrEnum):
    COURSE_CONTENT_EXPLANATION = "course_content_explanation"
    COURSE_SCHEDULE_AND_OPERATIONS = "course_schedule_and_operations"
    ASSESSMENT_REQUIREMENTS = "assessment_requirements"
    SUBMISSION_HELP = "submission_help"
    GRADING_AND_RUBRIC = "grading_and_rubric"
    EXTENSION_AND_SPECIAL_CONSIDERATION = "extension_and_special_consideration"
    ACADEMIC_INTEGRITY = "academic_integrity"
    TECHNICAL_ISSUE = "technical_issue"
    PERSONAL_STATUS = "personal_status"
    ADMINISTRATIVE_REQUEST = "administrative_request"
    STAFF_CONTACT = "staff_contact"
    OUT_OF_SCOPE = "out_of_scope"


class SearchSource(StrEnum):
    COURSE_MATERIALS = "course_materials"
    UNIVERSITY_POLICIES = "university_policies"
    HISTORICAL_QNA = "historical_qna"
    CANVAS= "canvas"


class SearchResult(StrictModel):
    source: SearchSource
    content: str = Field(min_length=1)
    title: str | None = None
    source_url: str | None = None
    excerpt: str | None = None


class SearchResults(StrictModel):
    query: str = Field(min_length=1)
    results: list[SearchResult]


class IntentClassification(StrictModel):
    """MVP routing decision produced by the student-question intent classifier."""

    intent: StudentIntent
    search_sources: list[SearchSource] = Field(
        description="Search sources needed to answer the question; use an empty list when no search is needed."
    )
    search_query: str | None = Field(
        default=None,
        description="Standalone query optimized for retrieval. Use null when no search is needed."
    )
    needs_clarification: bool
    clarification_question: str | None = None

class ChatMessage(StrictModel):
    role: Literal["user", "assistant"]
    content: str = Field(min_length=1)
    created_at: datetime | None = None


class ReplyRequest(StrictModel):
    """Internal input for initial reply orchestration; not an HTTP request model."""

    question: str = Field(min_length=1)
    user_id: str
    course_id: str
    chat_history: list[ChatMessage] = Field(default_factory=list)

class ReplyResponse(StrictModel):
    """Internal generated answer used by orchestration and evidence checks."""

    reply: str
    status: Literal[
        "answered",
        "needs_clarification",
        "not_found",
        "escalated",
        "error",
    ]
    citations: list[SearchResult]


class HTTPErrorResponse(StrictModel):
    """Error body returned by HTTPException for reply-session operations."""

    detail: str


class ReplyGeneration(StrictModel):
    """An answer and the evidence to persist atomically with its first revision."""

    response: ReplyResponse
    intent: IntentClassification
    retrieved_context: list[SearchResult]


class SessionStatus(StrEnum):
    PENDING_GENERATION = "pending_generation"
    AWAITING_REVIEW = "awaiting_review"
    REVISION_REQUESTED = "revision_requested"
    ACCEPTING = "accepting"
    ACCEPTED = "accepted"


class Revision(StrictModel):
    revision_number: int = Field(ge=1)
    feedback: str | None = None
    revision_content: str | None = None


class ReplySession(StrictModel):
    conversation_id: str
    course_id: str = Field(min_length=1)
    user_id: str | None = None
    student_query: str = Field(min_length=1)
    status: SessionStatus = SessionStatus.PENDING_GENERATION
    revisions: list[Revision] = Field(default_factory=list)
    created_at: datetime = Field(default_factory=utc_now)
    expires_at: datetime = Field(default_factory=lambda: utc_now() + SESSION_RETENTION)


class CreateReplySessionRequest(StrictModel):
    question: str = Field(min_length=1)
    course_id: str = Field(min_length=1)
    user_id: str | None = None


class ReviewReplySessionRequest(StrictModel):
    feedback: str = Field(min_length=1)
