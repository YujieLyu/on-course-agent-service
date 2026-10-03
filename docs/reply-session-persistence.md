# Reply-session persistence

Every reply session is stored in Cosmos DB, including its question, revisions, session citations,
teacher feedback and original retrieved evidence.
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
Each embedded revision saves its number, feedback, draft text, actual generation
`outcome`, full `evidence` snapshot, actual `citations` and `completed_at` in the
same conditional document write. Revision identity is the pair of session ID
and revision number. Completion time is the time the finished revision is
recorded, not the start of an external model call. This container has no vector index.

The public revision excludes the full evidence snapshot. Its citations identify
only the sources that version used. Top-level `citations` mirrors the latest
revision and is no longer cumulative. Repeated source type, URL and content
combinations are deduplicated within each version. An uncited new answer has
`citations: []`, regardless of earlier versions' citations.

Generation assigns source IDs such as `source_1` using the order of the original
retrieval results. The model returns selected IDs, which the answer agent resolves
to original `SearchResult` objects before the draft and citations are saved.
Revisions rebuild the same mapping from the latest revision's persisted evidence
order, including after a restart; citations do not determine source IDs.
IDs require no additional document fields. Unknown IDs are ignored and counted
in logs without rejecting the draft. Existing citations are not revalidated.

Sessions use locally generated UUIDs; no provider conversation is created or
continued. Model calls receive explicit context with `store=False`. The original
retrieval is retained at session level for legacy compatibility; each new revision
also records the evidence it used. Each review reads the latest version, generates from
saved sources plus feedback history, and appends a completed revision in one
conditional write. There is no persisted pending-review state. Failed generation
leaves both the feedback history and previous answer unchanged.

Existing completed revisions remain readable through nullable metadata defaults.
Missing legacy outcomes, evidence, citations and completion times stay `null`;
they are not reconstructed from accumulated session citations or session creation
time. An empty evidence snapshot (`[]`) is known empty and never triggers fallback.
When a legacy revision has unknown evidence, a new review uses the original
session retrieval and records that snapshot on the new revision only. Historical
text and feedback are preserved. GET makes no data writes; no mass migration is
required. Legacy top-level accumulated citations may remain in storage but are
not exposed as the current revision's citations.

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

- `src/domain/reply_sessions.py`: the immutable `ReplySession` aggregate owns
  creation, fixed expiry, allowed transitions, revision numbering, original
  evidence, and acceptance decisions. Operations take an explicit time and return
  a new aggregate without I/O. Revisions and acceptance payloads are immutable;
  revision and evidence collections are tuples. `SessionSnapshot` adds only the
  opaque repository version to an aggregate. Repository protocols and domain
  errors also live here.
- `src/domain/evidence.py`: immutable source values shared with
  generation. These values have no dependency on HTTP response schemas.
- `src/services/reply_sessions.py`: `ReplySessionService` coordinates retrieval, answer
  generation, direct review, and persistence. Its `_commit()` method is the single application entry point
  for both session creation and conditional replacement, including the write-time
  expiry check and successful-transition logging. It never reloads a snapshot
  during commit or retries a conflicting business operation.
- `src/models.py`: public `ReplySessionResponse` and `RevisionResponse` DTOs
  project only public fields and use those names in OpenAPI. The domain aggregate
  and revision retain `ReplySession` and `Revision`. HTTP handlers explicitly
  convert aggregates to response DTOs; the full retrieval snapshot, acceptance payloads, and
  storage versions are excluded. The DTO requires persisted timestamps instead
  of inventing them during response validation.
- `src/agents/reply_agent.py`: one structured model call with explicit question,
  sources, previous reply, and feedback history, followed by source-ID resolution.
  No database access or provider conversation management.
- `src/services/reply_acceptance.py`: claim, publish, finalize, and recovery.
- `src/services/historical_qna.py`: embedding preparation through an injected
  embedding function and QA repository.
- `src/repositories/`: Cosmos document mapping, item TTL, ETag handling, version
  checks, and SDK exception translation. `_etag` never enters the public model.
- `src/bootstrap.py`: production adapter and service composition.

The same domain lifetime check runs at read and write boundaries. Both checks
are necessary because a long-running generation or publication can cross expiry.
Cosmos TTL uses that same remaining-lifetime calculation. Application services
select the next workflow step, while domain operations enforce the transition
rules. Recovery continues to publish only already-persisted acceptance decisions.

Initial generation and revision each perform one session point read and one
conditional write. The write uses the original snapshot version without a
second read. A newer revision therefore invalidates stale work atomically.
The completed reply, feedback, citations, and original sources stay in the same
Cosmos document. Acceptance still uses its persisted, immutable decision.

## Browser recovery

Clients should save the conversation ID after creation and retrieve the session
on reload. Clear the ID on `404`; retain it on network errors or `503`. Scope
browser storage keys to the API origin, course, and user when available.

Keep unsent feedback in the UI while `/review` runs. Successful review responses
already contain the new answer. Failed generation does not save that feedback;
allow the user to retry it. After a timeout, GET the session to reconcile an
uncertain write before resubmitting. Disable competing mutations while a request
is running. Cosmos ETags also reject overlapping writes from different clients.

Only completed revisions are durable. Manual answer edits and unsubmitted notes
are not saved. Clients can display each revision's own citations and outcome;
top-level citations describe the latest version. Show unknown provenance for
legacy `null` values rather than claiming there were no sources. Explicitly
accept the latest answer for QA publication.
