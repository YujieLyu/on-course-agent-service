"""Create or validate the durable reply-session container."""

from azure.cosmos import CosmosClient, PartitionKey

from src.config import REPLY_SESSION_RETENTION_SECONDS, load_cosmos_session_settings
from src.repositories.reply_sessions import validate_session_container


def main() -> None:
    settings = load_cosmos_session_settings()
    with CosmosClient(settings["endpoint"], credential=settings["key"]) as client:
        database = client.get_database_client(settings["database_name"])
        container = database.create_container_if_not_exists(
            id=settings["container_name"],
            partition_key=PartitionKey(path="/id"),
            default_ttl=REPLY_SESSION_RETENTION_SECONDS,
            indexing_policy={
                "indexingMode": "consistent", "automatic": True,
                "includedPaths": [{"path": "/*"}],
                "excludedPaths": [
                    {"path": '/"_etag"/?'}, {"path": "/revisions/*"},
                    {"path": "/retrieved_context/*"},
                ],
            },
        )
        validate_session_container(container.read())
        print(f"Cosmos session container is configured: {settings['container_name']} (30-day TTL)")


if __name__ == "__main__":
    main()
