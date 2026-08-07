from datetime import datetime
from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


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
    needs_clarification: bool
    clarification_question: str | None = None

class ChatMessage(StrictModel):
    role: Literal["user", "assistant"]
    content: str = Field(min_length=1)
    created_at: datetime | None


class ReplyRequest(StrictModel):
    question: str = Field(min_length=1)
    user_id: str
    course_id: str
    chat_history: list[ChatMessage]

class ReplyResponse(StrictModel):
    reply: str
    status: Literal[
        "answered",
        "needs_clarification",
        "not_found",
        "escalated",
        "error",
    ]
    citations: list[SearchResult]

