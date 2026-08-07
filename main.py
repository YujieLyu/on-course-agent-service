
from fastapi import FastAPI

from src.models import ReplyRequest, ReplyResponse
from src.agents.orchestrator import run_orchestration

app = FastAPI()

@app.get("/health")
def check_health() -> str:
    return "running well"

@app.post("/reply")
async def generate_reply(request: ReplyRequest) -> ReplyResponse:
    response = await run_orchestration(request)
    return response