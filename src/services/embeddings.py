import os

from src.logger import get_logger, log_operation
from src.models import ClientType
from src.workflow.llm import create_llm_client

logger = get_logger(__name__)


async def generate_embedding(text: str, model: str | None = None) -> list[float]:
    client = create_llm_client(ClientType.OPENAI)
    embedding_model = (
        model
        or os.getenv("AZURE_EMBEDDING_MODEL_DEPLOYMENT_NAME")
        or os.getenv("OPENAI_EMBEDDING_MODEL", "text-embedding-3-small")
    )
    with log_operation(logger, "llm.embedding", model=embedding_model) as event:
        response = await client.embeddings.create(model=embedding_model, input=text)
        vector = response.data[0].embedding
        event["dimensions"] = len(vector)
        return vector
