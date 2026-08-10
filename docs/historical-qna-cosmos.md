# Historical Q&A with Azure Cosmos DB

The service stores only teacher-accepted replies. Each item uses `conversation_id`
as its ID, so a retried accept request upserts the same item instead of creating a
duplicate. Searches are restricted to the current `course_id` partition.

## 1. Enable Cosmos DB vector search

Create an Azure Cosmos DB for NoSQL account, then enable vector search in the
Azure portal under **Settings > Features > Vector Search for NoSQL API**, or run:

```bash
az cosmosdb update \
  --resource-group <resource-group> \
  --name <account-name> \
  --capabilities EnableNoSQLVectorSearch
```

Wait until the capability is active before creating the vector container.

## 2. Configure the service

Set these variables in `.env` or in the deployment environment:

```dotenv
AZURE_COSMOS_ENDPOINT=https://<account>.documents.azure.com:443/
AZURE_COSMOS_KEY=<secret-from-Key-Vault-or-app-setting>
AZURE_COSMOS_DATABASE_NAME=course-brain
AZURE_COSMOS_QA_MEMORY_CONTAINER=qa_memory
AZURE_COSMOS_QNA_TOP_K=3
AZURE_COSMOS_EMBEDDING_DIMENSIONS=3072
```

`AZURE_COSMOS_EMBEDDING_DIMENSIONS` must exactly match the embedding model used by
`AZURE_EMBEDDING_MODEL_DEPLOYMENT_NAME`. The checked-in default is 3072 for the
default output of `text-embedding-3-large`; use 1536 for
`text-embedding-3-small`.

## 3. Create the database and vector container

After enabling vector search and loading the environment variables, run:

```bash
uv run python -m scripts.provision_cosmos_qna
```

The container is created with:

- partition key `/course_id`;
- vector path `/question_vector`;
- cosine distance and `float32` values;
- a `quantizedFlat` vector index;
- the vector path excluded from the normal JSON index.

Use a new container name if an existing `qa_memory` container was created without
the vector policy. Cosmos vector policies must be applied when the container is
created and cannot be changed in place.

## 4. API flow

Create the review session with course context:

```json
{
  "question": "When is Assignment 1 due?",
  "course_id": "COSC1234-2026-S2",
  "user_id": "student-123"
}
```

Continue using `/generate`, `/review`, and `/accept`. On `/accept`, the service:

1. verifies that the session is awaiting review;
2. embeds the original student query;
3. upserts the query and final revision into Cosmos DB;
4. marks the in-memory session as `accepted` only after the write succeeds.

The intent classifier can already select `historical_qna`. The orchestration layer
now passes `course_id` to that search source, which executes a partition-scoped
`VectorDistance` query and returns the accepted Q&A as `SearchResult` objects.

## Production notes

- Put `AZURE_COSMOS_KEY` in Key Vault or the platform's secret store; do not commit
  it. Managed identity/RBAC is preferable when the deployment supports it.
- Historical answers are supporting context, not authoritative policy or current
  course material. The reply prompt already encodes this precedence.
- The response-session store remains process-local. Persist sessions separately
  before running multiple workers or requiring restart-safe review workflows.
- `user_id` is used by the live request flow but is deliberately not copied into
  historical Q&A documents. Define retention/deletion rules for the query text as
  part of privacy compliance.
