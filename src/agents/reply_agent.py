from src.agents.intent_classifier import _format_conversation_context
from src.models import ChatMessage, IntentClassification, ReplyResponse, SearchResult
from src.workflow.llm import load_system_prompt, request_structured_output


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
) -> ReplyResponse:
    system_prompt = load_system_prompt("reply_agent")
    formatted_retrieved_context = _format_retrieved_context(retrieved_context or [])
    instructions = system_prompt.substitute(
        conversation_context=_format_conversation_context(chat_history or []),
        retrieved_context=(
            "Use the authoritative context stored in the conversation's developer input."
            if conversation_id
            else formatted_retrieved_context
        ),
        session_context=(
            "Provided in the current conversation user input."
            if conversation_id
            else session_context or "No active reply session."
        ),
    )
    return await request_structured_output(
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
            if conversation_id
            else query
        ),
        output_type=ReplyResponse,
        request_name="Answer agent",
        conversation_id=conversation_id,
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
    if intent_context is not None:
        items.append(
            {
                "role": "developer",
                "content": (
                    "Intent classification:\n"
                    f"{intent_context.model_dump_json()}\n\n"
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
