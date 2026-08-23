from pathlib import Path
from string import Template
from typing import TypeVar
from uuid import uuid4

from openai import AsyncOpenAI, NotFoundError
from openai.types.responses.parsed_response import ParsedResponse
from openai.types.responses.response_input_param import ResponseInputParam
from pydantic import BaseModel

from src.config import load_openai_settings
from src.logger import get_logger
from src.models import ClientType

StructuredOutputT = TypeVar("StructuredOutputT", bound=BaseModel)
PROMPT_ROOT = Path(__file__).resolve().parent.parent / "prompts"
LOCAL_CONVERSATION_PREFIX = "local_"
logger = get_logger(__name__)

def load_system_prompt(agent_name: str) -> Template:
    prompt_path = PROMPT_ROOT / agent_name / "system.md"
    return Template(prompt_path.read_text())

def create_llm_client(client_type: ClientType) -> AsyncOpenAI:
    match client_type:
        case ClientType.OPENAI:
            return create_openai_client()
        case ClientType.ANTHROPIC:
            return create_anthropic_client()
        case _:
            return create_openai_client()

def create_openai_client() -> AsyncOpenAI:
    settings = load_openai_settings()
    return AsyncOpenAI(
        base_url=settings["endpoint"],
        api_key=settings["key"],
    )

def create_anthropic_client():
    ...

def parse_structured_response(
    response: ParsedResponse[StructuredOutputT],
    output_type: type[StructuredOutputT],
    *,
    request_name: str = "LLM request",
) -> StructuredOutputT:
    """Return the parsed model output or raise a descriptive response error."""
    for output in response.output:
        if output.type != "message":
            continue

        for item in output.content:
            if item.type == "refusal":
                raise ValueError(f"{request_name} refused: {item.refusal}")

    parsed = response.output_parsed
    if not isinstance(parsed, output_type):
        response_text = response.output_text.strip()
        detail = f": {response_text}" if response_text else ""
        raise ValueError(f"{request_name} returned no valid {output_type.__name__}{detail}")

    return parsed

async def request_structured_output(
    *,
    client_type: ClientType,
    model: str,
    instructions: str,
    input: str | ResponseInputParam,
    output_type: type[StructuredOutputT],
    request_name: str = "LLM request",
    conversation_id: str | None = None,
) -> StructuredOutputT:
    """Call the Responses API and return a validated Pydantic model."""
    client = create_llm_client(client_type)
    request = dict(
        model=model,
        instructions=instructions,
        input=input,
        text_format=output_type,
    )
    if conversation_id is not None:
        request["conversation"] = conversation_id
    response = await client.responses.parse(**request)
    return parse_structured_response(
        response,
        output_type,
        request_name=request_name,
    )


async def create_conversation() -> str:
    try:
        conversation = await create_openai_client().conversations.create()
        return conversation.id
    except NotFoundError:
        conversation_id = f"{LOCAL_CONVERSATION_PREFIX}{uuid4().hex}"
        logger.warning(
            "The configured LLM endpoint does not support Conversations; "
            "using process-local session context"
        )
        return conversation_id


def supports_provider_conversation(conversation_id: str | None) -> bool:
    return bool(
        conversation_id
        and not conversation_id.startswith(LOCAL_CONVERSATION_PREFIX)
    )
