# On Course Agent Service Evaluation Plan

## 1. Objectives

Build a reproducible, comparable, and auditable evaluation system that answers:

1. Does the service understand student intent and select the correct retrieval sources?
2. Does retrieval return the authoritative evidence needed to answer the question?
3. Is the final answer correct, complete, grounded, and accurately cited?
4. Does the service clarify or escalate when information is missing, private, or requires an official decision?
5. Are multi-turn generation, teacher review, revision, and acceptance reliable?
6. Does the service prevent PII, cross-course information, and unsupported-fact leakage?
7. Is a new model, prompt, index, or code version an improvement over the current release?

The primary purpose is release gating and regression analysis, not producing a single overall score.

## 2. Strategies Adopted from the Reference Framework

Retain these design principles from the reference evaluation implementation:

- All system variants implement one `Baseline` or `SystemUnderTest` protocol.
- Input context and gold labels remain strictly separated; the system never sees reference answers at runtime.
- Support both `gold_history` and `predicted_history`; formal results use `predicted_history`.
- Conversations may run concurrently, but turns within one conversation remain sequential.
- Append each completed result immediately to `predictions.jsonl` and update `metrics.json` atomically.
- Record refusals, empty responses, invalid structures, timeouts, and dependency failures as countable `prediction_error` values.
- Report aggregate metrics and breakdowns by dataset features.
- Support smoke runs, full batch runs, retries, rate limits, and progress logs.

This service is an open-text RAG system. It should not copy the reference project's numeric or exact-match accuracy. Primary scoring must use structured routing labels, atomic facts, citation evidence, and a human-calibrated judge rubric.

## 3. Current Data Status and Prerequisites

Processing `SMNA-sent-matched.csv` currently produces:

- 445 scrubbed lecturer or staff messages;
- 269 thread-level evaluation candidates;
- 269 human-review tasks;
- 1 rejected record;
- 0 cases with `eval_ready=true`.

The source export contains sent staff messages but no attributable inbound student message body. Its `thread` value is an email subject, not the student question. Before evaluating answer correctness:

- [ ] Add student messages matched by thread, or have authorised reviewers enter and verify the original questions.
- [ ] Complete human review for names and indirect identifiers, then set `pii_reviewed=true`.
- [ ] Confirm each reference answer is complete, current, and lecturer-approved.
- [ ] Label intent, expected sources, status, atomic required facts, and forbidden claims.
- [ ] Set `eval_ready=true` only after all checks pass.

Until then, the evaluation framework may use curated smoke cases, but the 269 candidates must not be reported as a real gold accuracy set.

## 4. Canonical Evaluation Data Format

Use JSONL with one conversation per line. A single-turn case is a conversation containing one turn.

```json
{
  "id": "smna_eval_0001",
  "dataset_version": "smna-v1",
  "split": "test",
  "course_id": "SMNA",
  "turns": [
    {
      "question": "Scrubbed student question",
      "gold": {
        "intent": "assessment_requirements",
        "search_sources": ["course_materials"],
        "status": "answered",
        "reference_answer": "Scrubbed and reviewed lecturer answer",
        "required_facts": [
          "A PDF report must be submitted",
          "The submission must include a repository link"
        ],
        "forbidden_claims": [
          "Submission by private email is allowed"
        ],
        "acceptable_citations": [
          {
            "source": "course_materials",
            "document_id": "assignment-1-spec"
          }
        ],
        "needs_clarification": false
      }
    }
  ],
  "metadata": {
    "thread_id": "smna_thread_0001",
    "topic": "assignment_1",
    "answerability": "answerable",
    "sensitivity": "normal",
    "time_bucket": "2026-S1",
    "pii_reviewed": true,
    "eval_ready": true
  }
}
```

Rules:

- Each `required_fact` must be atomic and independently scoreable.
- `forbidden_claims` capture dangerous hallucinations, incorrect policies, and unauthorised commitments.
- Citations should use stable `document_id` values instead of relying only on mutable URLs or titles.
- Cases involving personal grades, submission status, or health information should expect escalation or refusal rather than storing private facts as gold.
- Deduplicate and split by `thread_id`; never place one thread in multiple splits.

## 5. Dataset Composition and Splits

### 5.1 Sources

The final dataset should include:

1. **Reviewed real cases:** scrubbed student questions and lecturer-approved answers; the primary gold set.
2. **Course coverage cases:** cases added by course owners to cover missing topics.
3. **Boundary cases:** clarification, not found, escalation, personal status, and administrative requests.
4. **Adversarial cases:** prompt injection, malicious retrieved content, cross-course access, PII extraction, and outdated policy.

Synthetic cases may support development and stress testing, but must use `data_origin=synthetic` and be reported separately from real cases.

### 5.2 Splits

- `dev`: prompt, routing, and retrieval tuning.
- `test`: frozen release acceptance set.
- `challenge`: privacy, safety, adversarial, and fault-injection cases.

Requirements:

- Split by thread, not message.
- Remove exact and near duplicates across splits.
- Prefer time-based splitting so test data better represents future traffic.
- Check whether evaluation answers already exist in historical Q&A or the retrieval index; mark these as `retrieval_contaminated=true` and report separately.
- Freeze test questions, gold labels, and required facts for each release cycle.

## 6. Systems and Baselines

Every variant accepts the same `EvalContext` and returns one `Prediction` containing answer, status, citations, intent, selected sources, errors, latency, and token/cost metadata.

Compare at least:

| Variant | Purpose |
|---|---|
| `no_retrieval` | Measure model prior knowledge and hallucination risk |
| `course_materials_only` | Measure core Azure Search RAG capability |
| `full_without_historical_qna` | Measure routing without historical-answer influence |
| `full_system` | Evaluate the current complete orchestrator |
| `previous_release` | Detect regressions relative to the last release |

Fix the dataset, model deployment, generation settings, index snapshot, and judge version for every comparison.

## 7. Layered Evaluation

Do not infer every failure from the final reply-session draft. Evaluate components and end-to-end behaviour separately.

### 7.1 Data and Privacy Gates

- Schema validation rate
- `eval_ready` and `pii_reviewed` rates
- Exact and near-duplicate rates
- Direct PII residual count
- Indirect PII found during human review
- Train/dev/test thread leakage count
- Retrieval contamination count

Any unreviewed case or case containing direct PII is excluded from the formal test set.

### 7.2 Intent Classification

- Intent accuracy and macro F1
- Per-intent precision, recall, and F1
- Search-source set exact match
- Search-source micro/macro precision, recall, and F1
- `needs_clarification` precision, recall, and F1
- Clarification-question specificity and answerability
- Query-rewrite factual preservation and retrieval lift

Report academic integrity, extension/special consideration, personal status, out-of-scope, and administrative requests separately.

### 7.3 Retrieval

Using reviewed `acceptable_citations` or relevant document IDs:

- Recall@1, Recall@3, and Recall@5
- Precision@k
- MRR and nDCG@k
- Source-routing recall
- Wrong-course document retrieval rate
- Empty-retrieval and dependency-error rates
- Retrieval latency p50 and p95

Report `course_materials`, `university_policies`, `historical_qna`, and `canvas` separately. Canvas is currently a stub and must appear as a known gap rather than being hidden inside an average.

### 7.4 Answer Generation

Primary metrics:

- Required-fact precision, recall, and F1
- Forbidden-claim violation rate
- Groundedness and faithfulness
- Citation precision, recall, and coverage
- Status accuracy: answered, needs clarification, not found, escalated, or error
- Answerability decision accuracy
- Clarification quality
- Escalation correctness
- Concision, actionability, and tone

Reference-answer similarity is diagnostic only. Different wording with the same facts should pass; similar wording with incorrect facts or citations should fail.

### 7.5 Multi-turn and Teacher Review

Run both history modes:

- `gold_history`: isolates current-turn capability.
- `predicted_history`: measures real error propagation and is the formal result.

Metrics:

- Follow-up resolution accuracy
- Pronoun and reference resolution accuracy
- Conversation all-correct rate
- Revision-instruction adherence
- Required-fact retention after revision
- New-hallucination rate after revision
- Valid state-transition rate
- Create/generate/get/review/revise/accept API success rate
- Cosmos upsert success and idempotency after acceptance
- Course partition isolation
- Behaviour after restart under the current process-local session limitation

### 7.6 Safety and Privacy

The challenge set must cover:

- Requests for names, emails, student IDs, or private records
- Prompt injection that asks the model to ignore system rules
- Malicious instructions in retrieved documents
- Cross-course queries
- Requests to change grades, approve extensions, or make official decisions
- Unavailable Canvas or personal-status data
- Conflicts between outdated historical Q&A and current authoritative policy

Metrics: PII leakage rate, cross-course leakage rate, unsafe compliance rate, and correct refusal/escalation rate.

### 7.7 Reliability, Latency, and Cost

- HTTP and schema success rate
- Prediction-error rate by error code
- p50, p95, and p99 latency
- Intent, embedding, retrieval, and answer-stage latency
- Input/output tokens, embedding calls, and estimated cost per case
- Retry and rate-limit counts
- Repeat-run consistency
- Throughput and error rate under concurrency

The public website provider currently embeds chunks sequentially, so embedding-call count and p95 latency require special attention.

## 8. Scoring

Use deterministic scoring first, LLM judging second, and human calibration throughout.

### 8.1 Deterministic Scoring

Do not use an LLM judge for:

- Intent, source set, status, HTTP/schema, and state transitions
- Document ID and citation matching
- PII patterns, wrong-course documents, and forbidden literals
- Latency, token usage, cost, and error codes

### 8.2 Atomic Facts

Score every `required_fact` and `forbidden_claim` independently. Prefer rules or structured matching for stable facts; use a judge only when deterministic scoring is unreliable. Retain every fact-level decision and rationale for audit.

### 8.3 LLM Judge

The judge receives scrubbed data only and follows a versioned structured rubric:

- Factual correctness
- Completeness
- Groundedness
- Citation support
- Clarification or escalation appropriateness
- Style and actionability

The judge sees the question, permitted evidence, required/forbidden facts, and candidate answer, but not the system variant name. Calibrate it against dual-human labels and report judge-human agreement; do not report judge scores alone.

### 8.4 Human Review

- Stratify samples across all intents and failure slices.
- Review every high-risk case.
- Use two independent reviewers for a calibration subset and adjudicate disagreements.
- Report Cohen's kappa or another suitable agreement measure.

## 9. Aggregation and Slices

Report aggregate metrics by:

- Intent
- Search source
- Topic or assessment
- Answerability and expected status
- Single-turn, multi-turn, and turn index
- Gold history and predicted history
- Real, curated, synthetic, and challenge origin
- Normal and sensitive cases
- Clean and contaminated retrieval
- System variant, model, and prompt version

Also report:

- Macro metrics so frequent intents do not hide rare failures
- Micro metrics reflecting overall traffic
- Conversation all-correct rate
- Worst-performing slices with sample sizes
- Bootstrap confidence intervals
- Changes from the previous release and a regression-case list

Always display `n` for small slices; never show only percentages.

## 10. Execution and Outputs

Recommended layout:

```text
outputs/eval/
  <dataset_version>/
    <system_variant>/
      <split>/
        <run_id>/
          config.json
          predictions.jsonl
          metrics.json
          failures.jsonl
          judge_results.jsonl
          report.md
```

`config.json` records:

- Dataset SHA-256 and version
- Git commit
- Model and embedding deployment/version
- Prompt file hashes
- Azure Search index/version
- Cosmos configuration identifier without secrets
- History mode, workers, limit, retry, and rate-limit settings
- Judge model and rubric version
- Run time and random seed

Flush each turn to `predictions.jsonl` immediately. Update `metrics.json` through temporary-file replacement so interrupted runs preserve completed results. Write every failed case to `failures.jsonl`; never remove failures from the denominator.

## 11. Evaluation Tiers

### PR Smoke Test

- 20–30 frozen cases
- Cover all statuses, major intents, one multi-turn revision, and safety cases
- Use fixed fixtures or mocks for external dependencies
- Run quickly, deterministically, and without paid network calls

### Nightly Live Evaluation

- Full dev set
- Real models and test indexes
- Compare `full_system` with `previous_release`
- Save latency, cost, retrieval traces, and judge results

### Release Evaluation

- Frozen test and challenge sets
- Predicted history
- Full baseline comparison
- Human review of regressions, high-risk errors, and judge disagreements
- Produce a signable `report.md`

## 12. Initial Acceptance Gates

Thresholds remain provisional until the first baseline run. Start with hard gates:

- Direct PII leakage: 0
- Cross-course leakage: 0
- Unsafe approval or grade-change compliance: 0
- Evaluation schema validity: 100%
- Session state-transition tests: 100%
- HTTP and structured-response success: at least 99%
- No overall core-metric regression greater than 2 percentage points
- No critical intent or safety-slice regression greater than 5 percentage points

After the first full evaluation, set formal thresholds for routing F1, required-fact F1, citation precision/recall, groundedness, and p95 latency. Version all thresholds and never change them after viewing test results.

## 13. Implementation Tasks

### Phase 0: Gold Data

- [ ] Obtain matched inbound student messages.
- [ ] Complete PII and answer-validity review.
- [ ] Label intents, sources, statuses, required facts, forbidden claims, and citations.
- [ ] Split dev/test/challenge by thread and time, then freeze test.
- [ ] Publish a dataset card, version, and SHA-256.

### Phase 1: Evaluation Framework

- [ ] Create `src/evaluation/types.py` with EvalCase, Prediction, Gold, and ScoredPrediction.
- [ ] Implement a JSONL loader and schema validator that accepts only `eval_ready=true`.
- [ ] Implement a shared `SystemUnderTest` protocol and the five variants.
- [ ] Implement conversation-level concurrency and turn-level sequential execution.
- [ ] Implement incremental predictions and atomic metrics persistence.
- [ ] Record run manifests, error codes, latency, token usage, and retrieval traces.

### Phase 2: Scorers

- [ ] Implement deterministic intent, source, and status scoring.
- [ ] Implement Recall@k, MRR, nDCG, and citation metrics.
- [ ] Implement required-fact and forbidden-claim scoring.
- [ ] Implement a structured LLM judge calibrated against human labels.
- [ ] Implement slice metrics, conversation all-correct, confidence intervals, and regression detection.

### Phase 3: Safety and Multi-turn

- [ ] Build PII, prompt-injection, cross-course, and unauthorised-action challenge sets.
- [ ] Cover gold/predicted history, revision adherence, and error propagation.
- [ ] Cover acceptance persistence, idempotency, failure atomicity, and restart behaviour.

### Phase 4: Automation

- [ ] Add the smoke set to CI.
- [ ] Configure nightly live evaluation with a cost cap.
- [ ] Build previous-release comparison reports and release gates.
- [ ] Periodically review stale gold labels, index contamination, PII, and slice coverage.

## 14. Definition of Done

The evaluation system is complete only when:

1. Every test case has `pii_reviewed=true` and `eval_ready=true`.
2. Gold data cannot leak into system inputs.
3. Every failure remains in the denominator with an explicit error code.
4. Results are reproducible from dataset, code, prompt, model, and index versions.
5. Reports identify whether regressions originate in routing, retrieval, generation, citations, sessions, or dependencies.
6. Release gates cover correctness, privacy, safety, reliability, latency, and cost.
