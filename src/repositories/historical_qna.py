import asyncio
from functools import cache
from typing import Any

from azure.core import MatchConditions
from azure.core.exceptions import AzureError
from azure.cosmos import ContainerProxy, CosmosClient
from azure.cosmos.exceptions import CosmosHttpResponseError, CosmosResourceNotFoundError

from src.config import load_cosmos_qna_settings
from src.logger import get_logger, log_operation
from src.domain.reply_sessions import AcceptedQna, QaMemoryConflictError, StorageUnavailableError

logger = get_logger(__name__)


@cache
def _get_container() -> ContainerProxy:
    settings = load_cosmos_qna_settings()
    client = CosmosClient(settings["endpoint"], credential=settings["key"])
    database = client.get_database_client(settings["database_name"])
    return database.get_container_client(settings["container_name"])


class CosmosQaMemoryRepository:
    def __init__(self, container: ContainerProxy | None = None) -> None:
        self._container = container

    @property
    def container(self) -> ContainerProxy:
        return self._container if self._container is not None else _get_container()

    def save(self, item: AcceptedQna) -> None:
        reply = item.reply
        document = {
            "id": reply.conversation_id, "document_type": "historical_qna",
            **reply.model_dump(mode="json", exclude={"conversation_id"}),
            "question_vector": list(item.question_vector),
        }
        with log_operation(
            logger, "cosmos.qna.save", conversation_id=reply.conversation_id, course_id=reply.course_id,
        ):
            try:
                self._save_versioned(document)
            except AzureError as error:
                raise StorageUnavailableError("Could not save accepted QA Memory") from error

    def _save_versioned(self, document: dict[str, Any]) -> None:
        container = self.container
        # Bounded retries only reconcile conditional-write races. An older
        # revision or different content at the same revision is never retried.
        for _ in range(3):
            try:
                existing = container.read_item(item=document["id"], partition_key=document["course_id"])
            except CosmosResourceNotFoundError:
                try:
                    container.create_item(body=document)
                    return
                except CosmosHttpResponseError as error:
                    if error.status_code == 409:
                        continue
                    raise

            if existing.get("document_type") != "historical_qna":
                raise QaMemoryConflictError("QA Memory ID belongs to a different document type")
            existing_revision = existing["revision_number"]
            if existing_revision > document["revision_number"]:
                raise QaMemoryConflictError("A newer accepted revision already exists in QA Memory")
            if existing_revision == document["revision_number"]:
                if any(existing[key] != document[key] for key in ("course_id", "student_query", "accepted_response")):
                    raise QaMemoryConflictError("QA Memory contains different content for this revision")
                return
            try:
                container.replace_item(
                    item=document["id"], body=document, etag=existing["_etag"],
                    match_condition=MatchConditions.IfNotModified,
                )
                return
            except CosmosHttpResponseError as error:
                if error.status_code in (404, 412):
                    continue
                raise
        raise StorageUnavailableError("QA Memory changed repeatedly; retry the pending acceptance")


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
    with log_operation(logger, "cosmos.qna.search", course_id=course_id) as event:
        items = await asyncio.to_thread(
            lambda: list(
                _get_container().query_items(
                    query=sql,
                    parameters=parameters,
                    partition_key=course_id,
                )
            )
        )
        event["result_count"] = len(items)
        return items
