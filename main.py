import asyncio
from uuid import uuid4
from fastapi import FastAPI, HTTPException

from src.logger import get_logger
from src.observability import RequestLoggingMiddleware
from src.models import (
    CreateReplySessionRequest,
    HTTPErrorResponse,
    ReplySession as ReplySessionResponse,
    ReviewReplySessionRequest,
)
from src.bootstrap import create_session_service, save_accepted_qna
from src.services.reply_acceptance import accept_reply
from src.domain.reply_sessions import (
    InvalidSessionActionError,
    QaMemoryConflictError,
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
reply_sessions = create_session_service()

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
    description="Persist a pending_generation session with a fixed 30-day lifetime and return its conversation_id.",
    operation_id="create_reply_session",
    response_model=ReplySessionResponse,
    status_code=201,
    responses={503: {"model": HTTPErrorResponse, "description": "The session could not be created."}},
)
async def create_reply_session(request: CreateReplySessionRequest) -> ReplySessionResponse:
    try:
        conversation_id = uuid4().hex
        session = await asyncio.to_thread(
            reply_sessions.create, conversation_id, request.question,
            request.course_id,
            request.user_id,
        )
        return ReplySessionResponse.model_validate(session)
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
    response_model=ReplySessionResponse,
    responses={
        404: {"model": HTTPErrorResponse, "description": "Reply session not found or expired."},
        503: {"model": HTTPErrorResponse, "description": "The session could not be retrieved."},
    },
)
def get_reply_session(conversation_id: str) -> ReplySessionResponse:
    try:
        return ReplySessionResponse.model_validate(reply_sessions.get(conversation_id))
    except SessionNotFoundError:
        raise HTTPException(status_code=404, detail="Reply session not found")
    except Exception:
        logger.exception("Failed to retrieve reply session")
        raise HTTPException(status_code=503, detail="The service could not retrieve the session")


@app.post(
    "/reply-sessions/{conversation_id}/generate",
    tags=["Reply Sessions"],
    summary="Generate the initial session reply",
    description=(
        "Search all four sources and generate the first draft for a pending_generation session. "
        "The updated session enters awaiting_review. No request body is required."
    ),
    operation_id="generate_session_reply",
    response_model=ReplySessionResponse,
    responses={
        404: {"model": HTTPErrorResponse, "description": "Reply session not found."},
        409: {"model": HTTPErrorResponse, "description": "The session is not ready for generation."},
        503: {"model": HTTPErrorResponse, "description": "The reply could not be generated."},
    },
)
async def generate_session_reply(conversation_id: str) -> ReplySessionResponse:
    try:
        session = await reply_sessions.generate(conversation_id)
        return ReplySessionResponse.model_validate(session)
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
    summary="Revise a reply using teacher feedback",
    description=(
        "Generate a revision using teacher feedback and the saved retrieval. "
        "Save feedback and the completed reply together, returning an awaiting_review session. "
        "A failed review leaves the previous version unchanged."
    ),
    operation_id="review_reply_session",
    response_model=ReplySessionResponse,
    responses={
        404: {"model": HTTPErrorResponse, "description": "Reply session not found."},
        409: {"model": HTTPErrorResponse, "description": "The session is not awaiting review."},
        503: {"model": HTTPErrorResponse, "description": "The reply could not be revised."},
    },
)
async def review_reply_session(
    conversation_id: str, request: ReviewReplySessionRequest
) -> ReplySessionResponse:
    try:
        session = await reply_sessions.review(conversation_id, request.feedback)
        return ReplySessionResponse.model_validate(session)
    except SessionNotFoundError:
        raise HTTPException(status_code=404, detail="Reply session not found")
    except InvalidSessionActionError as error:
        raise HTTPException(status_code=409, detail=str(error))
    except Exception:
        logger.exception("Failed to revise reply session")
        raise HTTPException(status_code=503, detail="The service could not revise the reply")


@app.post(
    "/reply-sessions/{conversation_id}/accept",
    tags=["Reply Sessions"],
    summary="Accept a session reply",
    description=(
        "Conditionally lock the reviewed revision in accepting state, publish it to QA Memory, "
        "then mark it accepted. Retry accepting sessions to resume publication; accepted sessions "
        "return successfully without another write. No request body is required."
    ),
    operation_id="accept_reply_session",
    response_model=ReplySessionResponse,
    responses={
        404: {"model": HTTPErrorResponse, "description": "Reply session not found."},
        409: {"model": HTTPErrorResponse, "description": "Session state, revision, or QA Memory version conflicts with acceptance."},
        503: {"model": HTTPErrorResponse, "description": "The accepted reply could not be persisted."},
    },
)
async def accept_reply_session(conversation_id: str) -> ReplySessionResponse:
    try:
        session = await accept_reply(conversation_id, reply_sessions, save_accepted_qna)
        return ReplySessionResponse.model_validate(session)
    except SessionNotFoundError:
        raise HTTPException(status_code=404, detail="Reply session not found")
    except (InvalidSessionActionError, QaMemoryConflictError) as error:
        raise HTTPException(status_code=409, detail=str(error))
    except Exception:
        logger.exception(
            "Failed to persist accepted historical Q&A",
            extra={"event_fields": {"conversation_id": conversation_id}},
        )
        raise HTTPException(status_code=503, detail="The service could not accept the reply")
