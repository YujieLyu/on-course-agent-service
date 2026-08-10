"""Create or validate the Cosmos DB container used for historical Q&A."""

from azure.cosmos import CosmosClient, PartitionKey
from azure.cosmos.exceptions import CosmosResourceExistsError

from src.config import load_cosmos_qna_settings


def main() -> None:
    settings = load_cosmos_qna_settings()
    client = CosmosClient(settings["endpoint"], credential=settings["key"])
    database = client.create_database_if_not_exists(settings["database_name"])
    indexing_policy = {
        "indexingMode": "consistent",
        "automatic": True,
        "includedPaths": [{"path": "/*"}],
        "excludedPaths": [
            {"path": '/"_etag"/?'},
            {"path": "/question_vector/*"},
        ],
        "vectorIndexes": [
            {"path": "/question_vector", "type": "quantizedFlat"}
        ],
    }
    vector_embedding_policy = {
        "vectorEmbeddings": [
            {
                "path": "/question_vector",
                "dataType": "float32",
                "distanceFunction": "cosine",
                "dimensions": settings["embedding_dimensions"],
            }
        ]
    }

    try:
        database.create_container(
            id=settings["container_name"],
            partition_key=PartitionKey(path="/course_id"),
            indexing_policy=indexing_policy,
            vector_embedding_policy=vector_embedding_policy,
        )
        print(f"Created Cosmos container: {settings['container_name']}")
    except CosmosResourceExistsError:
        container = database.get_container_client(settings["container_name"])
        properties = container.read()
        _validate_existing_container(properties, settings["embedding_dimensions"])
        print(f"Cosmos container is already configured: {settings['container_name']}")


def _validate_existing_container(properties: dict, dimensions: int) -> None:
    partition_paths = properties.get("partitionKey", {}).get("paths", [])
    embeddings = properties.get("vectorEmbeddingPolicy", {}).get(
        "vectorEmbeddings", []
    )
    vector_indexes = properties.get("indexingPolicy", {}).get("vectorIndexes", [])
    expected_embedding = {
        "path": "/question_vector",
        "dataType": "float32",
        "distanceFunction": "cosine",
        "dimensions": dimensions,
    }
    if partition_paths != ["/course_id"]:
        raise RuntimeError("Existing container must use /course_id as partition key")
    if expected_embedding not in embeddings:
        raise RuntimeError(
            "Existing container has a different vector embedding policy; "
            "create a new container because vector policies cannot be changed in place"
        )
    if not any(
        item.get("path") == "/question_vector" for item in vector_indexes
    ):
        raise RuntimeError("Existing container has no /question_vector vector index")


if __name__ == "__main__":
    main()
