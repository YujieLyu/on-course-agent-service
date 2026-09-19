"""Cosmos serialization, TTL, conditional writes, and SDK error translation."""

import math
from functools import cache
from typing import Any

from azure.core import MatchConditions
from azure.core.exceptions import AzureError
from azure.cosmos import ContainerProxy, CosmosClient
from azure.cosmos.exceptions import CosmosHttpResponseError, CosmosResourceNotFoundError

from src.config import REPLY_SESSION_RETENTION_SECONDS, load_cosmos_session_settings
from src.domain.reply_sessions import (
    AcceptedReply, Clock, SessionConflictError, SessionNotFoundError, SessionSnapshot,
    StorageUnavailableError,
)
from src.domain.retention import utc_now
from src.models import IntentClassification, ReplySession, SearchResult


def validate_session_container(properties: dict) -> None:
    if properties.get("partitionKey", {}).get("paths") != ["/id"]:
        raise RuntimeError("Reply session container must use /id as its partition key")
    if properties.get("defaultTtl") != REPLY_SESSION_RETENTION_SECONDS:
        raise RuntimeError("Reply session container must have a default TTL of 2592000 seconds")


@cache
def _get_container() -> ContainerProxy:
    settings = load_cosmos_session_settings()
    client = CosmosClient(settings["endpoint"], credential=settings["key"])
    container = client.get_database_client(settings["database_name"]).get_container_client(
        settings["container_name"]
    )
    try:
        validate_session_container(container.read())
    except CosmosResourceNotFoundError as error:
        client.close()
        raise StorageUnavailableError("Reply session container is not provisioned") from error
    except Exception:
        client.close()
        raise
    return container


class CosmosReplySessionRepository:
    def __init__(self, container: ContainerProxy | None = None, *, clock: Clock = utc_now) -> None:
        self._container = container
        self.clock = clock

    @property
    def container(self) -> ContainerProxy:
        return self._container if self._container is not None else _get_container()

    def create(self, session: ReplySession) -> SessionSnapshot:
        document = self._document(session)
        try:
            return self._snapshot(self.container.create_item(body=document))
        except CosmosHttpResponseError as error:
            if error.status_code == 409:
                raise SessionConflictError("Session already exists") from error
            raise StorageUnavailableError("Could not create reply session") from error
        except AzureError as error:
            raise StorageUnavailableError("Could not create reply session") from error

    def read(self, conversation_id: str) -> SessionSnapshot:
        try:
            return self._snapshot(self.container.read_item(item=conversation_id, partition_key=conversation_id))
        except CosmosResourceNotFoundError as error:
            raise SessionNotFoundError(conversation_id) from error
        except AzureError as error:
            raise StorageUnavailableError("Could not read reply session") from error

    def replace(self, snapshot: SessionSnapshot) -> SessionSnapshot:
        document = self._document(snapshot.session)
        document.update(
            intent_context=snapshot.intent_context.model_dump(mode="json") if snapshot.intent_context else None,
            retrieved_context=[result.model_dump(mode="json") for result in snapshot.retrieved_context],
            acceptance=snapshot.acceptance.model_dump(mode="json") if snapshot.acceptance else None,
        )
        try:
            return self._snapshot(self.container.replace_item(
                item=snapshot.session.conversation_id, body=document,
                etag=snapshot.version, match_condition=MatchConditions.IfNotModified,
            ))
        except CosmosResourceNotFoundError as error:
            raise SessionNotFoundError(snapshot.session.conversation_id) from error
        except CosmosHttpResponseError as error:
            if error.status_code == 412:
                raise SessionConflictError("Session changed; fetch it before retrying") from error
            raise StorageUnavailableError("Could not save reply session") from error
        except AzureError as error:
            raise StorageUnavailableError("Could not save reply session") from error

    def pending_ids(self, limit: int) -> list[str]:
        try:
            return list(self.container.query_items(
                query=("SELECT TOP @limit VALUE c.id FROM c "
                       "WHERE c.document_type = 'reply_session' AND c.status = 'accepting' "
                       "AND c.expires_at > @now"),
                parameters=[{"name": "@limit", "value": limit},
                            {"name": "@now", "value": self.clock().isoformat()}],
                enable_cross_partition_query=True,
            ))
        except AzureError as error:
            raise StorageUnavailableError("Could not list pending acceptances") from error

    def _document(self, session: ReplySession) -> dict[str, Any]:
        remaining = (session.expires_at - self.clock()).total_seconds()
        if remaining <= 0:
            raise SessionNotFoundError(session.conversation_id)
        return {
            "id": session.conversation_id, "document_type": "reply_session",
            **session.model_dump(mode="json"),
            "intent_context": None, "retrieved_context": [], "acceptance": None,
            "ttl": math.ceil(remaining),
        }

    @staticmethod
    def _snapshot(document: dict[str, Any]) -> SessionSnapshot:
        return SessionSnapshot(
            session=ReplySession.model_validate({
                key: document[key] for key in ReplySession.model_fields if key in document
            }),
            version=document["_etag"],
            intent_context=IntentClassification.model_validate(document["intent_context"])
            if document.get("intent_context") is not None else None,
            retrieved_context=tuple(SearchResult.model_validate(item) for item in document.get("retrieved_context", [])),
            acceptance=AcceptedReply.model_validate(document["acceptance"])
            if document.get("acceptance") is not None else None,
        )
