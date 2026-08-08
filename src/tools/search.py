from src.models import SearchResults, SearchSource
from src.tools.search_methods import (
    azure_search_hybrid_search,
    canvas_search,
    history_qna_search,
    web_search,
)

async def search(query: str, search_source: str) -> SearchResults:
    match search_source:
        case SearchSource.COURSE_MATERIALS:
            return await azure_search_hybrid_search(query)
        case SearchSource.UNIVERSITY_POLICIES:
            return await web_search(query)
        case SearchSource.CANVAS:
            return await canvas_search(query)
        case _:
            return await history_qna_search(query)

