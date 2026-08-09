from src.models import ChatMessage, IntentClassification
from src.workflow.llm import load_system_prompt, request_structured_output

async def intent_classify(
    query: str,
    client_type: str,
    model: str,
    chat_history: list[ChatMessage] | None = None,
    session_context: str | None = None,
) -> IntentClassification:
    system_prompt = load_system_prompt("intent_classifier")
    instructions = system_prompt.substitute(
        conversation_context=_format_conversation_context(chat_history or []),
        session_context=session_context or "No active reply session.",
    )
    return await request_structured_output(
        client_type=client_type,
        model=model,
        instructions=instructions,
        input=query,
        output_type=IntentClassification,
        request_name="Intent classifier",
    )

def _format_conversation_context(chat_history: list[ChatMessage]) -> str:
    if not chat_history:
        return "No prior conversation."

    recent_messages = chat_history[-6:]
    return "\n".join(
        f"{message.role}: {message.content}" for message in recent_messages
    )
