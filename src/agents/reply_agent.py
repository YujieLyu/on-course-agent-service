import json
from collections.abc import Sequence

from src.domain.evidence import merge_citations
from src.logger import get_logger
from src.models import ReplyDraft, ReplyResponse, SearchResult
from src.workflow.llm import (
    load_system_prompt,
    request_structured_output,
)


logger = get_logger(__name__)


async def run_answer(
    query: str,
    model: str,
    retrieved_context: Sequence[SearchResult] = (),
    *,
    previous_reply: str | None = None,
    feedback_history: Sequence[str] = (),
) -> ReplyResponse:
    # The original retrieval order is persisted, keeping source IDs stable.
    sources = {
        f"source_{index}": result
        for index, result in enumerate(retrieved_context, start=1)
    }
    draft = await request_structured_output(
        model=model,
        instructions=load_system_prompt("reply_agent").substitute(),
        input=[
            {
                "role": "developer",
                "content": "Authoritative retrieved context:\n" + _format_retrieved_context(sources),
            },
            {
                "role": "user",
                "content": json.dumps({
                    "student_query": query,
                    "previous_reply": previous_reply,
                    "feedback_history": list(feedback_history),
                    "task": "Revise the reply using teacher feedback." if previous_reply is not None
                            else "Generate the initial reply.",
                }, ensure_ascii=False),
            },
        ],
        output_type=ReplyDraft,
        request_name="Answer agent",
    )
    unknown_count = sum(source_id not in sources for source_id in draft.cited_source_ids)
    if unknown_count:
        logger.warning("reply.citations.unknown_sources", extra={"event_fields": {
            "unknown_source_count": unknown_count,
        }})
    return ReplyResponse(
        reply=draft.reply,
        status=draft.status,
        citations=list(merge_citations(
            sources[source_id] for source_id in draft.cited_source_ids if source_id in sources
        )),
    )


def _format_retrieved_context(sources: dict[str, SearchResult]) -> str:
    if not sources:
        return "No retrieved context."

    formatted_results = []
    for source_id, result in sources.items():
        title = result.title or "Untitled source"
        source_url = f"\nurl: {result.source_url}" if result.source_url else ""
        excerpt = f"\nexcerpt: {result.excerpt}" if result.excerpt else ""
        formatted_results.append(
            f"[{source_id}] source: {result.source}\n"
            f"title: {title}{source_url}{excerpt}\n"
            f"content: {result.content}"
        )

    return "\n\n".join(formatted_results)
