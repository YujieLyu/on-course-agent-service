# MVP task breakdown

- Breakdown revision: 1, coordinator draft.
- Design: [revision 4](ARCHITECTURE.md), approved by the user.
- Baseline: `v1-backup`, `f232b945f64bd9ac21799fc27939e4d87b5d6081`.
- Breakdown approval: PENDING for the full ledger. The user explicitly approved
  the TASK-001 definition and its execution; required workflow handoffs remain
  satisfied for TASK-001; it is the sole selected implementation task.
- Required planner handoff: COMPLETE for TASK-001. After restoring the source
  branch configuration, the native planner confirmed TASK-001 boundaries and
  observable criteria. Full-ledger decomposition/approval remains pending.

## Execution and review

Each task delivers one behavior that can be reviewed separately. All tasks below
are PROPOSED except TASK-001, now REVIEW_REQUIRED for revision 2 after applying
user naming feedback and renewed independent review.
After the applicable approval/handoff gates, start only one explicitly selected task;
implementation -> independent review -> human review approval -> formal QA -> stop.
No separate ADRs apply. Future review and QA records use `reviews/TASK-NNN.md` and
`qa/TASK-NNN.md`; those records do not exist yet.

TASK-001 start authorization is recorded below. All implementation-review approvals
and completion evidence remain pending. Other tasks remain unapproved and unstarted.
Root records approval, baseline and evidence when they occur.
Changes to the definitions below require renewed breakdown approval.

Update `docs/api-contract.md`, `docs/openapi.json` and the MCP adapter within each
task that changes an exposed contract. Do not publish endpoints or fields that
require a later task to function. Frontend adaptation, login, material review,
source ingestion and unrelated cleanup are excluded throughout.

## Task overview

| ID | Outcome | Implementation approach | Dependencies | Completion signal |
| --- | --- | --- | --- | --- |
| TASK-001 | Persist each revision's own result and provenance | Extend the existing revision model and Cosmos serialization with outcome, evidence, citations and timestamps; project each revision's own references and read legacy records without inventing missing provenance. | None | Restart restores the actual outcome and citations for each new revision |
| TASK-002 | Recover generation/revision without losing feedback or duplicating revisions | Reserve a pending revision and save feedback with an operation identity before model calls; use conditional writes to complete it and resume the same operation on retry. | 001 | Fail/retry and competing-request fixtures preserve one operation's revision |
| TASK-003 | Enforce course isolation and prepare metadata migration | Add validated course filters to private retrieval, exclude records without ownership metadata and prepare a dry-run backfill script using a verified course mapping. | None | Cross-course and missing-metadata fixtures cannot leak course material |
| TASK-004 | Expose retrieval failures and support evidence reuse/refresh | Return source availability alongside search results; reuse saved evidence by default, persist a fresh snapshot on explicit refresh and resolve citations against that revision's evidence. | 001, 002, 003 | Unavailable differs from no match; explicit refresh creates a new snapshot |
| TASK-005 | Permanently retain QA and support disable | Add a course-scoped disable endpoint and active-only repository queries; preserve disabled status during publication retries and verify container/item retention settings. | None | Disabled QA is retained, excluded from search and never reactivated by retry |
| TASK-006 | Assess and clean an editor-submitted QA candidate | Introduce a small candidate assessment service that checks submitted-content eligibility and cleans both question and answer, returning a rejected, unchanged or changed candidate before embedding/publication. | 001 | Eligibility/privacy fixtures validate both question and answer before storage |
| TASK-007 | Accept exact editor text with cleaned-preview confirmation and recovery | Extend the acceptance coordinator to capture editor text and its base revision, keep cleaned previews and confirmation identities in the session, then freeze and publish the exact confirmed candidate with existing recovery. | 002, 005, 006 | Confirmed preview is the exact durable QA; failed publication resumes it |
| TASK-008 | Report required dependency readiness | Keep process liveness separate and add bounded readiness checks for required configured dependencies, reporting safe failures without credentials or routine paid generation calls. | None | Liveness stays healthy while required dependency failure reports not ready |
| TASK-009 | Backfill verified course metadata and qualify filtered deployment | Review the target schema and dry-run manifest, apply only the authorized ownership mapping and QA lifecycle changes, then verify live filtered queries and retention settings. | 003, 005 | Approved mapping is applied and live filtered queries prove ownership |
| TASK-010 | Evaluate the complete backend workflow with local real data | Run reproducible baseline/current cases on local real data, exercise the complete workflow with disposable QA entries and record retrieval quality, failures and HTTP/MCP/OpenAPI agreement. | 004, 007, 008, 009 | Reproducible evaluation report and matching backend contract examples |

The IDs express review slices, not permission to execute the whole queue.
TASK-001 is the recommended first task. TASK-009 additionally needs the canonical
course ID and verified index ownership mapping from the user/data owner.

## TASK-001 — Revision records and provenance

**Goal/reason:** stop losing generation outcomes and displaying cumulative citations
as if every revision used them. Extend the existing session aggregate, persistence
and response projection; generation/review still complete through their current flow.

**Scope/files:** `src/domain/reply_sessions.py`, `src/repositories/reply_sessions.py`,
`src/services/reply_sessions.py`, `src/models.py`, relevant domain/persistence tests.
Persist revision identity, actual outcome, evidence, citations and timestamps.
Exclude operation recovery and acceptance changes. API/MCP projection updates are
coupled to storage so a restored session and its public representation agree.

**Acceptance/evidence:** AC1: a two-revision fixture survives repository/service
restart with distinct outcomes and citation sets, including zero citations.
AC2: existing records remain readable without inventing missing outcomes or
per-revision evidence; missing legacy provenance is represented explicitly.
AC3: creation-based 30-day expiry, ETag conflict protection and existing acceptance
recovery still work. Formal QA repeats these cases through GET and the HTTP/MCP flows.

**Risk/intermediate/revert:** historical records do not contain enough information
to reconstruct provenance. Use an explicit compatible reader; no mass data rewrite.
Old acceptance behavior remains functional until TASK-007. Reverting new writers
must retain a reader for records already written; do not blindly downgrade storage.


### Approval and execution record

- Definition approval and start authorization: APPROVED by the user for TASK-001
  only, in this conversation; this does not approve the other nine definitions.
- Explicit statement: "批准TASK-001，执行".
- Recorded at: 2026-10-03 10:05:10 UTC (2026-10-03 20:05:10 AEST).
- Covered design: revision 4. Covered task: the TASK-001 overview row and detailed
  definition above, before this record was added; SHA-256: `80b5f38e5574f231a1815af68052aeef48dd55ddda79fb2e9b5ea017dc539a9f`.
- Status: REVIEW_REQUIRED; revision 2 applies responsibility-based DTO names
  and passed renewed independent review. Human implementation approval/QA pending.
- Runtime preflight: native `implementer` and `reviewer` launches each returned
  `agent type is currently not available`; no substitute agent was launched.
  At that preflight, the earlier native `planner` handoff remained unresolved.
- Git preflight: branch `v1-backup`, HEAD
  `f232b945f64bd9ac21799fc27939e4d87b5d6081`; source, tests, API entrypoints,
  dependencies and runtime configuration match HEAD. The only pre-existing changes
  are the user-staged `ARCHITECTURE.md`, `STATUS.md` and `TASKS.md` in this directory.
  Their index contents were preserved. Recheck the baseline after unblocking.
- Runtime resolution: 2026-10-03 10:21:45 UTC; restored configuration from
  `feature/add-reply-session` at `6da311609f79e41f9ef18c00f2958fb694716297`
  on the user's explicit instruction. The native planner loaded and found no
  TASK-001 scope/criterion blocker. Earlier launch errors remain historical evidence.
- Task-start baseline: [recorded pre-edit inventory](reviews/TASK-001/task-start.json).
- Next valid action: human review of [TASK-001 revision 2](reviews/TASK-001.md).
  Other tasks remain pending; formal QA awaits explicit implementation approval.
- Prior revision 1 independent review: no material findings or task-scoped
  blockers; advisory only. The naming update has renewed independent review recorded below.
  [Review packet](reviews/TASK-001.md) records current feedback/history.
- Human implementation approval: PENDING. Prior revision 1 content identity:
  `a1bb5087d15564d9f6fe6fe6e4be7b9834761516ef55f1180279bfe903662008`.
- Current revision 2: `2c96aca255997e1b1a090efa696341c3a01dc514029586b450bac32adefb765a`.
  Renewed native review found no material findings or task-scoped blockers.
- Developer checks: 54 focused tests PASS for revision 2; exact command and
  captured log in the review packet. Prior revision 1 checks remain historical.
- Formal QA / completion: NOT RUN / PENDING; task is not DONE.

## TASK-002 — Durable revision operations

**Goal/reason:** save teacher feedback before external work and make retries resume
one operation. Cover initial `/generate` and single-request `/review` together;
they share revision reservation, conditional completion and recovery semantics.

**Scope/files:** session domain/service/repository, `src/models.py`, `main.py`,
`mcp_server.py`, session/failure-contract tests. Define operation identity, pending
state, base-revision checks and recoverable failure fields. GET exposes progress.
Exclude retrieval refresh policy and QA acceptance.

**Acceptance/evidence:** AC1: a model failure after feedback persistence leaves
that feedback and pending operation visible after restart; retry completes the same
revision. AC2: duplicate requests and concurrent different requests cannot append
duplicate revisions or overwrite newer work; conflicting operation payloads fail.
AC3: `/review` returns the completed revision without a follow-up `/generate`;
HTTP/MCP clients can reproduce the same retry behavior. Formal QA injects failures
before/after conditional writes and simulates response loss.

**Risk/intermediate/revert:** this task changes mutation DTOs; document exact retry
and conflict responses in this task. Completed and pending states both work at its
end. Revert only after pending operations are reconciled or their reader is retained.

## TASK-003 — Course-scoped retrieval and migration preparation

**Goal/reason:** prevent private course evidence from crossing course boundaries,
including when today's index has no course metadata.

**Scope/files:** `src/tools/search_providers/azure_ai_search.py`,
`src/tools/search_providers/historical_qna_search.py`, `src/config.py`, provider tests,
a small migration script under `scripts/` and its current run instructions.
Validate/escape course filters and prepare a dry-run ownership/backfill manifest.
Exclude live index mutation, new ingestion and QA status changes.

**Acceptance/evidence:** AC1: two-course fixtures return only the requested course;
missing ownership never triggers an unfiltered fallback. AC2: the dry run rejects
ambiguous ownership and missing canonical ID, identifies affected records/schema
requirements and makes no remote changes. Formal QA verifies filter parameters,
missing-metadata behavior and repeatable dry-run output against isolated fixtures.

**Risk/intermediate/revert:** existing live course-material retrieval may return
no evidence until TASK-009; disclose this limitation. Do not deploy the filtered
course provider as qualified before backfill. General knowledge remains usable.
If live activation fails, stop private course retrieval rather than revert to an
unfiltered query. Preparation can proceed without guessing the real course ID.

## TASK-004 — Retrieval availability and revision evidence

**Goal/reason:** let teachers distinguish missing evidence from unavailable sources
and decide when a revision needs new information.

**Scope/files:** `src/tools/search.py`, provider adapters, `src/domain/evidence.py`,
reply agent/prompt and session service/DTOs, search/reply-flow tests. Persist source
availability beside each revision's evidence; reuse by default, refresh only when
explicitly requested. Resolve citations from that revision's server-owned evidence.
Exclude provider replacement, new search infrastructure and broad ranking changes.

**Acceptance/evidence:** AC1: source timeout/error differs from successful zero
matches and is disclosed in the response; remaining valid sources/general knowledge
remain usable while unsupported course facts stay unknown. AC2: default review makes
no new retrieval call; explicit refresh persists a new snapshot without changing
prior evidence/citations. AC3: unknown source IDs cannot become trusted citations.
Formal QA exercises partial/all-source failures and checks prompts and public results.

**Risk/intermediate/revert:** Canvas stays an empty stub, not a mandatory healthy
source. Changes are bounded to availability/evidence behavior; TASK-010 measures
remaining search-quality issues. Revert routing/projection together while retaining
readability of stored snapshots.

## TASK-005 — Permanent QA lifecycle and disable

**Goal/reason:** keep reusable QA permanently while allowing teachers to remove an
entry from retrieval without deleting it.

**Scope/files:** QA repository/domain ports, `main.py`, `src/models.py`, bootstrap,
QA provisioning script, QA/versioning/API tests and MCP adapter if it exposes disable.
Implement course-scoped `PATCH /qa-memory/{id}`, active-only search and permanent
retention. Prepare explicit handling of legacy QA without status; verify legacy
compatibility with fixtures and include any required backfill in TASK-009.
Exclude general CRUD, enable/restore and changes to reply eligibility.

**Acceptance/evidence:** AC1: disable twice preserves content/provenance and excludes
the entry from search in its course. AC2: publication retry, including a higher
revision/race, cannot reactivate a disabled record. AC3: permanent retention is
verified at both container-default and item TTL boundaries; session TTL is unchanged.
Formal QA checks wrong-course requests, CAS races, legacy records and retry behavior.

**Risk/intermediate/revert:** container configuration must be checked before claiming
permanent retention; fake-client tests alone are insufficient deployment evidence.
Repository filtering and endpoint ship together. Once disabling is used, keep the
filter even if the endpoint is withdrawn; rollback must not expose disabled entries.

## TASK-006 — QA candidate eligibility and privacy

**Goal/reason:** validate the teacher's submitted content, rather than trusting the
old model draft, before it can become reusable QA.

**Scope/files:** a small candidate assessment/privacy component within existing
`src/services/`, supporting domain types, reply policy/prompt where needed, focused
unit tests. Its service interface returns eligible/rejected and the cleaned question
and answer, with enough identity to detect edits. Exclude public acceptance states
and Cosmos publication; the current route is switched only in TASK-007.

**Acceptance/evidence:** AC1: useful general answers may qualify without citations;
clarification, not-found/error content and private individual decisions do not.
Manual edits are assessed independently of the old draft outcome. AC2: identifying
content in either question or answer is cleaned before any embedding/write; failures
produce no publishable candidate. AC3: tests distinguish unchanged content from a
changed preview and reject stale/untrusted provenance. Formal QA uses named policy
cases, verifies both texts and checks that ordinary logs contain no raw content.

**Risk/intermediate/revert:** privacy detection and eligibility can be uncertain;
expose uncertain/rejected results rather than guessing approval. The component is
independently callable/testable, with no half-working public endpoint. Existing
acceptance is not MVP-qualified until TASK-007. Revert by removing the unused component.

## TASK-007 — Editor acceptance and confirmed publication

**Goal/reason:** Save to QA must save the exact validated editor submission, including
manual edits, and require teacher confirmation when privacy cleaning changes it.

**Scope/files:** acceptance domain/coordinator, session repository/service, QA
publication service/bootstrap, `main.py`, DTOs, MCP and acceptance/recovery tests.
Integrate TASK-006 with existing CAS freeze, idempotent publication and recovery.
Keep temporary preview identity, editor provenance, confirm/edit/cancel behavior and
publication-recovery state in the session. This coupling is necessary to prevent
unconfirmed or replaced content from reaching permanent QA. Exclude new QA CRUD.

**Acceptance/evidence:** AC1: exact editor text/base revision/operation identity are
validated; stale submissions and conflicting retries fail, and manual changes retain
provenance without a generation call. AC2: changed question/answer returns a preview
and writes/embeds no QA until confirmation; confirm publishes precisely that preview
without re-cleaning; edits invalidate it and cancellation publishes nothing.
AC3: unchanged eligible text can publish directly; lost acknowledgements, publication
failure and concurrent retries recover one frozen decision, preserve disabled status
and mark accepted only after durable QA. Formal QA exercises the complete HTTP/MCP
flow and recovery script; GET distinguishes preview from publication recovery.

**Risk/intermediate/revert:** replace the old acceptance contract in one complete
slice, with no raw-content bypass. Legacy frozen acceptance lacks privacy proof;
recovery must not silently publish it without the new gate. Reverting cannot restore
the old bypass or discard pending confirmations; retain safe readers/recovery.

## TASK-008 — Dependency readiness

**Goal/reason:** shared deployment needs to distinguish a responding process from a
backend that can reach its required configured dependencies.

**Scope/files:** `main.py`, configuration/bootstrap and a small readiness helper,
health/failure-contract tests. Keep `/health` liveness and expose bounded readiness
for the approved model/embedding configuration, Cosmos and course-search dependency.
Exclude monitoring platforms, paid generation as a routine probe and Canvas readiness.

**Acceptance/evidence:** AC1: liveness responds while a required dependency is
unavailable; readiness returns a safe failure with the affected dependency and no
credentials. AC2: checks have bounded duration and follow configured approved
endpoints. Formal QA injects missing configuration, timeouts and provider failures;
live checks in TASK-010 qualify actual connectivity.

**Risk/intermediate/revert:** avoid claiming credential validity from presence alone
or making expensive model requests on every health poll. The endpoint must state
what its checks establish. Revert the added readiness surface without altering liveness.

## TASK-009 — Verified data migration and deployment qualification

**Goal/reason:** code filters cannot establish live isolation while course ownership
metadata is absent. Qualify the existing course index and legacy QA lifecycle.

**Prerequisites:** TASK-003 and TASK-005 DONE; canonical course ID and verified
record-to-course ownership mapping supplied; target schema/container settings and
dry-run affected-record manifest reviewed. Do not infer ownership from one course
being present or silently start remote mutation.

**Scope/files:** migration/provisioning scripts and current instructions, isolated
migration fixtures, a compact execution/verification report. Apply only the reviewed
mapping; handle filterable field/schema constraints and QA status/TTL prerequisites.
If the existing index cannot be updated within this scope, stop for breakdown revision.
Exclude ingestion, model/API behavior and destructive index replacement.

**Acceptance/evidence:** AC1: dry run and repeat application have stable, bounded
results; missing/ambiguous ownership remains excluded. AC2: after human review of the
script/manifest and authorization of the target operation, formal QA records live
course-filter queries, record counts and permanent QA retention settings, with no
cross-course/unmapped hits. Missing live evidence is blocked, not PASS.

**Risk/intermediate/revert:** preserve a scoped before-state manifest and avoid raw
private text in reports. The approved environment does not justify guessing metadata.
Disable private course retrieval on a failed migration; never remove its course filter.
Store only necessary recovery data locally, outside ordinary logs.

## TASK-010 — Backend evaluation and handoff

**Goal/reason:** establish whether the assembled MVP meets the actual teaching
workflow and retrieval needs before unified frontend adaptation.

**Scope/files:** existing `eval/` guidance and a small reproducible local evaluation
runner if needed; workflow integration fixtures and current API/examples. Use local
real data in the approved environment and disposable QA entries for explicit save
checks. Exclude new product behaviors and committing raw evaluation data.

**Acceptance/evidence:** AC1: a local report separates retrieval misses, source
failures, grounded course facts, general answers and revision outcomes; it records
baseline versus current behavior on the same cases without invented pass thresholds.
AC2: create/generate/review/restart/editor save/preview confirm/disable/recovery are
reproducible and HTTP, MCP and OpenAPI agree. AC3: credentials/raw PII stay out of
logs, tracked reports and automatic QA publication. Formal QA repeats approved cases
against live dependencies and records limitations rather than claiming fixture-only
success as live qualification. Quality failures requiring code return to a scoped task
and renewed review rather than expanding this evaluation task.

**Risk/intermediate/revert:** real data/model access may block live evaluation; record
that explicitly. Evaluation output is separate from permanent QA and does not trigger
acceptance. This task produces evidence and handoff; reverting it leaves behavior intact.

## Developer checks and formal QA evidence

These are planned checks, not results. Reuse the existing unittest conventions:

- TASK-001: `uv run python -m unittest tests.unit.test_reply_session_domain tests.unit.test_session_persistence tests.unit.test_reply_agent -v`
- TASK-002: `uv run python -m unittest tests.unit.test_session_persistence tests.unit.test_api_failure_contract tests.unit.test_mcp_server tests.integration.test_reply_flow -v`
- TASK-003/004: `uv run python -m unittest tests.unit.test_search tests.unit.test_reply_agent tests.integration.test_reply_flow -v`
- TASK-005: `uv run python -m unittest tests.unit.test_qna_versioning tests.unit.test_historical_qna tests.unit.test_reply_acceptance -v`
- TASK-006: focused new candidate-policy tests using the existing unittest pattern;
  record the actual module and command when implemented, not a fictional current check.
- TASK-007: `uv run python -m unittest tests.unit.test_reply_acceptance tests.unit.test_qna_versioning tests.integration.test_reply_flow tests.integration.test_mcp_flow -v`
- TASK-008: `uv run python -m unittest tests.unit.test_api_failure_contract tests.unit.test_observability -v`
- TASK-009: isolated migration fixtures and reviewed script dry run; exact commands
  depend on the verified schema/mapping and are recorded before implementation review.
- TASK-010: `uv run python -m unittest discover -s tests -t . -v`, followed by the
  documented local evaluation procedure; no evaluation runner exists today.

Add meaningful task-specific cases to these modules or a focused new module.
Formal QA happens only after that task's human implementation-review approval and
records criterion IDs, exact commands, expected/actual results and evidence in its
QA record. No check was run or passed as part of this documentation-only breakdown.

## Approval and history

- 2026-10-02: the user explicitly approved design revision 4; see its approval record.
- Breakdown revision 1: coordinator draft; planner handoff blocked, human approval
  pending. All task execution/review/QA/completion records remain pending.

- 2026-10-03 10:05:10 UTC: user approved and selected TASK-001; recorded scoped
  authorization. Native implementation/review preflight failed; TASK-001 is BLOCKED,
  with no product changes or tests run. Other task definitions remain pending.

- 2026-10-03 10:21:45 UTC: user-authorized Codex configuration restoration
  completed; native planner loaded and validated TASK-001. Resume only TASK-001
  under its existing scoped definition/start approval; record the pre-edit baseline.

- 2026-10-03: TASK-001 implementation and targeted developer checks completed;
  froze revision 1, verified reconstruction and obtained independent native review
  with no material findings. TASK-001 is REVIEW_REQUIRED, awaiting explicit human
  implementation approval. No formal QA or later task was started.

- 2026-10-03 12:59:34 UTC: user naming feedback reopens the same TASK-001
  implementation. Preserve revision 1 and refresh developer checks/independent
  review before returning to human review; no other task or formal QA starts.

- 2026-10-03: responsibility-based DTO names applied; frozen revision 2 passed
  54 developer tests and renewed independent review with no material findings.
  Preserve r1; return TASK-001 to REVIEW_REQUIRED for human review of r2.
