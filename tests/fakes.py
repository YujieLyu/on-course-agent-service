"""Domain repository and Cosmos boundary fakes; production always uses Cosmos DB."""

from copy import deepcopy
from dataclasses import replace
from threading import Lock

from azure.core import MatchConditions
from azure.cosmos.exceptions import CosmosHttpResponseError, CosmosResourceNotFoundError

from src.models import IntentClassification, ReplyGeneration, ReplyResponse, StudentIntent, SessionStatus
from src.domain.reply_sessions import SessionConflictError, SessionNotFoundError, SessionSnapshot
from src.domain.retention import utc_now
from src.repositories.reply_sessions import CosmosReplySessionRepository
from src.services.reply_sessions import ReplySessionService


class FakeSessionContainer:
    def __init__(self, partition_field: str = "id") -> None:
        self.partition_field = partition_field
        self.documents: dict[str, dict] = {}
        self.lock = Lock()
        self.version = 0

    def _write(self, body: dict) -> dict:
        self.version += 1
        document = deepcopy(body)
        document["_etag"] = str(self.version)
        self.documents[body["id"]] = document
        return deepcopy(document)

    def create_item(self, *, body: dict) -> dict:
        with self.lock:
            if body["id"] in self.documents:
                raise CosmosHttpResponseError(status_code=409)
            return self._write(body)

    def read_item(self, *, item: str, partition_key: str) -> dict:
        if self.partition_field == "id":
            assert partition_key == item
        with self.lock:
            if item not in self.documents:
                raise CosmosResourceNotFoundError(status_code=404)
            assert self.documents[item][self.partition_field] == partition_key
            return deepcopy(self.documents[item])

    def replace_item(self, *, item: str, body: dict, etag: str, match_condition) -> dict:
        assert match_condition == MatchConditions.IfNotModified
        with self.lock:
            if item not in self.documents:
                raise CosmosResourceNotFoundError(status_code=404)
            if self.documents[item]["_etag"] != etag:
                raise CosmosHttpResponseError(status_code=412)
            return self._write(body)


class InMemorySessionRepository:
    """Business-port fake: no Cosmos document format or Azure errors."""

    def __init__(self):
        self.snapshots = {}
        self.lock = Lock()
        self.version = 0

    def create(self, session):
        with self.lock:
            if session.conversation_id in self.snapshots:
                raise SessionConflictError("Session already exists")
            return self._write(SessionSnapshot(session, ""))

    def read(self, conversation_id):
        with self.lock:
            if conversation_id not in self.snapshots:
                raise SessionNotFoundError(conversation_id)
            return self.snapshots[conversation_id]

    def replace(self, snapshot):
        with self.lock:
            current = self.snapshots.get(snapshot.session.conversation_id)
            if current is None:
                raise SessionNotFoundError(snapshot.session.conversation_id)
            if current.version != snapshot.version:
                raise SessionConflictError("Session changed")
            return self._write(snapshot)

    def _write(self, snapshot):
        self.version += 1
        saved = replace(snapshot, version=str(self.version))
        self.snapshots[snapshot.session.conversation_id] = saved
        return saved

    def pending_ids(self, limit):
        with self.lock:
            return [key for key, value in self.snapshots.items()
                    if value.session.status == SessionStatus.ACCEPTING][:limit]


def make_session_service(container: FakeSessionContainer | None = None, *, clock=utc_now) -> ReplySessionService:
    repository = CosmosReplySessionRepository(container, clock=clock) if container is not None else InMemorySessionRepository()
    return ReplySessionService(repository, clock=clock)


def generation_result(response: ReplyResponse) -> ReplyGeneration:
    return ReplyGeneration(
        response=response,
        intent=IntentClassification(
            intent=StudentIntent.OUT_OF_SCOPE, search_sources=[], needs_clarification=False,
        ),
        retrieved_context=[],
    )
