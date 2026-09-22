# On Course Agent Service

A FastAPI service that generates grounded answers to student questions. It uses
an LLM to classify each question, retrieves relevant context from the configured
sources, and generates a draft in a reply session. Every reply supports teacher
feedback and repeated revision before acceptance for reuse as historical Q&A. Acceptance pins the reviewed
revision before publishing it and can be resumed after a failure.

## Features

- Intent classification and source selection for student questions
- General explanations, problem-solving help, and practical guidance without mandatory retrieval
- Course-aware draft generation with citations for retrieved information
- Hybrid keyword/vector retrieval from Azure AI Search
- University policy retrieval from configured public web pages
- Course-scoped historical Q&A retrieval using Azure Cosmos DB vector search
- Multi-turn reply generation, review, revision, and acceptance
- OpenAPI documentation provided by FastAPI

Canvas is represented as a search source, but its provider is currently a stub
and returns no results. Reply sessions and their evidence are stored in the
Cosmos DB `reply_sessions` container. They survive service restarts and are
shared between workers, and expire 30 days after creation.

## Answer generation

Ordinary conceptual questions, self-contained exercises, programming help, and
study advice can use the model's general knowledge without retrieved evidence.
The intent classifier skips retrieval when the question does not depend on
course-specific information. Empty or irrelevant search results do not prevent
a general answer.

Course-specific details such as deadlines, assessment formats, grading rules,
and university policies still require supplied course information or explicit
teacher confirmations in the prompt. Partial answers retain useful information
and identify missing details, with `answered` status. `not_found` is reserved
for unavailable course-specific information when no substantive part can be
answered. Missing references to an exam do not establish that there is no exam.

The draft text is returned without a separate evidence-review model call,
quote-matching gate, or fixed fallback replacement. The model selects source IDs
such as `source_1` in an internal `cited_source_ids` field. The service resolves
those IDs to original retrieved `SearchResult` objects, preserving their text,
URLs, titles, and excerpts. Unknown IDs are ignored and their count is logged;
they do not block the answer. General-knowledge answers may have empty citations.
Factual accuracy and whether a selected source supports the answer still rely
on the generation prompt and teacher review. Initial generation uses one
classification call and one answer call; a revision uses one answer call.

Source IDs follow the retrieval order and remain stable when that saved snapshot
is reused. Session revisions reuse a persisted snapshot of the original retrieval and
may use explicit factual confirmations from teacher feedback. Previous assistant
drafts and requests to change tone are not evidence. The public API returns the
session with revision history and a top-level `citations` list. Citations
accumulate sources actually used across generated replies, deduplicated by
source type, URL, and source text while preserving their first occurrence.
Review and answers without new citations retain the accumulated list. New
sessions and older records without citations return `[]`; legacy per-revision
citations are merged on read. Internal answer statuses and the complete
retrieval snapshot are not included.

## Prerequisites

- Python 3.12 or later
- [`uv`](https://docs.astral.sh/uv/getting-started/installation/) for dependency
  and virtual-environment management
- An OpenAI-compatible Responses API endpoint and API key
- For course-material search: an Azure AI Search index containing text and
  vector fields
- For accepted historical Q&A: an Azure Cosmos DB for NoSQL account with vector
  search enabled

Cosmos DB is required for every reply-session operation. Azure AI Search and
the historical Q&A container are queried when selected by the intent classifier;
accepted answers are also persisted to historical Q&A.

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
AZURE_COSMOS_REPLY_SESSIONS_CONTAINER=reply_sessions
AZURE_COSMOS_QNA_TOP_K=3
AZURE_COSMOS_EMBEDDING_DIMENSIONS=1536

# Optional logging overrides
LOG_LEVEL=INFO
LOG_FORMAT=json

# Total execution deadline for each MCP tool call, in seconds
MCP_TOOL_TIMEOUT_SECONDS=120
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

## Provision reply-session storage

Create or validate the `reply_sessions` container in the existing database:

```bash
uv run python -m scripts.provision_cosmos_sessions
```

The container uses `/id` as its partition key and a 2,592,000-second default TTL.
Sessions expire 30 days after creation; revision and acceptance do not extend
that deadline. The application persists draft history and evidence together.
`qa_memory` keeps accepted historical Q&A under its separate retention policy.

For pending QA publications after a failure or restart, retry `/accept` or run
`uv run python -m scripts.recover_reply_acceptances --limit 100`. Schedule this
command in your deployment if unattended recovery is required.

See [reply-session persistence](docs/reply-session-persistence.md) for setup,
expiry behavior, and browser recovery using a saved `conversation_id`.

## Run the service

Start the development server with automatic reload:

```bash
uv run fastapi dev main.py
```

The service is available at `http://127.0.0.1:8000`. Useful URLs are:

- Health check: `http://127.0.0.1:8000/health`
- OpenAPI specification: `http://127.0.0.1:8000/openapi.json`
- Swagger UI: `http://127.0.0.1:8000/docs`
- ReDoc: `http://127.0.0.1:8000/redoc`

For a production-style local process without reload:

```bash
uv run fastapi run main.py --host 0.0.0.0 --port 8000
```

## MCP server

The project includes a local STDIO MCP server that exposes the reply-session
API as model-callable tools. It calls the FastAPI application in process, so a
separate HTTP server does not need to be running.

The available tools are:

- `create_reply_session`
- `get_reply_session`
- `generate_session_reply`
- `request_reply_revision`
- `accept_reply_session`

Run the server directly:

```bash
uv run python mcp_server.py
```

To register it with Codex from the repository root:

```bash
codex mcp add on-course-agent -- \
  uv --directory "$PWD" run python mcp_server.py
```

Confirm that Codex can discover it:

```bash
codex mcp list
```

The MCP process loads the same `.env` configuration as the FastAPI service.
Generating replies therefore requires the same OpenAI and retrieval settings
described in [Setup](#setup). Reply sessions are shared with HTTP workers using
the same Cosmos database and session container and last 30 days from creation.

Tool schemas describe the same sessions and revision feedback as the HTTP API.
Create a session, generate its draft, then submit feedback and generate again
as needed. Pass returned conversation IDs unchanged. MCP session tools accept
letters, digits, underscores, and hyphens; URL paths, query strings, and encoded
URL fragments are rejected before execution.

`MCP_TOOL_TIMEOUT_SECONDS` sets a finite, positive execution deadline for each
tool call (default: 120 seconds). The deadline cancels the awaiting operation and
returns a tool error with a request ID. There are no automatic retries. An
external request or synchronous database write already running in a worker
thread can still finish after cancellation. Check session status before retrying
a write; a timeout does not prove that a remote write was rolled back.

To evaluate Streamable HTTP locally using the same tools:

```bash
uv run fastmcp run mcp_server.py:mcp --transport http --host 127.0.0.1 --port 8001
```

Use the `/mcp` endpoint on that port. A shared deployment still needs
authentication and course/session authorization. FastAPI and MCP processes share
sessions through Cosmos DB. Acceptance
should only be called after explicit teacher approval; the tool description is
guidance to the calling agent, not an authorization check.

## Observability

Application logs are JSON lines on stderr by default, keeping stdout available
for the STDIO MCP protocol. `LOG_LEVEL` controls verbosity; `LOG_FORMAT=text`
selects readable text with structured fields appended. Existing custom Python
logging format strings are also supported. FastMCP's transport startup messages
may use its own stderr format.

HTTP requests accept an optional `X-Request-ID` and return it on handled
responses. IDs must contain 1–128 letters, digits, dots, underscores, or hyphens;
otherwise a new ID is generated. Each MCP tool invocation gets a new request ID
that is passed into the internal HTTP request and carried through asynchronous
work and worker threads. Request IDs correlate logs; they do not authenticate
callers.

Events include:

- `mcp.tool.*` and `http.request.*`: tool or route, duration, HTTP status, and
  completion, rejection, timeout, or cancellation outcome.
- `reply.*`, `retrieval.*`, and `llm.*`: routing decisions, source/result counts,
  model calls, token usage when available, and stage durations. Skipped public
  policy pages produce a warning with their configured page index.
- `session.transition`: conversation/course ID, previous and next status, and
  revision number.
- `cosmos.qna.*`: database search/write duration, outcome, and result counts.

Application events omit questions, chat history, generated answers, retrieved
content, credentials, and raw HTTP URLs. Failure logs retain the exception type
and stack locations without exception messages or local variables, since
provider errors can contain submitted data. FastMCP argument-validation logs
are sanitized for the same reason. Independently configured SDK debug logs and
web-server access logs are outside this application logging policy.

For a slow or failed call, find its `request_id`, then inspect stage durations
and the last failure/cancellation event. Session transitions distinguish a
generated draft from an accepted answer; Cosmos write events show persistence
attempts.

## API usage

For frontend integration, see the [API contract](docs/api-contract.md) for wire
types, session transitions, errors, and client examples. The accompanying
[OpenAPI snapshot](docs/openapi.json) can be imported into API tooling.

### Import into Postman

For a ready-to-run test collection covering every endpoint, request bodies,
expected responses, and failure cases, see the [HTTP API test suite](docs/postman-testing.md).

1. Select **Import** in Postman and choose `docs/openapi.json`, or enter
   `http://127.0.0.1:8000/openapi.json` while the service is running.
2. Choose **Specification with a Postman Collection** to view the specification
   and generate requests for all six API operations.
3. When generating a collection, select **Tags** for folder organization to group
   requests under **Health** and **Reply Sessions**.

The specification includes the API title and version, stable operation IDs,
request and response schemas, and known error responses. Its server URL defaults
to `http://127.0.0.1:8000`; set the generated collection's base URL to the deployed
address when calling another environment. Use the running service's specification
for the latest routes, and regenerate the checked-in snapshot after API changes
using the [contract maintenance instructions](docs/api-contract.md#maintaining-this-contract).

### Generate and review a reply

All replies use this session workflow. The direct `POST /reply` endpoint and
`answer_student_question` MCP tool have been removed; clients must create a
session and generate its reply. Every generated draft can be revised until it
enters `accepting`. This state locks the reviewed revision until QA publication
finishes and the session becomes `accepted`. Repeating `/accept` resumes pending
publication or returns an already accepted session.

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
