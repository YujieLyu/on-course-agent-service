from src.models import SearchResult, SearchResults, SearchSource
from src.repositories import historical_qna as historical_qna_repository
from src.services.embeddings import generate_embedding


async def search(query: str, course_id: str) -> SearchResults:
    query_vector = await generate_embedding(query)
    items = await historical_qna_repository.search_accepted_qna(
        query_vector,
        course_id,
    )
    results = [
        SearchResult(
            source=SearchSource.HISTORICAL_QNA,
            title="Accepted historical Q&A",
            content=(
                f"Student question: {item['student_query']}\n"
                f"Accepted answer: {item['accepted_response']}"
            ),
            excerpt=item["student_query"],
        )
        for item in items
    ]
    return SearchResults(query=query, results=results)
