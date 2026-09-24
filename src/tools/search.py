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
SOURCE_TIMEOUT_SECONDS = 30


async def _run_source(
    source: SearchSource, method: SearchMethod, query: str, course_id: str,
) -> SearchResults:
    with log_operation(logger, "retrieval.source", source=source, course_id=course_id) as event:
        try:
            async with asyncio.timeout(SOURCE_TIMEOUT_SECONDS):
                response = await method(query, course_id)
        except Exception as error:
            # A source outage must not discard results from the other providers.
            event.update(outcome="unavailable", error_type=type(error).__name__)
            logger.warning("retrieval.source.unavailable", extra={"event_fields": {
                "source": source, "error_type": type(error).__name__,
            }})
            return SearchResults(query=query, results=[])
        event["result_count"] = len(response.results)
        return response


_SEARCH_METHODS: dict[SearchSource, SearchMethod] = {
    SearchSource.COURSE_MATERIALS: azure_ai_search.search,
    SearchSource.UNIVERSITY_POLICIES: public_website_search.search,
    SearchSource.CANVAS: canvas_search.search,
    SearchSource.HISTORICAL_QNA: historical_qna_search.search,
}


async def search(
    query: str,
    course_id: str,
) -> SearchResults:
    """Search all four providers concurrently; retain results from available sources."""
    with log_operation(
        logger, "retrieval.search", course_id=course_id, source_count=len(_SEARCH_METHODS),
    ) as event:
        responses = await asyncio.gather(*(
            _run_source(source, method, query, course_id) for source, method in _SEARCH_METHODS.items()
        ))
        results = [result for response in responses for result in response.results]
        event["result_count"] = len(results)
        return SearchResults(query=query, results=results)
