import asyncio
from functools import cache
from typing import Any

from azure.core.credentials import AzureKeyCredential
from azure.search.documents import SearchClient
from azure.search.documents.models import VectorizedQuery

from src.config import load_ai_search_settings
from src.models import SearchResult, SearchResults, SearchSource
from src.services.embeddings import generate_embedding


async def search(query: str, course_id: str) -> SearchResults:
    del course_id  # Azure AI Search is not currently partitioned by course.
    settings = _get_settings()
    search_client = create_search_client(settings)
    query_vector = await generate_embedding(query)
    vector_query = VectorizedQuery(
        vector=query_vector,
        k_nearest_neighbors=settings["top_k"],
        fields=settings["vector_field"],
    )
    search_kwargs: dict[str, Any] = {
        "search_text": query,
        "vector_queries": [vector_query],
        "search_fields": settings["keyword_fields"],
        "search_mode": "all",
        "top": settings["top_k"],
        "select": [settings["text_field"], settings["source_field"]],
    }
    semantic_config_name = settings["semantic_config_name"]
    if semantic_config_name:
        search_kwargs["query_type"] = "semantic"
        search_kwargs["semantic_configuration_name"] = semantic_config_name
    else:
        search_kwargs["query_type"] = "simple"

    raw_results = await asyncio.to_thread(_run_search, search_client, search_kwargs)
    results = [_map_result(item, settings) for item in raw_results]
    return SearchResults(query=query, results=results)


def create_search_client(search_settings: dict[str, Any]) -> SearchClient:
    return SearchClient(
        endpoint=search_settings["endpoint"],
        index_name=search_settings["index_name"],
        credential=AzureKeyCredential(search_settings["api_key"]),
    )


@cache
def _get_settings() -> dict[str, Any]:
    return load_ai_search_settings()


def _run_search(search_client: Any, search_kwargs: dict[str, Any]) -> list[Any]:
    return list(search_client.search(**search_kwargs))


def _map_result(item: Any, settings: dict[str, Any]) -> SearchResult:
    source_value = item.get(settings["source_field"], "")
    try:
        source = SearchSource(source_value)
    except ValueError:
        source = SearchSource.COURSE_MATERIALS

    return SearchResult(
        source=source,
        content=item.get(settings["text_field"], "") or "",
        title=item.get("title"),
        source_url=item.get("source_url"),
        excerpt=item.get("excerpt"),
    )
