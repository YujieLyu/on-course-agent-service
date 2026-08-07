from openai import AsyncOpenAI

from src.models import IntentClassification
from src.workflow.llm import load_system_prompt, request_structured_output

async def intent_classify(query: str, client_type: str, model:str) -> IntentClassification:
    system_prompt = load_system_prompt("intent_classifier")
    return request_structured_output(
        client_type=client_type,
        model=model,
        instructions=system_prompt,
        input=query,
        text_format= IntentClassification
    )