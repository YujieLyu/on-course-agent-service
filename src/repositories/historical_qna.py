import asyncio
from functools import cache
from typing import Any

from azure.cosmos import CosmosClient

from src.config import load_cosmos_qna_settings


@cache
def _get_container() -> Any:
    settings = load_cosmos_qna_settings()
    client = CosmosClient(settings["endpoint"], credential=settings["key"])
    database = client.get_database_client(settings["database_name"])
    return database.get_container_client(settings["container_name"])


async def upsert_accepted_qna(item: dict[str, Any]) -> None:
    await asyncio.to_thread(_get_container().upsert_item, item)


async def search_accepted_qna(
    query_vector: list[float],
    course_id: str,
) -> list[dict[str, Any]]:
    settings = load_cosmos_qna_settings()
    sql = f"""
        SELECT TOP {settings['top_k']}
            c.id,
            c.student_query,
            c.accepted_response,
            VectorDistance(c.question_vector, @embedding) AS similarity_score
        FROM c
        WHERE c.course_id = @course_id
          AND c.document_type = "historical_qna"
        ORDER BY VectorDistance(c.question_vector, @embedding)
    """
    parameters = [
        {"name": "@embedding", "value": query_vector},
        {"name": "@course_id", "value": course_id},
    ]
    return await asyncio.to_thread(
        lambda: list(
            _get_container().query_items(
                query=sql,
                parameters=parameters,
                partition_key=course_id,
            )
        )
    )
