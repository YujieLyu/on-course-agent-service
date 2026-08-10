from src.models import ChatMessage


def format_conversation_context(chat_history: list[ChatMessage]) -> str:
    if not chat_history:
        return "No prior conversation."

    recent_messages = chat_history[-6:]
    return "\n".join(
        f"{message.role}: {message.content}" for message in recent_messages
    )
