# MVP test coverage

The suite covers initial generation, repeated feedback-driven review, accepted
QA publication, and HTTP/MCP access. External calls are mocked; tests require no
credentials, network access, or paid API capacity.

- `integration`: HTTP runs real retrieval adapters and answer generation with
  fake OpenAI, search, website, and Cosmos clients. It verifies a workflow across
  a service restart, stable source IDs, cumulative citations, and acceptance of
  the latest revision. MCP exercises the same workflow, typed Streamable HTTP,
  and a real STDIO subprocess.
- `unit`: source mapping, parallel four-source retrieval and partial failure,
  structured stateless model calls, session transitions, atomic persistence,
  concurrency conflicts, expiry, and public response projection.
- Acceptance and QA tests retain publication retry, cancellation, lost write
  acknowledgements, immutable acceptance decisions, course-scoped retrieval,
  version protection, and recovery. These protect the MVP's QA write path.
- API and MCP tests retain input validation, safe errors, execution deadlines,
  route protection, request correlation, and logs without question or reply text.

Removed tests covered the deleted provider-conversation fallback, independent
classification, the old review-then-generate protocol, legacy document migration,
and evaluation-data scrubbing outside the service MVP. Repeated workflow and
persistence assertions have been consolidated. There is no target test count;
coverage follows the supported behavior.

Run all tests:

```bash
uv run python -m unittest discover -s tests -t . -v
```

Run one suite:

```bash
uv run python -m unittest discover -s tests/unit -t . -v
uv run python -m unittest discover -s tests/integration -t . -v
```

Live provider checks and answer-quality evaluation are separate manual checks.
Use disposable course data when testing acceptance against a live QA container.
