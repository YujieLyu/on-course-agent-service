import os

from src.models import ClientType
from src.workflow.llm import create_llm_client


async def generate_embedding(text: str, model: str | None = None) -> list[float]:
    client = create_llm_client(ClientType.OPENAI)
    embedding_model = (
        model
        or os.getenv("AZURE_EMBEDDING_MODEL_DEPLOYMENT_NAME")
        or os.getenv("OPENAI_EMBEDDING_MODEL", "text-embedding-3-small")
    )
    response = await client.embeddings.create(model=embedding_model, input=text)
    return response.data[0].embedding
