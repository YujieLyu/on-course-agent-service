import asyncio
import os
import re
from functools import cache
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse
from urllib.request import Request, urlopen

from azure.search.documents.models import VectorizedQuery

from src.config import load_ai_search_settings, load_web_search_settings
from src.models import ClientType, SearchResult, SearchResults, SearchSource
from src.workflow.llm import create_llm_client, create_search_client


async def web_search(query: str) -> SearchResults:
    web_search_settings = _get_web_search_settings()
    query_embedding = await _generate_embedding(query)
    chunks: list[tuple[float, SearchResult]] = []

    for url in web_search_settings["public_website_urls"]:
        try:
            html = await asyncio.to_thread(_fetch_webpage, url)
            title = _extract_title(html) or urlparse(url).netloc or url
            text = _extract_visible_text(html)

            for chunk_text in _split_into_chunks(text):
                chunk_embedding = await _generate_embedding(chunk_text)
                score = _cosine_similarity(query_embedding, chunk_embedding)
                chunks.append(
                    (
                        score,
                        SearchResult(
                            source=SearchSource.UNIVERSITY_POLICIES,
                            content=chunk_text,
                            title=title,
                            source_url=url,
                            excerpt=chunk_text[:220],
                        ),
                    )
                )
        except (HTTPError, URLError, TimeoutError, ValueError):
            continue

    chunks.sort(key=lambda item: item[0], reverse=True)
    results = [result for _, result in chunks[: web_search_settings["top_k"]]]
    return SearchResults(query=query, results=results)


async def canvas_search(query: str) -> SearchResults:
    return SearchResults(query=query, results=[])


async def history_qna_search(query: str) -> SearchResults:
    return SearchResults(query=query, results=[])


async def azure_search_hybrid_search(query: str) -> SearchResults:
    ai_search_settings = _get_ai_search_settings()
    search_client = create_search_client(ai_search_settings)
    query_vector = await _generate_embedding(query)
    vector_query = VectorizedQuery(
        vector=query_vector,
        k_nearest_neighbors=ai_search_settings["top_k"],
        fields=ai_search_settings["vector_field"],
    )
    search_kwargs: dict[str, Any] = {
        "search_text": query,
        "vector_queries": [vector_query],
        "search_fields": ai_search_settings["keyword_fields"],
        "search_mode": "all",
        "top": ai_search_settings["top_k"],
        "select": [ai_search_settings["text_field"], ai_search_settings["source_field"]],
    }
    semantic_config_name = ai_search_settings["semantic_config_name"]
    if semantic_config_name:
        search_kwargs["query_type"] = "semantic"
        search_kwargs["semantic_config_name"] = semantic_config_name
    else:
        search_kwargs["query_type"] = "simple"

    raw_results = await asyncio.to_thread(_run_search, search_client, search_kwargs)
    mapped_results = []
    for item in raw_results:
        source_value = item.get(ai_search_settings["source_field"], "")
        try:
            source = SearchSource(source_value)
        except ValueError:
            source = SearchSource.COURSE_MATERIALS

        mapped_results.append(
            SearchResult(
                source=source,
                content=item.get(ai_search_settings["text_field"], "") or "",
                title=item.get("title"),
                source_url=item.get("source_url"),
                excerpt=item.get("excerpt"),
            )
        )

    return SearchResults(query=query, results=mapped_results)


@cache
def _get_ai_search_settings() -> dict[str, Any]:
    return load_ai_search_settings()


@cache
def _get_web_search_settings() -> dict[str, Any]:
    return load_web_search_settings()


def _run_search(search_client: Any, search_kwargs: dict[str, Any]) -> list[Any]:
    return list(search_client.search(**search_kwargs))


async def _generate_embedding(query: str, model: str | None = None) -> list[float]:
    client = create_llm_client(ClientType.OPENAI)
    embedding_model = model or os.getenv("AZURE_EMBEDDING_MODEL_DEPLOYMENT_NAME") or os.getenv(
        "OPENAI_EMBEDDING_MODEL", "text-embedding-3-small"
    )
    response = await client.embeddings.create(model=embedding_model, input=query)
    return response.data[0].embedding


def _fetch_webpage(url: str) -> str:
    request = Request(
        url,
        headers={
            "User-Agent": "Mozilla/5.0 (compatible; on-course-agent/1.0)",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        },
    )
    with urlopen(request, timeout=10) as response:
        charset = response.headers.get_content_charset() or "utf-8"
        return response.read().decode(charset, errors="ignore")


def _extract_title(html: str) -> str | None:
    match = re.search(r"<title[^>]*>(.*?)</title>", html, flags=re.IGNORECASE | re.DOTALL)
    if not match:
        return None
    return _normalize_whitespace(_strip_html_tags(match.group(1))) or None


def _extract_visible_text(html: str) -> str:
    html = re.sub(r"(?is)<(script|style|noscript).*?>.*?</\1>", " ", html)
    text = _strip_html_tags(html)
    return _normalize_whitespace(text)


def _split_into_chunks(text: str, max_chars: int = 700, overlap: int = 120) -> list[str]:
    if not text:
        return []

    paragraphs = [part.strip() for part in re.split(r"\n{2,}|(?<=[.!?])\s+", text) if part.strip()]
    chunks: list[str] = []
    current = ""

    for paragraph in paragraphs:
        if len(paragraph) > max_chars:
            if current:
                chunks.append(current)
                current = ""
            start = 0
            while start < len(paragraph):
                chunk = paragraph[start : start + max_chars]
                chunks.append(_normalize_whitespace(chunk))
                start += max_chars - overlap
            continue

        if not current:
            current = paragraph
            continue

        if len(current) + 1 + len(paragraph) <= max_chars:
            current = f"{current} {paragraph}"
        else:
            chunks.append(current)
            current = paragraph

    if current:
        chunks.append(current)

    return [chunk for chunk in (_normalize_whitespace(chunk) for chunk in chunks) if chunk]


def _strip_html_tags(text: str) -> str:
    return re.sub(r"<[^>]+>", " ", text)


def _normalize_whitespace(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


def _cosine_similarity(left: list[float], right: list[float]) -> float:
    if not left or not right or len(left) != len(right):
        return 0.0

    dot = sum(a * b for a, b in zip(left, right))
    left_norm = sum(a * a for a in left) ** 0.5
    right_norm = sum(b * b for b in right) ** 0.5
    if not left_norm or not right_norm:
        return 0.0
    return dot / (left_norm * right_norm)