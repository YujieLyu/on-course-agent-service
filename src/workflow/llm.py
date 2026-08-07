from pathlib import Path
from string import Template
from typing import TypeVar

from openai import AsyncOpenAI, OpenAI
from openai.types.responses.parsed_response import ParsedResponse
from openai.types.responses.response_input_param import ResponseInputParam
from azure.core.credentials import AzureKeyCredential
from azure.search.documents import SearchClient
from pydantic import BaseModel

from src.config import load_openai_settings
from src.models import ClientType

StructuredOutputT = TypeVar("StructuredOutputT", bound=BaseModel)
PROMPT_ROOT = Path(__file__).resolve().parent / "prompts"

def load_system_prompt(agent_name: str) -> Template:
    prompt_path = PROMPT_ROOT / agent_name / "system.md"
    return Template(prompt_path.read_text())

def create_search_client(search_settings:dict)-> SearchClient:
    return SearchClient(
            endpoint=search_settings["endpoint"],
            index_name=search_settings["index_name"],
            credential=AzureKeyCredential(search_settings["api_key"]),
        )

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


def create_sync_openai_client() -> OpenAI:
    settings = load_openai_settings()
    return OpenAI(
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

def request_structured_output(
    *,
    client_type: ClientType,
    model: str,
    instructions: str,
    input: str | ResponseInputParam,
    output_type: type[StructuredOutputT],
    request_name: str = "LLM request",
) -> StructuredOutputT:
    """Call the Responses API and return a validated Pydantic model."""
    client = create_llm_client(client_type)
    response = client.responses.parse(
        model=model,
        instructions=instructions,
        input=input,
        text_format=output_type,
    )
    return parse_structured_response(
        response,
        output_type,
        request_name=request_name,
    )