# Test Suites

The test directory is divided into two suites:

- `unit`: focused tests that replace the component under a direct dependency with a mock.
- `integration`: HTTP-level tests that execute the complete internal request flow from FastAPI through classification, routing, retrieval, answer generation, validation, and serialization.

Integration tests mock only external boundaries:

- OpenAI client calls
- Azure AI Search client calls
- Public website HTTP requests
- Cosmos DB container operations

No integration test sends network traffic or consumes paid API capacity.

MCP coverage includes typed tool discovery, the complete review workflow,
Streamable HTTP through an in-process ASGI transport, and a real STDIO subprocess.
Regression tests cover removal of the direct-answer HTTP route and MCP tool,
repeated revision of generated drafts, retryable generation failures,
conversation-ID route injection, invalid session input, execution deadlines and cancellation, sanitized errors, request correlation,
and payload-free logging. HTTP and MCP tests replace external dependencies;
the STDIO smoke test only discovers tools and requests a missing session.

Run unit tests:

```bash
uv run python -m unittest discover -s tests/unit -t . -v
```

Run integration tests:

```bash
uv run python -m unittest discover -s tests/integration -t . -v
```

Run all tests:

```bash
uv run python -m unittest discover -s tests -t . -v
```

Live OpenAI, Azure Search, and public website checks belong in the Postman E2E collection rather than the default automated test suite.
