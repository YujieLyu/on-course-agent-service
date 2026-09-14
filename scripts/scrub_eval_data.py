"""Scrub lecturer email exports and prepare human-reviewable eval candidates.

The source file is treated as read-only. Outputs contain no exact timestamps and
do not claim to be eval-ready when the student question is absent.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import html
import json
import re
import unicodedata
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit, urlunsplit


EXPECTED_COLUMNS = ["datetime", "thread", "subject", "body_chars", "body"]
EMAIL_RE = re.compile(
    r"(?i)\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b"
)
STUDENT_ID_RE = re.compile(
    r"(?i)\b(?:s\d{7,10}|student\s*(?:id|number)?\s*[:#]?\s*\d{6,10})\b"
)
URL_RE = re.compile(r"(?i)\bhttps?://[^\s<>\]\[\"']+")
PHONE_CANDIDATE_RE = re.compile(
    r"(?<![\w])(?:\+?\d[\d ()-]{6,}\d)(?![\w])"
)
TITLE_PERSON_RE = re.compile(
    r"(?i)\b(?:prof(?:essor)?|dr|mr|mrs|ms|miss)\.?\s+"
    r"[A-Z][A-Za-z'’-]+(?:\s+[A-Z][A-Za-z'’-]+){0,3}"
)
GREETING_RE = re.compile(
    r"(?i)(^|(?<=[.!?])\s+)(\s*(?:hi|hello|dear|hey|good\s+(?:morning|afternoon|evening))\s+)"
    r"([^,.!?]{1,60})([,!.])"
)
SIGNATURE_RE = re.compile(
    r"(?i)\b(?:kind regards|best regards|warm regards|regards|cheers)\b.*$"
)
NAME_BEFORE_EMAIL_RE = re.compile(
    r"\b([A-Z][A-Za-z'’-]+(?:\s+[A-Z][A-Za-z'’-]+){0,5})\s*"
    r"<\s*(?i:[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,})\s*>"
)
HEADER_NAME_RE = re.compile(
    r"(?i:\b(?:from|to|cc):\s+)"
    r"([A-Z][A-Za-z'’-]+(?:\s+[A-Z][A-Za-z'’-]+){0,5})"
    r"(?=\s+(?i:date|sent|to|cc|subject):)"
)
SIGNOFF_NAME_RE = re.compile(
    r"(?i:\b(?:thanks|thank you|many thanks)[,!]?\s+)"
    r"([A-Z][A-Za-z'’-]+(?:\s+[A-Z][A-Za-z'’-]+){0,3})"
    r"(?=\s+(?i:from|sent|date|to|cc|subject):|\s*[.!]?$)"
)
CONTACT_NAME_RE = re.compile(
    r"(?i:\b(?:contact|email|message|call|speak\s+to|speak\s+with|talk\s+to|"
    r"talk\s+with|check\s+with|forward(?:ed)?\s+to|reach\s+out\s+to)\s+)"
    r"([A-Z][A-Za-z'’-]+(?:\s+[A-Z][A-Za-z'’-]+){0,3})"
)
POSSESSIVE_NAME_RE = re.compile(
    r"(?i:\b(?:check|review|mark|grade|submission|extension|request|email|group)\s+)"
    r"([A-Z][A-Za-z'’-]+)['’]s\b"
)
VOCATIVE_NAME_RE = re.compile(
    r",\s+([A-Z][a-z'’-]{2,}(?:\s+[A-Z][a-z'’-]{2,}){0,2})[!?,](?=\s|$)"
)
EMAIL_HEADER_TIMESTAMP_RE = re.compile(
    r"(?i)\b(date|sent):\s+.*?(?=\s+(?:from|to|cc|subject):)"
)
RESIDUAL_PERSON_CONTEXT_PATTERNS = (
    re.compile(
        r"\b[A-Z][A-Za-z'’-]+(?:\s+[A-Z][A-Za-z'’-]+){0,4}\s*<\[EMAIL_\d+\]>"
    ),
    re.compile(
        r"(?i:\b(?:from|to|cc):\s+)"
        r"[A-Z][A-Za-z'’-]+(?:\s+[A-Z][A-Za-z'’-]+){0,5}"
        r"(?=\s+(?i:date|sent|to|cc|subject):)"
    ),
    re.compile(
        r"(?i:\b(?:thanks|thank you|many thanks)[,!]?\s+)"
        r"[A-Z][A-Za-z'’-]+(?:\s+[A-Z][A-Za-z'’-]+){0,3}"
        r"(?=\s+(?i:from|sent|date|to|cc|subject):|\s*[.!]?$)"
    ),
    VOCATIVE_NAME_RE,
)
REPLY_PREFIX_RE = re.compile(r"(?i)^\s*(?:(?:re|fw|fwd)\s*:\s*)+")
PUBLIC_RMIT_HOSTS = {"rmit.edu.au", "www.rmit.edu.au"}
NON_PERSON_GREETINGS = {
    "all",
    "class",
    "everyone",
    "folks",
    "students",
    "team",
    "there",
    "hi",
    "the",
}


@dataclass
class ScrubStats:
    redactions: Counter[str] = field(default_factory=Counter)
    residual_hits: Counter[str] = field(default_factory=Counter)
    quality_flags: Counter[str] = field(default_factory=Counter)


class ThreadRedactor:
    """Assign stable typed placeholders within one thread, without saving a map."""

    def __init__(self, stats: ScrubStats):
        self.stats = stats
        self._tokens: dict[str, dict[str, str]] = defaultdict(dict)
        self._known_people: set[str] = set()

    def discover_people(self, value: str) -> None:
        """Find high-confidence person mentions before replacing surrounding PII."""
        text = normalize_text(value)
        patterns = (
            NAME_BEFORE_EMAIL_RE,
            HEADER_NAME_RE,
            SIGNOFF_NAME_RE,
            CONTACT_NAME_RE,
            POSSESSIVE_NAME_RE,
            VOCATIVE_NAME_RE,
        )
        for pattern in patterns:
            for match in pattern.finditer(text):
                self._remember_person(match.group(1))
        for match in GREETING_RE.finditer(text):
            self._remember_person(match.group(3))

    def _remember_person(self, value: str) -> None:
        person = value.strip()
        first_word = person.split(maxsplit=1)[0].casefold() if person else ""
        if person and first_word not in NON_PERSON_GREETINGS:
            self._known_people.add(person)

    def _token(self, kind: str, value: str) -> str:
        normalized = unicodedata.normalize("NFKC", value).casefold()
        values = self._tokens[kind]
        if normalized not in values:
            values[normalized] = f"[{kind}_{len(values) + 1}]"
        self.stats.redactions[kind] += 1
        return values[normalized]

    def scrub(self, value: str, *, strip_signature: bool = False) -> str:
        text = normalize_text(value)
        if not text:
            return text
        self.discover_people(text)

        if strip_signature:
            text, count = SIGNATURE_RE.subn("[SIGNATURE]", text, count=1)
            self.stats.redactions["SIGNATURE"] += count

        for person in sorted(self._known_people, key=len, reverse=True):
            text = re.sub(
                rf"(?<!\w){re.escape(person)}(?!\w)",
                lambda match: self._token("PERSON", match.group()),
                text,
                flags=re.IGNORECASE,
            )

        text = EMAIL_RE.sub(lambda match: self._token("EMAIL", match.group()), text)
        text = STUDENT_ID_RE.sub(
            lambda match: self._token("STUDENT_ID", match.group()), text
        )
        text = TITLE_PERSON_RE.sub(
            lambda match: self._token("PERSON", match.group()), text
        )
        text = URL_RE.sub(self._scrub_url, text)
        text = PHONE_CANDIDATE_RE.sub(self._scrub_phone_candidate, text)
        text, timestamp_count = EMAIL_HEADER_TIMESTAMP_RE.subn(
            lambda match: f"{match.group(1).title()}: [EMAIL_TIMESTAMP]",
            text,
        )
        self.stats.redactions["EMAIL_TIMESTAMP"] += timestamp_count
        return normalize_text(text)

    def _scrub_url(self, match: re.Match[str]) -> str:
        raw = match.group().rstrip(".,;:!?)")
        trailing = match.group()[len(raw) :]
        parts = urlsplit(raw)
        hostname = (parts.hostname or "").casefold()
        if hostname in PUBLIC_RMIT_HOSTS:
            self.stats.redactions["URL_QUERY_OR_FRAGMENT"] += bool(
                parts.query or parts.fragment
            )
            safe = urlunsplit((parts.scheme, parts.netloc, parts.path, "", ""))
            return safe + trailing
        return self._token("PRIVATE_URL", raw) + trailing

    def _scrub_phone_candidate(self, match: re.Match[str]) -> str:
        value = match.group()
        digits = re.sub(r"\D", "", value)
        if 8 <= len(digits) <= 15:
            return self._token("PHONE", value)
        return value


def normalize_text(value: str) -> str:
    text = unicodedata.normalize("NFKC", html.unescape(value or ""))
    text = text.replace("\u200b", "").replace("\ufeff", "")
    return re.sub(r"\s+", " ", text).strip()


def normalize_subject(value: str) -> str:
    return REPLY_PREFIX_RE.sub("", normalize_text(value))


def timestamp_bucket(value: str) -> str | None:
    try:
        return datetime.fromisoformat(value).strftime("%Y-%m")
    except ValueError:
        return None


def scan_residual_pii(text: str) -> Counter[str]:
    hits: Counter[str] = Counter()
    hits["EMAIL"] = len(EMAIL_RE.findall(text))
    hits["STUDENT_ID"] = len(STUDENT_ID_RE.findall(text))
    hits["PRIVATE_OR_UNSANITIZED_URL"] = sum(
        1
        for match in URL_RE.finditer(text)
        if (urlsplit(match.group()).hostname or "").casefold() not in PUBLIC_RMIT_HOSTS
        or bool(urlsplit(match.group()).query or urlsplit(match.group()).fragment)
    )
    hits["PHONE"] = sum(
        1
        for match in PHONE_CANDIDATE_RE.finditer(text)
        if 8 <= len(re.sub(r"\D", "", match.group())) <= 15
    )
    hits["PERSON_CONTEXT"] = sum(
        len(pattern.findall(text)) for pattern in RESIDUAL_PERSON_CONTEXT_PATTERNS
    )
    return Counter({key: value for key, value in hits.items() if value})


def load_rows(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8-sig", newline="") as source:
        reader = csv.DictReader(source)
        if reader.fieldnames != EXPECTED_COLUMNS:
            raise ValueError(
                f"Unexpected columns: {reader.fieldnames}; expected {EXPECTED_COLUMNS}"
            )
        rows = list(reader)

    for index, row in enumerate(rows, start=2):
        expected_length = row.get("body_chars", "")
        if not expected_length.isdigit() or int(expected_length) != len(row["body"]):
            raise ValueError(f"body_chars mismatch at CSV line {index}")
    return rows


def write_jsonl(path: Path, records: list[dict[str, Any]]) -> None:
    with path.open("w", encoding="utf-8") as output:
        for record in records:
            output.write(json.dumps(record, ensure_ascii=False) + "\n")


def build_outputs(rows: list[dict[str, str]]) -> tuple[
    list[dict[str, Any]],
    list[dict[str, Any]],
    list[dict[str, Any]],
    list[dict[str, Any]],
    dict[str, Any],
]:
    stats = ScrubStats()
    raw_threads: dict[str, list[tuple[int, dict[str, str]]]] = defaultdict(list)
    rejected: list[dict[str, Any]] = []

    for row_number, row in enumerate(rows, start=1):
        thread = normalize_subject(row["thread"])
        if not thread:
            rejected.append(
                {
                    "source_row": row_number,
                    "reason": "missing_thread",
                    "contains_raw_text": False,
                }
            )
            stats.quality_flags["missing_thread"] += 1
            continue
        raw_threads[thread].append((row_number, row))

    ordered_threads = sorted(
        raw_threads.items(), key=lambda item: min(row[0] for row in item[1])
    )
    messages: list[dict[str, Any]] = []
    candidates: list[dict[str, Any]] = []
    review_queue: list[dict[str, Any]] = []
    seen_answers: dict[str, str] = {}

    for thread_number, (_, thread_rows) in enumerate(ordered_threads, start=1):
        thread_id = f"smna_thread_{thread_number:04d}"
        redactor = ThreadRedactor(stats)
        thread_rows.sort(key=lambda item: item[1]["datetime"])
        for _, row in thread_rows:
            redactor.discover_people(row["thread"])
            redactor.discover_people(row["subject"])
            redactor.discover_people(row["body"])
        scrubbed_thread = redactor.scrub(
            normalize_subject(thread_rows[0][1]["thread"])
        )
        thread_messages: list[dict[str, Any]] = []

        for message_number, (source_row, row) in enumerate(thread_rows, start=1):
            subject = redactor.scrub(normalize_subject(row["subject"]))
            body = redactor.scrub(row["body"], strip_signature=True)
            residual = scan_residual_pii(f"{subject} {body}")
            stats.residual_hits.update(residual)
            flags = ["manual_pii_review_required"]
            if residual:
                flags.append("residual_pii_pattern_detected")
            if "[SIGNATURE]" not in body:
                flags.append("signature_not_detected")
            for flag in flags:
                stats.quality_flags[flag] += 1

            message = {
                "message_id": f"{thread_id}_message_{message_number:02d}",
                "thread_id": thread_id,
                "source_row": source_row,
                "sender_role": "lecturer_or_staff_unverified",
                "timestamp_bucket": timestamp_bucket(row["datetime"]),
                "subject": subject,
                "body": body,
                "quality_flags": flags,
                "pii_reviewed": False,
            }
            messages.append(message)
            thread_messages.append(message)

        final_message = thread_messages[-1]
        answer_fingerprint = hashlib.sha256(
            final_message["body"].casefold().encode("utf-8")
        ).hexdigest()
        candidate_flags = [
            "missing_student_question",
            "sender_role_unverified",
            "manual_pii_review_required",
        ]
        duplicate_of = seen_answers.get(answer_fingerprint)
        if duplicate_of:
            candidate_flags.append("duplicate_reference_answer")
        else:
            seen_answers[answer_fingerprint] = thread_id
        stats.quality_flags.update(candidate_flags)

        candidate = {
            "id": f"smna_eval_{thread_number:04d}",
            "course_id": "SMNA",
            "question": None,
            "reference_answer": final_message["body"],
            "context": [],
            "metadata": {
                "thread_id": thread_id,
                "subject": final_message["subject"] or scrubbed_thread,
                "time_bucket": final_message["timestamp_bucket"],
                "source": "lecturer_sent_email",
                "source_message_count": len(thread_messages),
            },
            "expected_sources": [],
            "required_facts": [],
            "quality_flags": candidate_flags,
            "pii_reviewed": False,
            "eval_ready": False,
        }
        if duplicate_of:
            candidate["duplicate_of_thread_id"] = duplicate_of
        candidates.append(candidate)
        review_queue.append(
            {
                "id": candidate["id"],
                "thread_id": thread_id,
                "review_actions": [
                    "supply_or_verify_student_question",
                    "verify_sender_role",
                    "review_names_and_indirect_identifiers",
                    "verify_reference_answer_is_complete_and_current",
                    "assign_intent_expected_sources_and_required_facts",
                ],
                "quality_flags": candidate_flags,
            }
        )

    audit = {
        "source": {
            "file": "SMNA-sent-matched.csv",
            "sha256": None,
            "row_count": len(rows),
            "columns": EXPECTED_COLUMNS,
            "raw_file_modified": False,
        },
        "outputs": {
            "scrubbed_message_count": len(messages),
            "candidate_count": len(candidates),
            "eval_ready_count": sum(item["eval_ready"] for item in candidates),
            "review_queue_count": len(review_queue),
            "rejected_count": len(rejected),
        },
        "redactions": dict(sorted(stats.redactions.items())),
        "residual_pattern_hits": dict(sorted(stats.residual_hits.items())),
        "quality_flags": dict(sorted(stats.quality_flags.items())),
        "limitations": [
            "The export contains lecturer/staff sent messages but no attributable student message body.",
            "Names and indirect identifiers require human review; rule-based redaction is not sufficient for release.",
            "No candidate is eval-ready until its question, sender role, PII status, and answer currency are reviewed.",
        ],
    }
    return messages, candidates, review_queue, rejected, audit


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()

    rows = load_rows(args.input)
    messages, candidates, review_queue, rejected, audit = build_outputs(rows)
    audit["source"]["sha256"] = hashlib.sha256(args.input.read_bytes()).hexdigest()

    args.output_dir.mkdir(parents=True, exist_ok=True)
    write_jsonl(args.output_dir / "SMNA-messages.scrubbed.jsonl", messages)
    write_jsonl(args.output_dir / "SMNA-eval.candidates.jsonl", candidates)
    write_jsonl(args.output_dir / "SMNA-eval.review.jsonl", review_queue)
    write_jsonl(args.output_dir / "SMNA-eval.rejected.jsonl", rejected)
    (args.output_dir / "SMNA-eval.audit.json").write_text(
        json.dumps(audit, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(audit["outputs"], indent=2))


if __name__ == "__main__":
    main()
