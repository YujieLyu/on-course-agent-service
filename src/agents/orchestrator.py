import asyncio

from src.agents.intent_classifier import intent_classify
from src.agents.reply_agent import run_answer
from src.config import get_model_name
from src.models import ReplyRequest, ReplyResponse, ClientType, SearchResult
from src.tools.search import search


reply_model_name = get_model_name("REPLY_MODEL_NAME")
intent_classifier_model_name = get_model_name("INTENT_CLASSIFIER_MODEL_NAME")

async def run_orchestration(request: ReplyRequest) -> ReplyResponse:
    # input guardrail
    # intent classifer - agent
    intent_response = await intent_classify(
        request.question,
        ClientType.OPENAI,
        intent_classifier_model_name,
        request.chat_history,
    )
    # arrange search - agent
    search_query = intent_response.search_query or request.question

    search_results: list[SearchResult] = []
    search_responses = await asyncio.gather(
        *(search(search_query, search_source) for search_source in intent_response.search_sources)
    )
    for search_response in search_responses:
        search_results.extend(search_response.results)
    # 
    # answer question -agent
    response = await run_answer(
        request.question,
        ClientType.OPENAI,
        reply_model_name,
        request.chat_history,
        search_results,
    )
    # output guardrail
    return response

