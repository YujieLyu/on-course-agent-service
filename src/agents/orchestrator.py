from src.agents.intent_classifier import intent_classify
from src.config import create_client, get_model_settings
from src.models import ReplyRequest, ReplyResponse, ClientType


orchestrator_settings = get_model_settings("ORCHESTRATOR_MODEL_NAME")
openai_client = create_client(orchestrator_settings)

async def run_orchestration(request: ReplyRequest) -> ReplyResponse:
    # input guardrail
    # intent classifer - agent
    intent_response = intent_classify(request.question, ClientType.OPENAI, "INTENT_CLASSIFIER_MODEL_NAME")
    # arrange search - agent
    # execute search
    # answer question -agent
    response = await run_answer(request.question)
    # output guardrail
    return response
    ...


async def run_answer(question:str) -> str:
    response = await openai_client.responses.parse(
        model= orchestrator_settings["model"],
        input = question,
        instructions="you are an assistant to answer questions",
        text_format=ReplyResponse
    )
    parsed = response.output_parsed
    if parsed is None:
        raise ValueError("llm returns nothing")
    return parsed
