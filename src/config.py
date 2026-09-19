import os
from pathlib import Path
from string import Template

from dotenv import load_dotenv
from openai import AsyncOpenAI
from src.logger import get_logger
from src.domain.retention import SESSION_RETENTION

logger = get_logger(__name__)

load_dotenv()

def load_openai_settings() -> dict:
    endpoint = os.getenv("OPENAI_ENDPOINT")
    api_key=os.getenv("OPENAI_API_KEY")
    return {
        "endpoint": endpoint,
        "key": api_key
    }

def get_model_name(model_name:str) -> str:
    return os.getenv(model_name)

def load_ai_search_settings() -> dict:
    try:
        return {
            "endpoint": os.getenv("AZURE_SEARCH_ENDPOINT"),
            "api_key": os.getenv("AZURE_SEARCH_KEY"),
            "index_name": os.getenv("AZURE_SEARCH_INDEX_NAME"),
            "vector_field": os.getenv("AZURE_SEARCH_VECTOR_FIELD"),
            "text_field": os.getenv("AZURE_SEARCH_TEXT_FIELD"),
            "source_field": os.getenv("AZURE_SEARCH_SOURCE_FIELD"),
            "keyword_fields": os.environ["AZURE_SEARCH_KEYWORD_FIELDS"].split(","),
            "top_k": int(os.environ["AZURE_SEARCH_TOP_K"]),
            "semantic_config_name": os.environ["AZURE_SEARCH_SEMANTIC_CONFIG_NAME"],
        }
    except Exception as e:
        logger.error("Failed to load search configuration", exc_info=e)
        raise

def load_public_website_search_settings() -> dict:
    try:
        return {
            "public_website_urls": [
                url.strip()
                for url in os.environ.get("PUBLIC_WEBSITE_URLS", "").split(",")
                if url.strip()
            ],
            "top_k": int(os.environ.get("WEB_SEARCH_TOP_K", "3")),
        }
    except Exception as e:
        logger.error("Failed to load web search configuration", exc_info=e)
        raise

def load_cosmos_qna_settings() -> dict:
    try:
        return {
            "endpoint": os.environ["AZURE_COSMOS_ENDPOINT"],
            "key": os.environ["AZURE_COSMOS_KEY"],
            "database_name": os.environ["AZURE_COSMOS_DATABASE_NAME"],
            "container_name": os.environ.get(
                "AZURE_COSMOS_QA_MEMORY_CONTAINER", "qa_memory"
            ),
            "top_k": int(os.environ.get("AZURE_COSMOS_QNA_TOP_K", "3")),
            "embedding_dimensions": int(
                os.environ.get("AZURE_COSMOS_EMBEDDING_DIMENSIONS", "3072")
            ),
        }
    except Exception as e:
        logger.error("Failed to load Cosmos Q&A configuration", exc_info=e)
        raise

def get_log_format() -> str:
    return os.getenv(
        "LOG_FORMAT",
        "%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    )


REPLY_SESSION_RETENTION_SECONDS = int(SESSION_RETENTION.total_seconds())


def load_cosmos_session_settings() -> dict:
    container_name = os.environ.get("AZURE_COSMOS_REPLY_SESSIONS_CONTAINER", "reply_sessions")
    if container_name == os.environ.get("AZURE_COSMOS_QA_MEMORY_CONTAINER", "qa_memory"):
        raise ValueError("Reply sessions and historical Q&A must use separate containers")
    return {
        "endpoint": os.environ["AZURE_COSMOS_ENDPOINT"],
        "key": os.environ["AZURE_COSMOS_KEY"],
        "database_name": os.environ["AZURE_COSMOS_DATABASE_NAME"],
        "container_name": container_name,
    }
