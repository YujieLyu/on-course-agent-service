from src.models import SearchResults


async def search(query: str, course_id: str) -> SearchResults:
    del course_id
    return SearchResults(query=query, results=[])
