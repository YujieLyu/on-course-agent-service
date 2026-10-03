# MVP design: reply sessions and reusable QA

- Revision: 4; APPROVED.
- Basis: the user's confirmed answers to Q01–Q16, including manual editor text,
  QA disable and cleaned-QA confirmation.
- Baseline: `v1-backup`, commit `f232b945f64bd9ac21799fc27939e4d87b5d6081`.
- Delivery: refactor the backend first; unify frontend adaptation afterward.

## 1. Scope and existing foundation

The [API](../../../main.py) already supports session creation/restoration, initial
answers, direct feedback-driven revision and acceptance into Cosmos QA. Retain the
existing domain/service/repository boundaries, conditional writes, acceptance lock,
deduplication and publication recovery. No new service deployment is required.

Frontend/backend remain separate. The frontend is demonstrated locally; deployment
is shared by multiple users. Backend evaluation uses local real data. The user
confirmed the existing Azure environment is approved: continue with the configured
model/embedding endpoint, Azure AI Search and Cosmos DB.

The backend session contract is the source of truth. Update its OpenAPI/examples
as contracts evolve, then adapt the frontend. Do not restore `/reply` or introduce
parallel `/answer/*` routes. Teaching-material review is deferred. Existing MCP
adapters follow the backend contract; they are not a separate business engine.

## 2. Reply workflow and API

Create session -> search and generate -> submit feedback and receive another draft
(repeat) -> Save to QA submits the editor text -> privacy handling -> confirm any
cleaned QA preview -> durable Cosmos publication.

| Endpoint | Responsibility |
| --- | --- |
| `POST /reply-sessions` | Save `question`, `course_id`, optional `user_id`; return a new `pending_generation` session before any model call. |
| `GET /reply-sessions/{conversation_id}` | Restore drafts, revision progress and acceptance state without generating. |
| `POST /reply-sessions/{conversation_id}/generate` | Search and generate the initial draft; return `awaiting_review`. |
| `POST /reply-sessions/{conversation_id}/review` | Save feedback first, then generate and save the revision within this single request. No follow-up `/generate` call. |
| `POST /reply-sessions/{conversation_id}/accept` | Save to QA submits editor text and the reviewed revision identity. Return a cleaned-QA preview when confirmation is required; publish only the confirmed candidate. Return `accepted` after durable publication. |
| `PATCH /qa-memory/{id}` | Set QA status to `disabled`; retain its record permanently and exclude it from retrieval. This iteration adds disable only, not general QA editing/CRUD. |

Keep snake_case DTOs: public session identity/course/user/question, status, revisions,
current-revision citations and creation/expiry times. Extend each revision with
outcome, operation identity, evidence, citations and timestamps. GET must expose
enough pending-work information to distinguish completed work from a saved revision
that needs recovery. Final state/request/retry fields are explicit contract work;
correlation IDs alone do not deduplicate mutations.

Each revision operation has a stable retry identity and conditional writes. Retrying
it resumes/returns the same revision instead of appending another. Model failure
leaves feedback saved and recoverable. A competing request cannot overwrite a newer
revision. Acceptance must use the exact editor text submitted by the teacher,
not silently substitute the earlier server-generated draft. Save manual changes as
a teacher-edited revision/candidate with provenance; no feedback-driven generation
is required merely to save these edits. Reject stale base-revision submissions.

## 3. Retrieval and answer boundaries

General knowledge remains available for concepts, programming help and study advice.
Course dates, submission requirements, grading rules and other course facts require
retrieved support or explicit teacher confirmation. Missing course information must
be stated as unknown.

Retain the [retrieval providers](../../../src/tools/search.py), with these changes:

- Restore `course_id` metadata and filter private course-material and QA retrieval.
  One course exists today, but its index metadata is missing. Backfill its verified
  ownership before filtered activation; missing metadata must not permit unfiltered
  cross-course search. Supply the canonical course ID and backfill mapping before migration.
- Reuse saved evidence for revision by default; explicitly requested new information
  may trigger refreshed retrieval. Save the resulting snapshot for that revision.
- Distinguish unavailable sources from no matches. Disclose failures and continue
  using remaining valid evidence/general knowledge, while keeping missing course
  facts unknown. Improve retrieval quality with targeted evaluation.
- Save each revision's actual citations and expose that version's references,
  replacing the cumulative citation list. Resolve source IDs using server-owned
  evidence; do not fabricate provenance or treat unknown IDs as valid citations.

Canvas remains an empty provider. This refactor consumes the existing course index;
new OneDrive ingestion/source-sync infrastructure is not introduced.

## 4. Data, identity and retention

| Record | Minimum behavior |
| --- | --- |
| `ReplySessionRecord` | Course, question, user email when supplied, embedded revisions, storage version and frozen acceptance. Expire 30 days after creation; revisions/acceptance do not extend expiry. |
| Embedded `ReplyRevisionRecord` | Stable revision/operation identity, saved feedback, pending/completed work, text, outcome, evidence snapshot, actual citations and timestamps. |
| `QAMemoryEntry` | Course, reusable question/answer, session/revision provenance, confirmation identity/time, question vector and `active`/`disabled` status. Permanently retained with no automatic expiry. Retrieve active entries only. |

Staff Microsoft login will be supplied by the frontend later; authentication and
membership integration are deferred. `user_id` can be the user's email. Until login
is verified by the backend, that value is identity metadata rather than proof of
identity/access. Course scoping is required in this iteration.

Evaluation input/output is separate from reusable QA and is not automatically
published. The Save to QA action remains explicit. Use the approved environment;
ordinary logs exclude raw questions, answers and identifying data.

## 5. QA acceptance and reusable components

Useful general-knowledge answers may enter QA after teacher confirmation without
retrieved citations. Reject clarification, `not_found`, `error` and private individual
decisions as reusable QA. An `answered` label alone is not sufficient eligibility.

Save to QA triggers `/accept` with the current editor text, the base revision and
a stable operation identity. Evaluate the submitted text itself; the old draft's
outcome/citations do not automatically certify a manually changed answer.
Apply privacy handling to both question and answer before QA embedding/publication.
The offline evaluation-scrubbing script is not an online privacy gate.

If cleaning changes the reusable content, return the cleaned question and answer
for teacher confirmation; do not insert them into `qa_memory` yet. The candidate
and its confirmation identity belong to the session's temporary acceptance state.
Confirm through the same `/accept` operation using that identity, then publish the
exact preview the teacher approved. Do not re-clean or replace it after confirmation.
Editing the candidate requires a new preview/confirmation; cancelling publishes
nothing. If no cleaning changes require a preview, the Save to QA action can
publish the validated submitted text directly. Final DTO/state fields must expose
the distinction between waiting for confirmation and awaiting publication recovery.

Reuse the [acceptance coordinator](../../../src/services/reply_acceptance.py): freeze
the exact confirmed eligible content, prevent competing changes, publish idempotently
and then complete acceptance. Failed publication resumes the same decision; accepted
sessions are terminal. A preview is not an accepted or published QA. Finish privacy
handling before permanent reusable storage.

Disabling QA changes retrieval eligibility, not retention. Keep content and provenance;
repeated disable requests are safe. Apply the active-status filter at the QA repository
boundary, and ensure acceptance retries cannot reactivate an already disabled record.

Keep [ReplySessionService](../../../src/services/reply_sessions.py), shared retrieval,
draft/revision helpers, QA publication and session/QA repositories. Extend these
boundaries instead of rebuilding persistence or introducing an evidence platform.

## 6. Verification and review

Q05 is confirmed: editor text is the answer submitted for QA, including direct edits.
Q09 is confirmed: disable is in this iteration and retains the record permanently.
Q08 is confirmed: show PII-cleaned QA to the teacher for confirmation before saving.
No product-choice question from that list remains pending. Canonical course/index
mapping and the concrete DTO/state contract still need implementation preparation.

Use Python >=3.12, uv and existing unittest patterns. Verify persisted-feedback
recovery, duplicate retries, concurrency, course filters, source failures,
per-revision citations, exact editor-text capture, QA eligibility/privacy previews,
confirmation identity, cancellation, disabled-QA exclusion and idempotent publication.
Health should distinguish liveness from required dependency readiness. Formal QA
follows human implementation review.

Design revision 4 is approved. Task-breakdown approval and implementation reviews
remain separate gates. No implementation has started.

## Design approval

- Status: APPROVED; architecture revision 4, with no separate ADRs.
- Actor: the user, in this conversation.
- Recorded at: 2026-10-02 23:50:13 UTC (2026-10-03 09:50:13 AEDT).
- Explicit statement: "design revision 4通过，进行下一步".
- Approved content SHA-256, before approval-only metadata edits: `1cac5ea1fe0f62bf05b2c16b2ac2627e6eaab8496c6807413c6fce343f5592af`.
- The index still preserves the exact approved pre-record content; approval metadata
  does not change the design requirements. Breakdown approval remains pending.
