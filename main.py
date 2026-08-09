
from fastapi import FastAPI, Response, status

from src.agents.orchestrator import run_orchestration
from src.logger import get_logger
from src.models import ReplyRequest, ReplyResponse

app = FastAPI()
logger = get_logger(__name__)

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
