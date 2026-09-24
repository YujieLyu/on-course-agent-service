"""Shared evidence values used by generation and durable session snapshots."""

from collections.abc import Iterable
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field


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
