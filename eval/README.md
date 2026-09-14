# SMNA evaluation data preparation

`SMNA-sent-matched.csv` contains raw lecturer/staff sent mail and must not be
committed or shared. The checked-in `.gitignore` excludes both the raw file and
all generated, not-yet-reviewed outputs.

## Generate scrubbed candidates

From the repository root, run:

```bash
uv run python -m scripts.scrub_eval_data \
  --input eval/SMNA-sent-matched.csv \
  --output-dir eval/processed
```

The command validates the source schema and `body_chars`, leaves the source file
unchanged, removes exact timestamps, scrubs direct identifier patterns, strips
signatures, and writes:

- `SMNA-messages.scrubbed.jsonl`: cleaned row-level staff messages;
- `SMNA-eval.candidates.jsonl`: one candidate reference answer per thread;
- `SMNA-eval.review.jsonl`: required human-review actions;
- `SMNA-eval.rejected.jsonl`: excluded source records and reasons;
- `SMNA-eval.audit.json`: source hash, counts, redactions, and limitations.

## Important limitation

The source export has no attributable student message body. Its `thread` value
is the email subject with reply prefixes removed, not the student question.
Consequently, generated candidates deliberately have:

```json
{
  "question": null,
  "pii_reviewed": false,
  "eval_ready": false
}
```

Do not synthesize a question from the lecturer answer and treat it as gold data.
Supply the matched inbound student message or have an authorised reviewer enter
and verify the original question.

## Human review checklist

For every candidate selected for the final evaluation set:

1. Supply and scrub the student question.
2. Verify the sender is lecturer/staff and the final message is the intended
   reference answer.
3. Check names and indirect identifiers that rule-based matching may miss.
4. Confirm that the answer is complete, current, and not dependent on private
   student state.
5. Assign `intent`, `expected_sources`, and atomic `required_facts`.
6. Remove or consolidate candidates flagged `duplicate_reference_answer`.
7. Set `pii_reviewed` and `eval_ready` to `true` only after review.

The final canonical dataset should remain JSONL. Split or deduplicate by
`thread_id`, never by individual message, to avoid leakage.
