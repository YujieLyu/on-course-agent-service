from src.agents.context import format_conversation_context
from src.agents.evidence_guard import enforce_evidence_guard
from src.models import ChatMessage, IntentClassification, ReplyResponse, SearchResult, StudentIntent
from src.workflow.llm import (
    load_system_prompt,
    request_structured_output,
    supports_provider_conversation,
)


async def run_answer(
    query: str,
    client_type: str,
    model: str,
    chat_history: list[ChatMessage] | None = None,
    retrieved_context: list[SearchResult] | None = None,
    session_context: str | None = None,
    conversation_id: str | None = None,
    intent_context: IntentClassification | None = None,
    is_revision: bool = False,
    teacher_feedback: list[str] | None = None,
) -> ReplyResponse:
    system_prompt = load_system_prompt("reply_agent")
    formatted_retrieved_context = _format_retrieved_context(retrieved_context or [])
    has_session = conversation_id is not None
    has_provider_conversation = supports_provider_conversation(conversation_id)
    instructions = system_prompt.substitute(
        conversation_context=format_conversation_context(chat_history or []),
        retrieved_context=(
            "Use the authoritative context provided in this request or its reply session."
            if has_session
            else formatted_retrieved_context
        ),
        session_context=(
            "Provided in the current request input."
            if has_session
            else session_context or "No active reply session."
        ),
    )
    response = await request_structured_output(
        client_type=client_type,
        model=model,
        instructions=instructions,
        input=(
            _build_input(
                query=query,
                session_context=session_context,
                intent_context=intent_context,
                retrieved_context=formatted_retrieved_context,
                is_revision=is_revision,
            )
            if has_session
            else query
        ),
        output_type=ReplyResponse,
        request_name="Answer agent",
        conversation_id=conversation_id if has_provider_conversation else None,
    )
    return await enforce_evidence_guard(
        query=query,
        response=response,
        client_type=client_type,
        model=model,
        retrieved_context=retrieved_context or [],
        chat_history=chat_history or [],
        teacher_feedback=teacher_feedback or [],
        allow_unreferenced_answer=(
            intent_context is not None and intent_context.intent == StudentIntent.OUT_OF_SCOPE
        ),
    )


def _build_input(
    *,
    query: str,
    session_context: str | None,
    intent_context: IntentClassification | None,
    retrieved_context: str,
    is_revision: bool,
) -> list[dict[str, str]]:
    items: list[dict[str, str]] = []
    if intent_context is not None or is_revision:
        items.append(
            {
                "role": "developer",
                "content": (
                    "Intent classification:\n"
                    f"{intent_context.model_dump_json() if intent_context else 'Not provided.'}\n\n"
                    "Authoritative retrieved context:\n"
                    f"{retrieved_context}"
                ),
            }
        )

    task = (
        "Revise the previous answer using the latest teacher feedback. "
        "Do not perform a new search."
        if is_revision
        else "Generate the initial answer to the student query."
    )
    items.append(
        {
            "role": "user",
            "content": (
                f"Student query:\n{query}\n\n"
                f"Reply session:\n{session_context or 'No active reply session.'}\n\n"
                f"Task:\n{task}"
            ),
        }
    )
    return items


def _format_retrieved_context(results: list[SearchResult]) -> str:
    if not results:
        return "No retrieved context."

    formatted_results = []
    for index, result in enumerate(results, start=1):
        title = result.title or "Untitled source"
        source_url = f"\nurl: {result.source_url}" if result.source_url else ""
        excerpt = f"\nexcerpt: {result.excerpt}" if result.excerpt else ""
        formatted_results.append(
            f"[{index}] source: {result.source}\n"
            f"title: {title}{source_url}{excerpt}\n"
            f"content: {result.content}"
        )

    return "\n\n".join(formatted_results)
