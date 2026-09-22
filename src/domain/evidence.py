"""Shared evidence values used by generation and durable session snapshots."""

from collections.abc import Iterable
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field


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
    CANVAS = "canvas"


class SearchResult(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    source: SearchSource
    content: str = Field(min_length=1)
    title: str | None = None
    source_url: str | None = None
    excerpt: str | None = None


def merge_citations(*groups: Iterable[SearchResult]) -> tuple[SearchResult, ...]:
    """Keep the first occurrence of each source passage in citation order."""
    sources: dict[tuple[SearchSource, str | None, str], SearchResult] = {}
    for group in groups:
        for citation in group:
            sources.setdefault((citation.source, citation.source_url, citation.content), citation)
    return tuple(sources.values())


class IntentClassification(BaseModel):
    """MVP routing decision produced by the student-question intent classifier."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    intent: StudentIntent
    search_sources: tuple[SearchSource, ...] = Field(
        description="Search sources needed to answer the question; use an empty list when no search is needed."
    )
    search_query: str | None = Field(
        default=None,
        description="Standalone query optimized for retrieval. Use null when no search is needed."
    )
    needs_clarification: bool
    clarification_question: str | None = None
