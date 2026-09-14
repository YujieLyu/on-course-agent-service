
from fastapi import FastAPI, HTTPException, Response, status

from src.agents.orchestrator import generate_session_revision, run_orchestration
from src.logger import get_logger
from src.observability import RequestLoggingMiddleware
from src.models import (
    CreateReplySessionRequest,
    ReplyRequest,
    ReplyResponse,
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

app = FastAPI()
app.add_middleware(RequestLoggingMiddleware)
logger = get_logger(__name__)
reply_sessions = ReplySessionStore()

UPSTREAM_ERROR_RESPONSE = ReplyResponse(
    reply="The service could not complete the request.",
    status="error",
    citations=[],
)

@app.get("/health")
def check_health() -> str:
    return "running well"

@app.post("/reply", responses={503: {"model": ReplyResponse}})
async def generate_reply(request: ReplyRequest, response: Response) -> ReplyResponse:
    try:
        return await run_orchestration(request)
    except Exception:
        logger.exception("Failed to generate reply due to an upstream dependency error")
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
        return UPSTREAM_ERROR_RESPONSE


@app.post("/reply-sessions", response_model=ReplySession, status_code=201)
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


@app.get("/reply-sessions/{conversation_id}", response_model=ReplySession)
def get_reply_session(conversation_id: str) -> ReplySession:
    try:
        return reply_sessions.get(conversation_id)
    except SessionNotFoundError:
        raise HTTPException(status_code=404, detail="Reply session not found")


@app.post("/reply-sessions/{conversation_id}/generate", response_model=ReplySession)
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


@app.post("/reply-sessions/{conversation_id}/review", response_model=ReplySession)
def review_reply_session(
    conversation_id: str, request: ReviewReplySessionRequest
) -> ReplySession:
    try:
        return reply_sessions.request_revision(conversation_id, request.feedback)
    except SessionNotFoundError:
        raise HTTPException(status_code=404, detail="Reply session not found")
    except InvalidSessionActionError as error:
        raise HTTPException(status_code=409, detail=str(error))


@app.post("/reply-sessions/{conversation_id}/accept", response_model=ReplySession)
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
