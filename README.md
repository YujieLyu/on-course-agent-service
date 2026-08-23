# On Course Agent Service

A FastAPI service that generates grounded answers to student questions. It uses
an LLM to classify each question, retrieves relevant context from the configured
sources, and returns a structured answer with citations. It also supports a
teacher review workflow in which replies can be revised and accepted for reuse
as historical Q&A.

## Features

- Intent classification and source selection for student questions
- Grounded answer generation with structured citations
- Hybrid keyword/vector retrieval from Azure AI Search
- University policy retrieval from configured public web pages
- Course-scoped historical Q&A retrieval using Azure Cosmos DB vector search
- Multi-turn reply generation, review, revision, and acceptance
- OpenAPI documentation provided by FastAPI

Canvas is represented as a search source, but its provider is currently a stub
and returns no results. Reply sessions are stored in process memory, so they are
lost when the service restarts and are not shared between workers.

## Prerequisites

- Python 3.12 or later
- [`uv`](https://docs.astral.sh/uv/getting-started/installation/) for dependency
  and virtual-environment management
- An OpenAI-compatible Responses API endpoint and API key
- For course-material search: an Azure AI Search index containing text and
  vector fields
- For accepted historical Q&A: an Azure Cosmos DB for NoSQL account with vector
  search enabled

Azure AI Search and Cosmos DB are only contacted when the intent classifier
selects their corresponding source, or when an accepted reply is persisted.
However, a complete local environment should configure both because source
selection happens dynamically.

## Setup

Clone the repository, enter its directory, and install the locked dependencies:

```bash
uv sync
```

Create a `.env` file in the repository root. `python-dotenv` loads this file
automatically when the application starts:

```dotenv
# LLM and embeddings
OPENAI_ENDPOINT=https://api.openai.com/v1
OPENAI_API_KEY=<api-key>
INTENT_CLASSIFIER_MODEL_NAME=<model-or-deployment-name>
REPLY_MODEL_NAME=<model-or-deployment-name>
OPENAI_EMBEDDING_MODEL=text-embedding-3-small

# Azure AI Search (course materials)
AZURE_SEARCH_ENDPOINT=https://<search-service>.search.windows.net
AZURE_SEARCH_KEY=<search-key>
AZURE_SEARCH_INDEX_NAME=<index-name>
AZURE_SEARCH_VECTOR_FIELD=content_vector
AZURE_SEARCH_TEXT_FIELD=content
AZURE_SEARCH_SOURCE_FIELD=source
AZURE_SEARCH_KEYWORD_FIELDS=title,content
AZURE_SEARCH_TOP_K=3
AZURE_SEARCH_SEMANTIC_CONFIG_NAME=

# Public university policy pages (comma-separated)
PUBLIC_WEBSITE_URLS=https://example.edu/policy-one,https://example.edu/policy-two
WEB_SEARCH_TOP_K=3

# Azure Cosmos DB (accepted historical Q&A)
AZURE_COSMOS_ENDPOINT=https://<account>.documents.azure.com:443/
AZURE_COSMOS_KEY=<cosmos-key>
AZURE_COSMOS_DATABASE_NAME=course-brain
AZURE_COSMOS_QA_MEMORY_CONTAINER=qa_memory
AZURE_COSMOS_QNA_TOP_K=3
AZURE_COSMOS_EMBEDDING_DIMENSIONS=1536

# Optional logging overrides
LOG_LEVEL=INFO
```

If the endpoint uses deployment names, set the model variables to those
deployment names. `AZURE_EMBEDDING_MODEL_DEPLOYMENT_NAME` may be used instead of
`OPENAI_EMBEDDING_MODEL` and takes precedence when both are set.

The embedding dimensions must match both the selected embedding model and the
vector fields in Azure AI Search and Cosmos DB. For example,
`text-embedding-3-small` returns 1536 dimensions by default, while
`text-embedding-3-large` returns 3072 dimensions by default.

Do not commit `.env`; it is already ignored by Git.

### Provision historical Q&A storage

After enabling vector search for the Cosmos DB account and setting the Cosmos
environment variables, create or validate the database and container:

```bash
uv run python -m scripts.provision_cosmos_qna
```

See [docs/historical-qna-cosmos.md](docs/historical-qna-cosmos.md) for the Azure
setup details, vector policy, and production considerations.

## Run the service

Start the development server with automatic reload:

```bash
uv run fastapi dev main.py
```

The service is available at `http://127.0.0.1:8000`. Useful URLs are:

- Health check: `http://127.0.0.1:8000/health`
- Swagger UI: `http://127.0.0.1:8000/docs`
- ReDoc: `http://127.0.0.1:8000/redoc`

For a production-style local process without reload:

```bash
uv run fastapi run main.py --host 0.0.0.0 --port 8000
```

## API usage

### Generate a reply directly

```bash
curl -X POST http://127.0.0.1:8000/reply \
  -H 'Content-Type: application/json' \
  -d '{
    "question": "When is Assignment 1 due?",
    "user_id": "student-123",
    "course_id": "COSC1234-2026-S2",
    "chat_history": []
  }'
```

The response contains `reply`, `status`, and `citations`. Possible status values
are `answered`, `needs_clarification`, `not_found`, `escalated`, and `error`.

### Generate and review a reply

Create a session:

```bash
curl -X POST http://127.0.0.1:8000/reply-sessions \
  -H 'Content-Type: application/json' \
  -d '{
    "question": "When is Assignment 1 due?",
    "course_id": "COSC1234-2026-S2",
    "user_id": "student-123"
  }'
```

Copy the returned `conversation_id`, then generate the first reply:

```bash
curl -X POST \
  http://127.0.0.1:8000/reply-sessions/<conversation_id>/generate
```

Request a revision if needed, then call `/generate` again:

```bash
curl -X POST \
  http://127.0.0.1:8000/reply-sessions/<conversation_id>/review \
  -H 'Content-Type: application/json' \
  -d '{"feedback":"Make the answer shorter and include the late penalty."}'

curl -X POST \
  http://127.0.0.1:8000/reply-sessions/<conversation_id>/generate
```

Accept the final reply and persist it to the course's historical Q&A collection:

```bash
curl -X POST \
  http://127.0.0.1:8000/reply-sessions/<conversation_id>/accept
```

Retrieve the current session at any point with:

```bash
curl http://127.0.0.1:8000/reply-sessions/<conversation_id>
```

The expected session progression is:

```text
pending_generation -> awaiting_review -> accepted
                              |
                              v
                    revision_requested
                              |
                              v
                       awaiting_review
```

## Tests

The test suite uses Python's built-in `unittest` runner. External OpenAI, Azure
AI Search, Cosmos DB, and website calls are mocked, so the tests do not require
credentials or network access.

Run all tests:

```bash
uv run python -m unittest discover -s tests -t . -v
```

Run a single suite:

```bash
uv run python -m unittest discover -s tests/unit -t . -v
uv run python -m unittest discover -s tests/integration -t . -v
```

See [tests/README.md](tests/README.md) for the test boundaries and conventions.

## Project structure

```text
main.py                         FastAPI routes and application entry point
src/agents/                     Intent classification and reply generation
src/prompts/                    LLM system prompts
src/services/                   Embedding and historical Q&A services
src/repositories/               Persistence access
src/tools/search.py             Multi-source search orchestration
src/tools/search_providers/     Azure, website, Cosmos, and Canvas providers
scripts/provision_cosmos_qna.py Cosmos DB provisioning utility
tests/unit/                     Isolated component tests
tests/integration/              End-to-end internal request-flow tests
```
