from src.agents.intent_classifier import intent_classify
from src.agents.reply_agent import run_answer
from src.config import get_model_name
from src.logger import get_logger, log_operation
from src.models import (
    ClientType,
    ReplyRequest,
    ReplyResponse,
    ReplySession,
    SessionStatus,
)
from src.reply_sessions import InvalidSessionActionError, ReplySessionStore
from src.tools.search import search


reply_model_name = get_model_name("REPLY_MODEL_NAME")
intent_classifier_model_name = get_model_name("INTENT_CLASSIFIER_MODEL_NAME")
logger = get_logger(__name__)

async def run_orchestration(
    request: ReplyRequest,
    *,
    session_context: str | None = None,
    conversation_id: str | None = None,
    session_store: ReplySessionStore | None = None,
) -> ReplyResponse:
    with log_operation(
        logger, "reply.generate", course_id=request.course_id, conversation_id=conversation_id,
    ) as event:
        intent_response = await intent_classify(
            request.question,
            ClientType.OPENAI,
            intent_classifier_model_name,
            request.chat_history,
            session_context,
        )
        logger.info("reply.classified", extra={"event_fields": {
            "intent": intent_response.intent,
            "search_sources": intent_response.search_sources,
            "needs_clarification": intent_response.needs_clarification,
        }})
        search_query = intent_response.search_query or request.question
        search_results = (
            await search(search_query, intent_response.search_sources, request.course_id)
        ).results
        if conversation_id is not None and session_store is not None:
            session_store.save_evidence_context(conversation_id, intent_response, search_results)
        response = await run_answer(
            request.question,
            ClientType.OPENAI,
            reply_model_name,
            request.chat_history,
            search_results,
            session_context=session_context,
            conversation_id=conversation_id,
            intent_context=intent_response,
        )
        event.update(reply_status=response.status, citation_count=len(response.citations))
        return response


async def generate_session_revision(
    conversation_id: str,
    store: ReplySessionStore,
) -> ReplySession:
    """Generate a revision using context already stored in the conversation."""
    session = store.get(conversation_id)
    if session.status != SessionStatus.REVISION_REQUESTED:
        raise InvalidSessionActionError("Session has no revision request")
    intent_context, retrieved_context = store.get_evidence_context(conversation_id)

    with log_operation(
        logger, "reply.revise", conversation_id=conversation_id, course_id=session.course_id,
    ):
        response = await run_answer(
            session.student_query,
            ClientType.OPENAI,
            reply_model_name,
            session_context=session.model_dump_json(),
            retrieved_context=retrieved_context,
            conversation_id=conversation_id,
            intent_context=intent_context,
            is_revision=True,
            teacher_feedback=[
                revision.feedback for revision in session.revisions if revision.feedback
            ],
        )
        return store.save_generation(conversation_id, response.reply)
