from pathlib import Path
from string import Template
from typing import TypeVar

from openai import AsyncOpenAI
from openai.types.responses.parsed_response import ParsedResponse
from openai.types.responses.response_input_param import ResponseInputParam
from pydantic import BaseModel

from src.config import load_openai_settings
from src.logger import get_logger, log_operation

StructuredOutputT = TypeVar("StructuredOutputT", bound=BaseModel)
PROMPT_ROOT = Path(__file__).resolve().parent.parent / "prompts"
logger = get_logger(__name__)

def load_system_prompt(agent_name: str) -> Template:
    prompt_path = PROMPT_ROOT / agent_name / "system.md"
    return Template(prompt_path.read_text())

def create_openai_client() -> AsyncOpenAI:
    settings = load_openai_settings()
    return AsyncOpenAI(
        base_url=settings["endpoint"],
        api_key=settings["key"],
    )

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
    model: str,
    instructions: str,
    input: str | ResponseInputParam,
    output_type: type[StructuredOutputT],
    request_name: str = "LLM request",
) -> StructuredOutputT:
    """Call the Responses API and return a validated Pydantic model."""
    client = create_openai_client()
    request = dict(
        model=model,
        instructions=instructions,
        input=input,
        text_format=output_type,
        store=False,
    )
    with log_operation(logger, "llm.response", model=model, request_name=request_name) as event:
        response = await client.responses.parse(**request)
        result = parse_structured_response(response, output_type, request_name=request_name)
        usage = getattr(response, "usage", None)
        if usage is not None:
            for field in ("input_tokens", "output_tokens", "total_tokens"):
                value = getattr(usage, field, None)
                if isinstance(value, int):
                    event[field] = value
        return result
