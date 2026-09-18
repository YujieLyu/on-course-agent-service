
from fastapi import FastAPI, HTTPException

from src.agents.orchestrator import generate_session_revision, run_orchestration
from src.logger import get_logger
from src.observability import RequestLoggingMiddleware
from src.models import (
    CreateReplySessionRequest,
    HTTPErrorResponse,
    ReplyRequest,
    ReplySession,
    ReviewReplySessionRequest,
    SessionStatus,
)
from src.services.historical_qna import save_accepted_qna
from src.reply_sessions import (
    InvalidSessionActionError,
    ReplySessionStore,
    SessionNotFoundError,
)

app = FastAPI(
    title="OnCourse Agent API",
    description="Student reply sessions with lecturer review and revision workflows.",
    version="0.1.0",
    openapi_url="/openapi.json",
    docs_url="/docs",
    redoc_url="/redoc",
    servers=[
        {"url": "http://127.0.0.1:8000", "description": "Local development"},
    ],
    openapi_tags=[
        {"name": "Health", "description": "Service availability."},
        {
            "name": "Reply Sessions",
            "description": "Create, generate, review, and accept lecturer reply drafts.",
        },
    ],
)
app.add_middleware(RequestLoggingMiddleware)
logger = get_logger(__name__)
reply_sessions = ReplySessionStore()

@app.get(
    "/health",
    tags=["Health"],
    summary="Check service health",
    description="Check that the application responds. This does not check upstream dependencies.",
    operation_id="check_health",
    response_model=str,
)
def check_health() -> str:
    return "running well"

@app.post(
    "/reply-sessions",
    tags=["Reply Sessions"],
    summary="Create a reply session",
    description="Create a session in pending_generation state and return its conversation_id.",
    operation_id="create_reply_session",
    response_model=ReplySession,
    status_code=201,
    responses={503: {"model": HTTPErrorResponse, "description": "The session could not be created."}},
)
async def create_reply_session(request: CreateReplySessionRequest) -> ReplySession:
    try:
        return await reply_sessions.create(
            request.question,
            request.course_id,
            request.user_id,
        )
    except Exception:
        logger.exception(
            "Failed to create reply session",
            extra={"event_fields": {"course_id": request.course_id}},
        )
        raise HTTPException(status_code=503, detail="The service could not create a session")


@app.get(
    "/reply-sessions/{conversation_id}",
    tags=["Reply Sessions"],
    summary="Get a reply session",
    description="Return the current session state, student question, feedback, and generated revisions.",
    operation_id="get_reply_session",
    response_model=ReplySession,
    responses={404: {"model": HTTPErrorResponse, "description": "Reply session not found."}},
)
def get_reply_session(conversation_id: str) -> ReplySession:
    try:
        return reply_sessions.get(conversation_id)
    except SessionNotFoundError:
        raise HTTPException(status_code=404, detail="Reply session not found")


@app.post(
    "/reply-sessions/{conversation_id}/generate",
    tags=["Reply Sessions"],
    summary="Generate or revise a session reply",
    description=(
        "Generate the first draft for a pending_generation session, or revise a draft for a "
        "revision_requested session using teacher feedback and existing evidence. "
        "The updated session enters awaiting_review. No request body is required."
    ),
    operation_id="generate_session_reply",
    response_model=ReplySession,
    responses={
        404: {"model": HTTPErrorResponse, "description": "Reply session not found."},
        409: {"model": HTTPErrorResponse, "description": "The session is not ready for generation."},
        503: {"model": HTTPErrorResponse, "description": "The reply could not be generated or checked."},
    },
)
async def generate_session_reply(conversation_id: str) -> ReplySession:
    try:
        session = reply_sessions.get(conversation_id)
        if session.status == SessionStatus.REVISION_REQUESTED:
            return await generate_session_revision(conversation_id, reply_sessions)
        if session.status == SessionStatus.PENDING_GENERATION:
            response = await run_orchestration(
                ReplyRequest(
                    question=session.student_query,
                    user_id=session.user_id or conversation_id,
                    course_id=session.course_id,
                ),
                session_context=session.model_dump_json(),
                conversation_id=conversation_id,
                session_store=reply_sessions,
            )
            return reply_sessions.save_generation(conversation_id, response.reply)
        raise InvalidSessionActionError("Session is not ready for generation")
    except SessionNotFoundError:
        raise HTTPException(status_code=404, detail="Reply session not found")
    except InvalidSessionActionError as error:
        raise HTTPException(status_code=409, detail=str(error))
    except Exception:
        logger.exception(
            "Failed to generate session reply",
            extra={"event_fields": {"conversation_id": conversation_id}},
        )
        raise HTTPException(status_code=503, detail="The service could not generate a reply")


@app.post(
    "/reply-sessions/{conversation_id}/review",
    tags=["Reply Sessions"],
    summary="Request a reply revision",
    description=(
        "Record teacher feedback for an awaiting_review session and move it to revision_requested. "
        "Call the generate operation to produce the revised draft."
    ),
    operation_id="review_reply_session",
    response_model=ReplySession,
    responses={
        404: {"model": HTTPErrorResponse, "description": "Reply session not found."},
        409: {"model": HTTPErrorResponse, "description": "The session is not awaiting review."},
    },
)
def review_reply_session(
    conversation_id: str, request: ReviewReplySessionRequest
) -> ReplySession:
    try:
        return reply_sessions.request_revision(conversation_id, request.feedback)
    except SessionNotFoundError:
        raise HTTPException(status_code=404, detail="Reply session not found")
    except InvalidSessionActionError as error:
        raise HTTPException(status_code=409, detail=str(error))


@app.post(
    "/reply-sessions/{conversation_id}/accept",
    tags=["Reply Sessions"],
    summary="Accept a session reply",
    description=(
        "Persist the final draft from an awaiting_review session as historical Q&A, "
        "then mark the session accepted. No request body is required."
    ),
    operation_id="accept_reply_session",
    response_model=ReplySession,
    responses={
        404: {"model": HTTPErrorResponse, "description": "Reply session not found."},
        409: {"model": HTTPErrorResponse, "description": "The session is not awaiting review."},
        503: {"model": HTTPErrorResponse, "description": "The accepted reply could not be persisted."},
    },
)
async def accept_reply_session(conversation_id: str) -> ReplySession:
    try:
        session = reply_sessions.get(conversation_id)
        if session.status != SessionStatus.AWAITING_REVIEW:
            raise InvalidSessionActionError("Session is not awaiting review")
        await save_accepted_qna(session)
        return reply_sessions.accept(conversation_id)
    except SessionNotFoundError:
        raise HTTPException(status_code=404, detail="Reply session not found")
    except InvalidSessionActionError as error:
        raise HTTPException(status_code=409, detail=str(error))
    except Exception:
        logger.exception(
            "Failed to persist accepted historical Q&A",
            extra={"event_fields": {"conversation_id": conversation_id}},
        )
        raise HTTPException(status_code=503, detail="The service could not accept the reply")
