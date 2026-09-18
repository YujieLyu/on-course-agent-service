import json

from pydantic import Field

from src.logger import get_logger
from src.models import ChatMessage, ReplyResponse, SearchResult, StrictModel
from src.workflow.llm import load_system_prompt, request_structured_output


logger = get_logger(__name__)


class EvidenceReference(StrictModel):
    evidence_id: str
    quote: str = Field(min_length=1)


class EvidenceReview(StrictModel):
    claims_supported: bool
    answers_question: bool
    has_factual_claims: bool
    evidence: list[EvidenceReference]


def insufficient_evidence_reply() -> ReplyResponse:
    return ReplyResponse(
        reply="Hi, I don't have enough confirmed information to answer this question.",
        status="not_found",
        citations=[],
    )


async def enforce_evidence_guard(
    *,
    query: str,
    response: ReplyResponse,
    client_type: str,
    model: str,
    retrieved_context: list[SearchResult],
    chat_history: list[ChatMessage],
    teacher_feedback: list[str],
    allow_unreferenced_answer: bool = False,
) -> ReplyResponse:
    """Review independently and fail closed before releasing a generated draft."""
    sources = {
        f"source_{index}": result.content
        for index, result in enumerate(retrieved_context, start=1)
    }
    sources.update({
        f"teacher_feedback_{index}": feedback
        for index, feedback in enumerate(teacher_feedback, start=1)
        if feedback.strip()
    })

    def reject(reason: str) -> ReplyResponse:
        logger.info("reply.evidence_guard", extra={"event_fields": {
            "decision": "blocked", "reason": reason,
        }})
        return insufficient_evidence_reply()

    # Do not let an ostensibly cautious draft carry unsupported conclusions.
    if response.status == "not_found":
        return reject("not_found")
    if response.status == "answered" and not sources and not allow_unreferenced_answer:
        return reject("no_evidence")

    review = await request_structured_output(
        client_type=client_type,
        model=model,
        instructions=load_system_prompt("evidence_guard").substitute(),
        input=json.dumps({
            "student_question": query,
            "chat_history": [message.model_dump(mode="json") for message in chat_history],
            "retrieved_sources": [
                {"evidence_id": f"source_{index}", **result.model_dump(mode="json")}
                for index, result in enumerate(retrieved_context, start=1)
            ],
            "teacher_feedback": [
                {"evidence_id": key, "content": value}
                for key, value in sources.items() if key.startswith("teacher_feedback_")
            ],
            "draft_response": response.model_dump(mode="json"),
        }),
        output_type=EvidenceReview,
        request_name="Evidence guard",
        # Do not let earlier assistant drafts or reviews become evidence.
        conversation_id=None,
    )
    if not review.claims_supported:
        return reject("unsupported_claims")
    if response.status == "answered" and not review.answers_question:
        return reject("unanswered_question")

    verified_ids: set[str] = set()
    for reference in review.evidence:
        content = sources.get(reference.evidence_id)
        quote = " ".join(reference.quote.split())
        if content is None or not quote or quote not in " ".join(content.split()):
            return reject("invalid_evidence_reference")
        verified_ids.add(reference.evidence_id)

    needs_evidence = review.has_factual_claims or (
        response.status == "answered" and not allow_unreferenced_answer
    )
    if needs_evidence and not verified_ids:
        return reject("missing_evidence_reference")

    # Return original source objects, never model-invented citation contents.
    citations = [
        result for index, result in enumerate(retrieved_context, start=1)
        if f"source_{index}" in verified_ids
    ]
    logger.info("reply.evidence_guard", extra={"event_fields": {
        "decision": "passed", "evidence_count": len(verified_ids),
    }})
    return response.model_copy(update={"citations": citations})
