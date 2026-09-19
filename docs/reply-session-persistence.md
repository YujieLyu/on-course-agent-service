# Reply-session persistence

Every reply session is stored in Cosmos DB, including its question, revisions,
teacher feedback, original intent classification, and original retrieved evidence.
HTTP and MCP workers use the same container. No in-memory fallback is used when
the database is unavailable; the API returns `503`.

## Setup

Use the existing Cosmos account and database, with a separate container:

```dotenv
AZURE_COSMOS_ENDPOINT=https://<account>.documents.azure.com:443/
AZURE_COSMOS_KEY=<key>
AZURE_COSMOS_DATABASE_NAME=<existing-database>
AZURE_COSMOS_QA_MEMORY_CONTAINER=qa_memory
AZURE_COSMOS_REPLY_SESSIONS_CONTAINER=reply_sessions
```

Run once before deploying the application:

```bash
uv run python -m scripts.provision_cosmos_sessions
```

The script creates the container if absent, then validates its partition key
and TTL. It does not recreate the database or change `qa_memory`. The key needs
permission to create a container. An incompatible existing container causes a
configuration error rather than silently bypassing the retention policy.

## Document layout and retention

Each session is one document with `id` equal to `conversation_id`. The partition
key is `/id`, allowing point reads using the ID already present in the HTTP URL.
Draft revisions and evidence are written together in one document; evidence is
not exposed in the public session response. This container has no vector index.

`created_at` and `expires_at` are UTC timestamps. Expiry is fixed at creation plus
30 days. Each write sets the item TTL to the remaining lifetime because Cosmos
TTL counts from its last modification timestamp, `_ts`. Reads do not write or
extend retention. The API returns `404` at absolute expiry, even while background
physical deletion is pending. See [Cosmos TTL documentation](https://learn.microsoft.com/en-us/azure/cosmos-db/time-to-live).

The 30-day rule applies to the `reply_sessions` container. Final accepted Q&A in
`qa_memory` and data held by the configured LLM provider have separate retention.
Sessions previously held only in a running process are not automatically migrated.

## Writes and recovery

Successful creation, generation, feedback, and acceptance responses follow a
successful session write. Conditional writes use Cosmos ETags; conflicting
changes return `409`, and stale generation cannot replace a newer revision.
Get the latest session before retrying a failed or uncertain mutation.

Acceptance is a recoverable workflow with a durable pending record in the same
session document:

1. Read one `SessionSnapshot` containing the session, evidence, and version.
2. Conditionally change `awaiting_review` to `accepting`, storing an immutable
   `acceptance` payload: conversation/course IDs, original question, revision
   number, accepted text, and acceptance timestamp. A conflicting review wins or
   loses this conditional write; an unsuccessful claim never publishes QA data.
3. Embed the pinned question and publish the pinned revision to `qa_memory`.
   Existing newer revisions or different content at the same revision cause
   `409`. Same-revision retries are no-ops; replacing an older revision requires
   its current ETag. Write races have a bounded reconciliation budget.
4. Conditionally mark the session `accepted` only after QA publication succeeds.
   Concurrent publishers of the same decision converge on the accepted state.

`accepting` locks the revision: review and generation return `409`. It means the
teacher's acceptance decision is durable, while QA synchronization is pending or
not yet acknowledged. QA Memory may already contain that approved revision if
finalization failed. `accepted` means publication was confirmed. These are
separate container writes, so this is recoverable eventual consistency, not a
cross-container transaction.

After a timeout or `503`, fetch the session. Repeat `POST /accept` for
`accepting`; an already `accepted` session returns `200` without another publish.
A process restart or a lost write acknowledgement does not discard the pending
decision. There is no automatic rollback to an editable draft.

For unattended recovery, schedule the following command in a worker or job:

```bash
uv run python -m scripts.recover_reply_acceptances --limit 100
```

The command processes a bounded batch, rechecks each candidate, retries only
existing acceptance decisions, continues after individual failures, and prints
completed/expired counts plus failed IDs. It exits nonzero if a publication
failed. The application does not install a scheduler. Run the job regularly and
monitor failures; increasing `--limit` allows a larger backlog to be inspected.
Recovery does not extend the fixed 30-day lifetime. Pending decisions must be
recovered before expiry; expired session content is not retained for the worker.

This refactor uses the existing containers and requires no new table or index.
Deploy all writers together and drain old instances that still use unconditional
QA upserts. Existing session documents without `acceptance` remain readable;
previously accepted sessions remain accepted. Existing inconsistent legacy QA
records are not silently overwritten or repaired by the recovery job.

## Responsibility boundaries

- `src/domain/reply_sessions.py`: storage-independent snapshots, immutable
  acceptance payloads, repository protocols, and domain errors.
- `src/reply_sessions.py`: creation, expiry policy, and state transitions using
  injected repository and clock dependencies. No SDK, serialization, or LLM calls.
- `src/agents/orchestrator.py`: generation from supplied context and evidence;
  revision generation neither reads nor writes the database.
- `src/services/reply_acceptance.py`: claim, publish, finalize, and recovery.
- `src/services/historical_qna.py`: embedding preparation through an injected
  embedding function and QA repository.
- `src/repositories/`: Cosmos document mapping, item TTL, ETag handling, version
  checks, and SDK exception translation. `_etag` never enters the public model.
- `src/bootstrap.py`: production adapter and service composition.

Initial generation and revision each perform one session point read and one
conditional write. The write uses the original snapshot version without a
second read. A newer revision therefore invalidates stale work atomically.

## Browser recovery

The `on-course/frontend` shared Answer surface implements this flow in API mode:

```dotenv
NEXT_PUBLIC_ONCOURSE_DATA_MODE=api
NEXT_PUBLIC_ONCOURSE_API_BASE=http://127.0.0.1:8000
```

Configure the deployed service origin instead when hosting the frontend. Restart
Next.js after changing its environment. Browser requests use a same-origin
`/api/reply-sessions` rewrite to the configured backend.

The browser saves only the conversation ID in localStorage after session
creation, before generation. On reload it calls
`GET /reply-sessions/{conversation_id}` and restores the question, latest draft,
submitted feedback, saved revision history, and status. `404` clears the ID;
network errors and `503` retain it. Interrupted generation and revision can be
resumed explicitly. Storage keys separate API origins, courses, surfaces, and
host reply targets. Failed localStorage writes do not retry successful creation.

The current frontend has no authenticated account identity; IDs are scoped to a
browser profile. Add authenticated user scope and ownership checks when account
authentication is introduced. Live Outlook recovery requires a message item ID;
Canvas uses its existing reply target key. Hosts without a stable key or access
to browser storage cannot automatically recover a session.

Only submitted content is durable. Manual draft edits and unsubmitted notes are
not saved; use the revision action to persist a changed draft. The current
session response has no structured citation metadata, so the frontend requires
teacher review and does not fabricate evidence. The session service does not yet
accept frontend preferences or surrounding host thread context.
