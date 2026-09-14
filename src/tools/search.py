import asyncio
from collections.abc import Awaitable, Callable

from src.logger import get_logger, log_operation
from src.models import SearchResults, SearchSource
from src.tools.search_providers import (
    azure_ai_search,
    canvas_search,
    historical_qna_search,
    public_website_search,
)


SearchMethod = Callable[[str, str], Awaitable[SearchResults]]
logger = get_logger(__name__)


async def _run_source(
    source: SearchSource, method: SearchMethod, query: str, course_id: str,
) -> SearchResults:
    with log_operation(logger, "retrieval.source", source=source, course_id=course_id) as event:
        response = await method(query, course_id)
        event["result_count"] = len(response.results)
        return response


async def _search_course_materials(query: str, course_id: str) -> SearchResults:
    return await azure_ai_search.search(query, course_id)


async def _search_university_policies(query: str, course_id: str) -> SearchResults:
    return await public_website_search.search(query, course_id)


async def _search_canvas(query: str, course_id: str) -> SearchResults:
    return await canvas_search.search(query, course_id)


async def _search_historical_qna(query: str, course_id: str) -> SearchResults:
    return await historical_qna_search.search(query, course_id)


_SEARCH_METHODS: dict[SearchSource, SearchMethod] = {
    SearchSource.COURSE_MATERIALS: _search_course_materials,
    SearchSource.UNIVERSITY_POLICIES: _search_university_policies,
    SearchSource.CANVAS: _search_canvas,
    SearchSource.HISTORICAL_QNA: _search_historical_qna,
}


async def search(
    query: str,
    search_sources: list[SearchSource | str],
    course_id: str,
) -> SearchResults:
    """Run and combine all requested search methods for a query.

    This is the only search entry point intended for callers outside this package.
    Unknown source values are ignored.
    """
    methods: list[tuple[SearchSource, SearchMethod]] = []
    selected_sources: set[SearchSource] = set()
    for search_source in search_sources:
        try:
            source = SearchSource(search_source)
        except ValueError:
            continue

        method = _SEARCH_METHODS.get(source)
        if method is not None and source not in selected_sources:
            methods.append((source, method))
            selected_sources.add(source)

    with log_operation(
        logger, "retrieval.search", course_id=course_id, source_count=len(methods),
    ) as event:
        responses = await asyncio.gather(*(
            _run_source(source, method, query, course_id) for source, method in methods
        ))
        results = [result for response in responses for result in response.results]
        event["result_count"] = len(results)
        return SearchResults(query=query, results=results)
