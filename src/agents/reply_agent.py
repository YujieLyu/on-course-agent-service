
from src.agents.intent_classifier import _format_conversation_context
from src.models import ChatMessage, ReplyResponse, SearchResult
from src.workflow.llm import load_system_prompt, request_structured_output


async def run_answer(  
    query: str,
    client_type: str,
    model: str,
    chat_history: list[ChatMessage] | None = None,
    retrieved_context: list[SearchResult] | None = None,
) -> ReplyResponse:
    system_prompt = load_system_prompt("reply_agent")
    instructions = system_prompt.substitute(
        conversation_context=_format_conversation_context(chat_history or []),
        retrieved_context=_format_retrieved_context(retrieved_context or []),
    )
    return await request_structured_output(
        client_type=client_type,
        model=model,
        instructions=instructions,
        input=query,
        output_type=ReplyResponse,
        request_name="Answer agent",
    )


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